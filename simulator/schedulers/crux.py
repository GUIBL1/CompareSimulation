from __future__ import annotations

from dataclasses import dataclass, field

from simulator.core.models import RuntimeState
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.base import Scheduler
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class CruxScheduler(Scheduler):
    max_priority_levels: int = 8
    candidate_path_limit: int = 8
    intensity_window_iterations: int = 3
    observed_comm_time_ms: dict[str, float] = field(default_factory=dict)

    def on_workload_arrival(self, job: UnifiedJob, runtime_state: RuntimeState) -> None:
        self.observed_comm_time_ms.setdefault(job.job_id, max(job.compute_phase_ms, 1.0))

    def maybe_reschedule(self, runtime_state: RuntimeState) -> bool:
        return bool(runtime_state.active_jobs)

    def compute_schedule(self, runtime_state: RuntimeState) -> ScheduleDecision:
        ranked_jobs = sorted(runtime_state.active_jobs, key=self._intensity_score, reverse=True)
        decision = ScheduleDecision(
            decision_time_ms=runtime_state.now_ms,
            valid_until_ms=runtime_state.now_ms,
            metadata={"scheduler": "crux"},
        )
        for index, job in enumerate(ranked_jobs):
            decision.priority_assignments[job.job_id] = min(index, self.max_priority_levels - 1)
        return decision

    def export_debug_state(self) -> dict[str, float]:
        return dict(self.observed_comm_time_ms)

    def _intensity_score(self, job: UnifiedJob) -> float:
        comm_time = self.observed_comm_time_ms.get(job.job_id, 1.0)
        return job.compute_phase_ms / max(comm_time, 1e-6)
