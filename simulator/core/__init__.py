"""Runtime state models and simulation core helpers."""

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
	from simulator.core.engine import RuntimeEngine

__all__ = ["RuntimeEngine"]


def __getattr__(name: str) -> Any:
	if name == "RuntimeEngine":
		from simulator.core.engine import RuntimeEngine

		return RuntimeEngine
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
