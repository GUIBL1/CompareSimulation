from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from simulator.core.models import RuntimeState
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class EpochAction:
    epoch_index: int
    chunk_id: str
    source_gpu: str
    current_node: str
    next_node: str
    expected_arrival_epoch: int
    route_fragment: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScheduleDecision:
    decision_time_ms: float
    valid_until_ms: float
    flow_assignments: dict[str, str] = field(default_factory=dict)
    path_assignments: dict[str, list[str]] = field(default_factory=dict)
    priority_assignments: dict[str, int] = field(default_factory=dict)
    epoch_actions: list[EpochAction] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Scheduler(ABC):
    @abstractmethod
    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        raise NotImplementedError

    @abstractmethod
    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        raise NotImplementedError

    @abstractmethod
    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        raise NotImplementedError

    @abstractmethod
    def export_debug_state(self) -> dict[str, Any]:
        raise NotImplementedError
