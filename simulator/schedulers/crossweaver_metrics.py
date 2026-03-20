from __future__ import annotations

from typing import Any

from simulator.core.models import RuntimeState


def build_crossweaver_run_metrics(
    runtime: RuntimeState,
    scheduler_debug_state: dict[str, Any],
) -> dict[str, Any]:
    scheduler_wall_time_ms = float(scheduler_debug_state.get("crossweaver_scheduler_wall_time_ms", 0.0) or 0.0)
    communication_execution_time_ms = _derive_crossweaver_communication_execution_time_ms(runtime)
    end_to_end_time_ms = scheduler_wall_time_ms + communication_execution_time_ms

    return {
        "crossweaver_scheduler_wall_time_ms": scheduler_wall_time_ms,
        "crossweaver_stage1a_time_ms": float(scheduler_debug_state.get("crossweaver_stage1a_time_ms", 0.0) or 0.0),
        "crossweaver_stage1b_time_ms": float(scheduler_debug_state.get("crossweaver_stage1b_time_ms", 0.0) or 0.0),
        "crossweaver_stage2_time_ms": float(scheduler_debug_state.get("crossweaver_stage2_time_ms", 0.0) or 0.0),
        "crossweaver_communication_execution_time_ms": communication_execution_time_ms,
        "crossweaver_end_to_end_time_ms": end_to_end_time_ms,
    }


def _derive_crossweaver_communication_execution_time_ms(runtime: RuntimeState) -> float:
    start_times = [flow.start_time_ms for flow in runtime.flow_states.values() if flow.start_time_ms is not None]
    end_times = [flow.end_time_ms for flow in runtime.flow_states.values() if flow.end_time_ms is not None]
    if not start_times or not end_times:
        return 0.0
    return max(0.0, max(end_times) - min(start_times))
