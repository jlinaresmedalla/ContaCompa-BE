from collections.abc import Callable

from contacompa.config import Settings
from contacompa.infrastructure.providers.base import Provider

ProviderFactory = Callable[[Settings], Provider]
_FACTORIES: dict[str, ProviderFactory] = {}


def register(name: str, factory: ProviderFactory) -> None:
    if name in _FACTORIES:
        raise ValueError(f"provider '{name}' is already registered")
    _FACTORIES[name] = factory


def get_provider(name: str, settings: Settings) -> Provider:
    try:
        factory = _FACTORIES[name]
    except KeyError as exc:
        raise KeyError(f"unknown provider '{name}'; registered: {sorted(_FACTORIES)}") from exc
    return factory(settings)


def registered() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def _reset_for_tests() -> None:
    _FACTORIES.clear()
