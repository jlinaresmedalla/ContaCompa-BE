"""Worker process: claims jobs, runs the extraction pipeline, records results.

Run with `python -m contacompa.entrypoints.worker`. Concurrency is `WORKER_CONCURRENCY` claim loops
sharing one engine; a reaper task requeues jobs whose lease expired. One circuit breaker for the
model provider (ADR 0021) is shared by all loops: while it is open nothing is claimed, and a job
that fails on a provider outage goes back to the queue without spending an attempt."""

import asyncio
import os
import signal
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import contacompa.infrastructure.providers.gemini  # noqa: F401 — registers the provider
from contacompa.application.pipeline import extract
from contacompa.application.pipeline.extract import ExtractionOutcome
from contacompa.application.services.processing import record_success
from contacompa.config import Settings, get_settings
from contacompa.domain.breaker import NO_PERMIT, CircuitBreaker, Permit
from contacompa.domain.config import RunConfig
from contacompa.infrastructure.blob import BlobStore, make_blob_store
from contacompa.infrastructure.db import queue
from contacompa.infrastructure.db.engine import make_engine, make_session_factory
from contacompa.infrastructure.db.models import Job
from contacompa.infrastructure.db.repos import (
    get_document,
    reset_provider_status,
    save_provider_status,
)
from contacompa.infrastructure.observability.llm_tracing import configure_langsmith
from contacompa.infrastructure.observability.logging import configure_logging, get_logger
from contacompa.infrastructure.observability.metrics import (
    configure_metrics,
    get_instruments,
    set_queue_stats,
)
from contacompa.infrastructure.observability.tracing import (
    configure_tracing,
    instrument_httpx,
    instrument_sqlalchemy,
)
from contacompa.infrastructure.providers.base import Provider, ProviderError, is_outage
from contacompa.infrastructure.providers.registry import get_provider

_log = get_logger("worker")
IDLE_SLEEP_SECONDS = 1.0
REAP_INTERVAL_SECONDS = 30.0
OUTAGE_RETRY_DELAY = timedelta(seconds=10)  # requeue delay for an outage below the threshold


class Worker:
    def __init__(
        self, settings: Settings, sessions: async_sessionmaker[AsyncSession], blobs: BlobStore
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.blobs = blobs
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._providers: dict[str, Provider] = {}
        self._stop = asyncio.Event()
        self.provider_name = settings.default_provider
        self.breaker = CircuitBreaker(
            threshold=settings.breaker_threshold,
            cooldown=timedelta(seconds=settings.breaker_cooldown_seconds),
            max_cooldown=timedelta(seconds=settings.breaker_max_cooldown_seconds),
            clock=lambda: datetime.now(UTC),
        )
        self._flush_lock = asyncio.Lock()

    def provider(self, name: str) -> Provider:
        if name not in self._providers:
            self._providers[name] = get_provider(name, self.settings)
        return self._providers[name]

    async def run(self) -> None:
        _log.info(
            "worker.started", worker_id=self.worker_id, concurrency=self.settings.worker_concurrency
        )
        async with self.sessions() as session:
            await reset_provider_status(session)  # a fresh process starts with a closed breaker
            await session.commit()
        loops = [
            asyncio.create_task(self._claim_loop(i))
            for i in range(self.settings.worker_concurrency)
        ]
        reaper = asyncio.create_task(self._reaper_loop())
        await self._stop.wait()
        for task in [*loops, reaper]:
            task.cancel()
        await asyncio.gather(*loops, reaper, return_exceptions=True)
        _log.info("worker.stopped", worker_id=self.worker_id)

    def stop(self) -> None:
        self._stop.set()

    async def _claim_loop(self, slot: int) -> None:
        worker_id = f"{self.worker_id}#{slot}"
        while not self._stop.is_set():
            permit: Permit | None = None
            try:
                permit = self.breaker.allow()
                if permit is None:
                    await asyncio.sleep(IDLE_SLEEP_SECONDS)  # paused: claim nothing
                    continue
                await self._flush_breaker()  # persists closed/open -> half-open
                async with self.sessions() as session:
                    job = await queue.claim(session, worker_id)
                    await session.commit()
                if job is None:
                    await asyncio.sleep(IDLE_SLEEP_SECONDS)
                    continue
                await self.process(job, worker_id, permit)
            except Exception as exc:  # a loop must never die from a database or logic error
                _log.error("worker.loop_error", worker_id=worker_id, error=type(exc).__name__)
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
            finally:
                if permit is not None:
                    self.breaker.abandon(permit)  # no-op unless this loop still holds the trial

    async def _flush_breaker(self) -> None:
        """Persist, log and count the breaker's state changes, in order. Never raises: pending
        changes stay queued and are written by the next flush if the database is unavailable."""
        async with self._flush_lock:
            transitions = self.breaker.pending_transitions()
            if not transitions:
                return
            try:
                async with self.sessions() as session:
                    for t in transitions:
                        await save_provider_status(
                            session,
                            provider=self.provider_name,
                            state=t.to_state.value,
                            open_until=t.open_until,
                            consecutive_failures=t.consecutive_failures,
                            reason=t.reason,
                        )
                    await session.commit()
            except Exception as exc:
                _log.error("provider.breaker_persist_failed", error=type(exc).__name__)
                return
            self.breaker.ack_transitions(len(transitions))
            for t in transitions:
                get_instruments().provider_breaker_transitions_total.add(
                    1, {"provider": self.provider_name, "state": t.to_state.value}
                )
                _log.warning(
                    f"provider.breaker_{t.to_state.value}",
                    provider=self.provider_name,
                    from_state=t.from_state.value,
                    consecutive_failures=t.consecutive_failures,
                    open_until=t.open_until.isoformat() if t.open_until else None,
                    reason=t.reason,
                )

    async def _reaper_loop(self) -> None:
        lease = timedelta(seconds=self.settings.worker_lease_seconds)
        while not self._stop.is_set():
            async with self.sessions() as session:
                reaped = await queue.reap_expired(session, lease)
                queued, oldest_age = await queue.stats(session)
                await session.commit()
            set_queue_stats(queued, oldest_age)
            if reaped:
                _log.warning("worker.reaped_expired_leases", count=reaped)
            await asyncio.sleep(REAP_INTERVAL_SECONDS)

    async def process(self, job: Job, worker_id: str, permit: Permit = NO_PERMIT) -> None:
        started = time.perf_counter()
        log = _log.bind(
            job_id=str(job.id), document_id=str(job.document_id), attempt=job.attempts + 1
        )
        try:
            try:
                cfg = RunConfig.model_validate(job.config)
                async with self.sessions() as session:
                    document = await get_document(session, job.document_id)
                if document is None:
                    raise ValueError("document row missing")
                data = await self.blobs.get(document.storage_key)
                outcome = await extract(data, cfg, self.provider(cfg.provider), document.mime_type)
            except Exception as exc:
                await self._on_extraction_failure(job, worker_id, permit, exc, log)
                return
            self.breaker.record_success(permit)  # before any database call
            await self._flush_breaker()
            try:
                await self._record_outcome(job, worker_id, cfg, outcome, log)
            except Exception as exc:
                await self._fail_job(job, worker_id, exc, log)
        finally:
            get_instruments().job_duration_seconds.record(time.perf_counter() - started)

    async def _record_outcome(
        self, job: Job, worker_id: str, cfg: RunConfig, outcome: ExtractionOutcome, log: Any
    ) -> None:
        finished_at = datetime.now(UTC)
        created_at = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=UTC)
        turnaround_ms = int((finished_at - created_at).total_seconds() * 1000)
        recorded = await record_success(
            self.sessions,
            job=job,
            worker_id=worker_id,
            cfg=cfg,
            outcome=outcome,
            turnaround_ms=turnaround_ms,
        )
        if not recorded:
            log.warning("job.lease_lost")
            return
        get_instruments().job_turnaround_seconds.record(turnaround_ms / 1000)
        log.info(
            "job.done",
            doc_type=outcome.doc_type,
            missing=len(outcome.missing),
            cost_usd=str(outcome.cost.billed_usd),
            latency_ms=outcome.latency_ms,
            turnaround_ms=turnaround_ms,
        )

    async def _on_extraction_failure(
        self, job: Job, worker_id: str, permit: Permit, exc: Exception, log: Any
    ) -> None:
        """Breaker bookkeeping first (no I/O), then requeue or fail the job; never raises."""
        if is_outage(exc):
            error = _error_text(exc)
            self.breaker.record_outage(permit, error)
            run_after = self.breaker.open_until or datetime.now(UTC) + OUTAGE_RETRY_DELAY
            await self._flush_breaker()
            await self._release_job(job, worker_id, exc, error, run_after, log)
        else:
            self.breaker.record_other_failure(permit)
            await self._flush_breaker()
            await self._fail_job(job, worker_id, exc, log)

    async def _release_job(
        self,
        job: Job,
        worker_id: str,
        exc: Exception,
        error: str,
        run_after: datetime,
        log: Any,
    ) -> None:
        reason = type(exc).__name__
        get_instruments().jobs_released_total.add(1, {"reason": reason})
        try:
            async with self.sessions() as session:
                released = await queue.release(session, job.id, run_after, error, worker_id)
                await session.commit()
        except Exception as db_exc:  # the lease expires and the reaper requeues the job
            log.error("job.release_failed", reason=reason, error=type(db_exc).__name__)
            return
        if released:
            log.warning("job.released", reason=reason, run_after=run_after.isoformat())
        else:
            log.warning("job.lease_lost", reason=reason)

    async def _fail_job(self, job: Job, worker_id: str, exc: Exception, log: Any) -> None:
        reason = type(exc).__name__
        get_instruments().jobs_failed_total.add(1, {"reason": reason})
        try:
            async with self.sessions() as session:
                status = await queue.fail(session, job.id, _error_text(exc), worker_id)
                await session.commit()
        except Exception as db_exc:  # the lease expires and the reaper requeues the job
            log.error("job.fail_failed", reason=reason, error=type(db_exc).__name__)
            return
        if status is None:
            log.warning("job.lease_lost", reason=reason)
        else:
            log.warning("job.failed", reason=reason, status=status.value)


def _error_text(exc: Exception) -> str:
    reason = type(exc).__name__
    message = str(exc) if isinstance(exc, ProviderError | ValueError | KeyError) else reason
    return f"{reason}: {message}"


async def main() -> None:
    settings = get_settings()
    configure_logging()
    configure_tracing(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
    configure_metrics(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
    instrument_httpx()
    configure_langsmith(settings)
    engine = make_engine(settings.database_url)
    instrument_sqlalchemy(engine)
    worker = Worker(settings, make_session_factory(engine), make_blob_store(settings))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
