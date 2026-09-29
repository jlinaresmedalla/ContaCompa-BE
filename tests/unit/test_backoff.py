from datetime import timedelta

import pytest

from contacompa.infrastructure.db.queue import backoff_for


def test_backoff_grows_and_caps() -> None:
    assert backoff_for(1) == timedelta(seconds=5)
    assert backoff_for(2) == timedelta(seconds=10)
    assert backoff_for(3) == timedelta(seconds=20)
    assert backoff_for(20) == timedelta(minutes=10)
    with pytest.raises(ValueError):
        backoff_for(0)
