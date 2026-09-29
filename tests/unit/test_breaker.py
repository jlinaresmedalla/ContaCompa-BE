"""Provider circuit breaker: state machine with a fake clock, and outage classification."""

from datetime import UTC, datetime, timedelta

import httpx

from contacompa.domain.breaker import NO_PERMIT, BreakerState, CircuitBreaker, Permit
from contacompa.infrastructure.providers.base import (
    InvalidOutputError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitedError,
    is_outage,
)
from contacompa.infrastructure.providers.gemini import map_error

CLOSED, OPEN, HALF_OPEN = BreakerState.CLOSED, BreakerState.OPEN, BreakerState.HALF_OPEN


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make() -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    breaker = CircuitBreaker(
        threshold=3,
        cooldown=timedelta(seconds=60),
        max_cooldown=timedelta(seconds=240),
        clock=clock,
    )
    return breaker, clock


def permit(breaker: CircuitBreaker) -> Permit:
    granted = breaker.allow()
    assert granted is not None
    return granted


def open_it(breaker: CircuitBreaker) -> None:
    for _ in range(3):
        breaker.record_outage(permit(breaker), "down")
    assert breaker.state is OPEN


def start_trial(breaker: CircuitBreaker, clock: FakeClock) -> Permit:
    assert breaker.open_until is not None
    clock.now = breaker.open_until
    trial = permit(breaker)
    assert trial.trial is not None and breaker.state is HALF_OPEN
    return trial


def reopen_after_cooldown(breaker: CircuitBreaker, clock: FakeClock) -> timedelta:
    """Wait out the cool-down, fail the trial, return the new cool-down."""
    breaker.record_outage(start_trial(breaker, clock), "still down")
    assert breaker.open_until is not None
    return breaker.open_until - clock.now


def test_closed_open_half_open_closed() -> None:
    breaker, clock = make()
    assert breaker.state is CLOSED and breaker.allow()

    open_it(breaker)
    assert breaker.open_until == clock.now + timedelta(seconds=60)
    assert breaker.allow() is None

    clock.advance(59)
    assert breaker.allow() is None
    clock.advance(1)
    trial = permit(breaker)
    assert breaker.state is HALF_OPEN

    breaker.record_success(trial)
    assert breaker.state is CLOSED
    assert breaker.open_until is None and breaker.consecutive_failures == 0
    assert [(t.from_state, t.to_state) for t in breaker.pending_transitions()] == [
        (CLOSED, OPEN),
        (OPEN, HALF_OPEN),
        (HALF_OPEN, CLOSED),
    ]


def test_transitions_stay_pending_until_acknowledged() -> None:
    breaker, _ = make()
    open_it(breaker)
    assert len(breaker.pending_transitions()) == 1
    assert len(breaker.pending_transitions()) == 1  # a failed write loses nothing
    breaker.ack_transitions(1)
    assert breaker.pending_transitions() == []


def test_stays_closed_below_threshold() -> None:
    breaker, _ = make()
    breaker.record_outage(permit(breaker), "down")
    breaker.record_outage(permit(breaker), "down")
    assert breaker.state is CLOSED and breaker.allow()
    assert breaker.pending_transitions() == []


def test_cooldown_doubles_caps_and_resets_on_close() -> None:
    breaker, clock = make()
    open_it(breaker)
    assert breaker.open_until == clock.now + timedelta(seconds=60)

    assert reopen_after_cooldown(breaker, clock) == timedelta(seconds=120)
    assert reopen_after_cooldown(breaker, clock) == timedelta(seconds=240)
    assert reopen_after_cooldown(breaker, clock) == timedelta(seconds=240)  # capped

    breaker.record_success(start_trial(breaker, clock))
    open_it(breaker)
    assert breaker.open_until == clock.now + timedelta(seconds=60)  # back to the base


def test_half_open_lets_exactly_one_trial_through() -> None:
    breaker, clock = make()
    open_it(breaker)
    clock.advance(60)

    trial = permit(breaker)
    assert breaker.allow() is None
    assert breaker.allow() is None

    breaker.abandon(trial)  # the trial found no job to run
    assert breaker.state is HALF_OPEN
    assert breaker.allow() is not None


def test_stale_success_does_not_close_a_half_open_breaker() -> None:
    breaker, clock = make()
    stale = permit(breaker)  # a call that started while closed
    open_it(breaker)
    trial = start_trial(breaker, clock)

    breaker.record_success(stale)

    assert breaker.state is HALF_OPEN
    assert breaker.allow() is None  # the trial is still the only call in flight
    breaker.record_success(trial)
    assert breaker.state is CLOSED


def test_stale_outage_does_not_reopen_or_start_a_second_trial() -> None:
    breaker, clock = make()
    stale = permit(breaker)
    open_it(breaker)
    trial = start_trial(breaker, clock)

    breaker.record_outage(stale, "late")

    assert breaker.state is HALF_OPEN
    assert breaker.allow() is None  # still exactly one trial
    breaker.record_success(trial)
    assert breaker.state is CLOSED


def test_stale_results_are_ignored_while_open_and_after_reclosing() -> None:
    breaker, clock = make()
    stale = permit(breaker)
    open_it(breaker)
    until = breaker.open_until
    breaker.record_outage(stale, "late")
    breaker.record_success(stale)
    assert breaker.state is OPEN and breaker.open_until == until

    breaker.record_success(start_trial(breaker, clock))
    breaker.record_outage(stale, "very late")  # from before the breaker opened
    assert breaker.state is CLOSED and breaker.consecutive_failures == 0
    breaker.record_outage(NO_PERMIT, "unknown")
    assert breaker.consecutive_failures == 0


def test_non_outage_failure_resets_the_count() -> None:
    breaker, _ = make()
    breaker.record_outage(permit(breaker), "down")
    breaker.record_outage(permit(breaker), "down")
    breaker.record_other_failure(permit(breaker))
    breaker.record_outage(permit(breaker), "down")
    breaker.record_outage(permit(breaker), "down")
    assert breaker.state is CLOSED

    breaker.record_outage(permit(breaker), "down")
    assert breaker.state is OPEN


def test_non_outage_failure_on_the_trial_ends_it_without_closing() -> None:
    breaker, clock = make()
    open_it(breaker)
    trial = start_trial(breaker, clock)

    breaker.record_other_failure(trial)

    assert breaker.state is HALF_OPEN and breaker.consecutive_failures == 0
    second = permit(breaker)  # a new trial may start
    assert second.trial != trial.trial
    breaker.record_success(trial)  # the finished trial can no longer decide anything
    assert breaker.state is HALF_OPEN
    breaker.record_success(second)
    assert breaker.state is CLOSED


def test_abandon_by_a_finished_trial_does_not_free_the_next_one() -> None:
    breaker, clock = make()
    open_it(breaker)
    first = start_trial(breaker, clock)
    breaker.record_other_failure(first)
    permit(breaker)  # second trial in flight

    breaker.abandon(first)

    assert breaker.allow() is None


def test_outage_classification() -> None:
    assert is_outage(ProviderUnavailableError("503"))
    assert is_outage(RateLimitedError("429"))
    assert is_outage(map_error(TimeoutError()))
    assert is_outage(map_error(httpx.ReadTimeout("slow")))
    assert is_outage(map_error(httpx.ConnectError("refused")))
    assert is_outage(map_error(ConnectionRefusedError()))
    assert not is_outage(map_error(OSError("disk")))
    assert not is_outage(InvalidOutputError("bad json"))
    assert not is_outage(ProviderError("rejected (400)"))
    assert not is_outage(ValueError("document row missing"))
