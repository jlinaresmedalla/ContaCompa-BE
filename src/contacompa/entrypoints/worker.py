"""Worker process: claims jobs, runs the extraction pipeline, records results.

Run with `python -m contacompa.entrypoints.worker`. Concurrency is `WORKER_CONCURRENCY` claim loops
sharing one engine; a reaper task requeues jobs whose lease expired."""

import asyncio
import os
import signal
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import contacompa.infrastructure.providers.gemini  # noqa: F401 — registers the provider
from contacompa.application.pipeline import extract
from contacompa.application.services.processing import record_success
from contacompa.config import Settings, get_settings
from contacompa.domain.config import RunConfig
from contacompa.infrastructure.blob import BlobStore, LocalBlobStore
from contacompa.infrastructure.db import queue
from contacompa.infrastructure.db.engine import make_engine, make_session_factory
from contacompa.infrastructure.db.models import Job
from contacompa.infrastructure.db.repos import get_document
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
from contacompa.infrastructure.providers.base import Provider, ProviderError
from contacompa.infrastructure.providers.registry import get_provider

_log = get_logger("worker")
IDLE_SLEEP_SECONDS = 1.0
REAP_INTERVAL_SECONDS = 30.0


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

    def provider(self, name: str) -> Provider:
        if name not in self._providers:
            self._providers[name] = get_provider(name, self.settings)
        return self._providers[name]

    async def run(self) -> None:
        _log.info(
            "worker.started", worker_id=self.worker_id, concurrency=self.settings.worker_concurrency
        )
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
            async with self.sessions() as session:
                job = await queue.claim(session, worker_id)
                await session.commit()
            if job is None:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
                continue
            await self.process(job, worker_id)

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

    async def process(self, job: Job, worker_id: str) -> None:
        started = time.perf_counter()
        log = _log.bind(
            job_id=str(job.id), document_id=str(job.document_id), attempt=job.attempts + 1
        )
        instruments = get_instruments()
        try:
            cfg = RunConfig.model_validate(job.config)
            async with self.sessions() as session:
                document = await get_document(session, job.document_id)
            if document is None:
                raise ValueError("document row missing")
            data = await self.blobs.get(document.storage_key)
            outcome = await extract(data, cfg, self.provider(cfg.provider), document.mime_type)
            finished_at = datetime.now(UTC)
            created_at = (
                job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=UTC)
            )
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
            instruments.job_turnaround_seconds.record(turnaround_ms / 1000)
            log.info(
                "job.done",
                doc_type=outcome.doc_type,
                missing=len(outcome.missing),
                cost_usd=str(outcome.cost.billed_usd),
                latency_ms=outcome.latency_ms,
                turnaround_ms=turnaround_ms,
            )
        except Exception as exc:
            reason = type(exc).__name__
            message = str(exc) if isinstance(exc, ProviderError | ValueError | KeyError) else reason
            async with self.sessions() as session:
                status = await queue.fail(session, job.id, f"{reason}: {message}", worker_id)
                await session.commit()
            instruments.jobs_failed_total.add(1, {"reason": reason})
            if status is None:
                log.warning("job.lease_lost", reason=reason)
            else:
                log.warning("job.failed", reason=reason, status=status.value)
        finally:
            instruments.job_duration_seconds.record(time.perf_counter() - started)


async def main() -> None:
    settings = get_settings()
    configure_logging()
    configure_tracing(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
    configure_metrics(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
    instrument_httpx()
    configure_langsmith(settings)
    engine = make_engine(settings.database_url)
    instrument_sqlalchemy(engine)
    worker = Worker(settings, make_session_factory(engine), LocalBlobStore(Path(settings.blob_dir)))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
