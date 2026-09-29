"""Circuit breaker for the model provider: a pure state machine with an injected clock.

Closed lets every call through. After `threshold` consecutive outages it opens and refuses calls
for a cool-down (doubling on each reopening, capped). When the cool-down ends the next `allow()`
moves it to half-open and grants exactly one trial call: success closes it, an outage reopens it.
`allow()` returns a `Permit`; only the holder of the trial permit can close or reopen from
half-open, and results of calls that started before the breaker changed state are ignored.
Every state change is queued as a `Transition`; the owner persists and acknowledges them."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


@dataclass(frozen=True)
class Permit:
    """Granted by `allow()`: the breaker epoch it was granted in and, for a trial, the trial id."""

    epoch: int
    trial: int | None = None


NO_PERMIT = Permit(epoch=-1)  # matches nothing: results reported with it never change the state


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class Transition:
    from_state: BreakerState
    to_state: BreakerState
    open_until: datetime | None
    consecutive_failures: int
    reason: str | None


class CircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int,
        cooldown: timedelta,
        max_cooldown: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        self._threshold = threshold
        self._base_cooldown = cooldown
        self._max_cooldown = max_cooldown
        self._clock = clock
        self._next_cooldown = cooldown
        self._epoch = 0  # bumped on every state change
        self._trial: int | None = None  # id of the trial in flight (half-open only)
        self._trial_seq = 0
        self._pending: list[Transition] = []
        self.state = BreakerState.CLOSED
        self.open_until: datetime | None = None
        self.consecutive_failures = 0
        self.reason: str | None = None

    def allow(self) -> Permit | None:
        """A permit for one provider call now, or None while paused. Half-open grants one trial."""
        if self.state is BreakerState.CLOSED:
            return Permit(self._epoch)
        if self.state is BreakerState.OPEN:
            if self.open_until is not None and self._clock() < self.open_until:
                return None
            self.open_until = None
            self._move(BreakerState.HALF_OPEN)
        elif self._trial is not None:
            return None
        self._trial_seq += 1
        self._trial = self._trial_seq
        return Permit(self._epoch, self._trial)

    def abandon(self, permit: Permit) -> None:
        """The trial ended without telling anything about the provider (no job, crash)."""
        if self._holds_trial(permit):
            self._trial = None

    def record_success(self, permit: Permit) -> None:
        if self._holds_trial(permit):
            self._close()
        elif self._is_current(permit):
            self.consecutive_failures = 0

    def record_other_failure(self, permit: Permit) -> None:
        """Any failure that is not an outage: resets the count. A trial ends without closing."""
        if self._holds_trial(permit):
            self.consecutive_failures = 0
            self._trial = None
        elif self._is_current(permit):
            self.consecutive_failures = 0

    def record_outage(self, permit: Permit, reason: str) -> None:
        if self._holds_trial(permit):
            self.consecutive_failures += 1
            self.reason = reason
            self._open()
        elif self._is_current(permit):
            self.consecutive_failures += 1
            self.reason = reason
            if self.consecutive_failures >= self._threshold:
                self._open()

    def pending_transitions(self) -> list[Transition]:
        return list(self._pending)

    def ack_transitions(self, count: int) -> None:
        """Forget the first `count` pending transitions once they are persisted."""
        del self._pending[:count]

    def _holds_trial(self, permit: Permit) -> bool:
        return (
            self.state is BreakerState.HALF_OPEN
            and permit.trial is not None
            and permit.trial == self._trial
        )

    def _is_current(self, permit: Permit) -> bool:
        return (
            self.state is BreakerState.CLOSED
            and permit.trial is None
            and permit.epoch == self._epoch
        )

    def _open(self) -> None:
        self.open_until = self._clock() + self._next_cooldown
        self._next_cooldown = min(self._next_cooldown * 2, self._max_cooldown)
        self._move(BreakerState.OPEN)

    def _close(self) -> None:
        self.consecutive_failures = 0
        self.open_until = None
        self.reason = None
        self._next_cooldown = self._base_cooldown
        self._move(BreakerState.CLOSED)

    def _move(self, to_state: BreakerState) -> None:
        self._pending.append(
            Transition(
                self.state, to_state, self.open_until, self.consecutive_failures, self.reason
            )
        )
        self.state = to_state
        self._epoch += 1
        self._trial = None
