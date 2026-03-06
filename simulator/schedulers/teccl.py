from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

from simulator.core.models import RuntimeState
from simulator.schedulers.base import EpochAction
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.base import Scheduler
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class TECCLStrategy:
    epoch_size_ms: float = 1.0
    solver_backend: str = "small_scale_debug_solver"
    max_solver_time_ms: int = 1000
    allow_gpu_replication: bool = True
    allow_switch_replication: bool = False
    enable_gpu_buffer: bool = True
    enable_switch_buffer: bool = False


@dataclass(slots=True)
class TECCLScheduler(Scheduler):
    strategy: TECCLStrategy = field(default_factory=TECCLStrategy)
    pending_jobs: list[str] = field(default_factory=list)

    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        if job.job_id not in self.pending_jobs:
            self.pending_jobs.append(job.job_id)

    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        if self.strategy.epoch_size_ms <= 0:
            raise ValueError("epoch_size_ms must be positive")
        epoch_position = runtime_state.now_ms / self.strategy.epoch_size_ms
        return abs(epoch_position - round(epoch_position)) < 1e-9

    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        current_epoch = int(ceil(runtime_state.now_ms / self.strategy.epoch_size_ms))
        epoch_actions: list[EpochAction] = []
        for job in runtime_state.active_jobs:
            epoch_actions.extend(self._build_epoch_actions(job, current_epoch))
        return ScheduleDecision(
            decision_time_ms=runtime_state.now_ms,
            valid_until_ms=runtime_state.now_ms + self.strategy.epoch_size_ms,
            epoch_actions=epoch_actions,
            metadata={
                "scheduler": "teccl",
                "solver_backend": self.strategy.solver_backend,
                "allow_gpu_replication": self.strategy.allow_gpu_replication,
                "allow_switch_replication": self.strategy.allow_switch_replication,
                "enable_gpu_buffer": self.strategy.enable_gpu_buffer,
                "enable_switch_buffer": self.strategy.enable_switch_buffer,
            },
        )

    def export_debug_state(self) -> dict[str, object]:
        return {
            "pending_jobs": list(self.pending_jobs),
            "strategy": {
                "epoch_size_ms": self.strategy.epoch_size_ms,
                "solver_backend": self.strategy.solver_backend,
                "allow_gpu_replication": self.strategy.allow_gpu_replication,
                "allow_switch_replication": self.strategy.allow_switch_replication,
                "enable_gpu_buffer": self.strategy.enable_gpu_buffer,
                "enable_switch_buffer": self.strategy.enable_switch_buffer,
            },
        }

    def _build_epoch_actions(self, job: UnifiedJob, current_epoch: int) -> list[EpochAction]:
        actions: list[EpochAction] = []
        if not job.participants:
            return actions
        for demand in job.communication_demands:
            for chunk in demand.chunks:
                for source_gpu in chunk.source_set:
                    for destination in chunk.destination_set:
                        if source_gpu == destination:
                            continue
                        actions.append(
                            EpochAction(
                                epoch_index=current_epoch,
                                chunk_id=chunk.chunk_id,
                                source_gpu=source_gpu,
                                current_node=source_gpu,
                                next_node=destination,
                                expected_arrival_epoch=current_epoch + 1,
                                route_fragment=[source_gpu, destination],
                            )
                        )
        return actions
