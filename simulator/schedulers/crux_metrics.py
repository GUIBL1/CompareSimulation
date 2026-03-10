from __future__ import annotations

from statistics import mean
from typing import Any

from simulator.core.models import RuntimeState


def build_crux_run_metrics(
    runtime: RuntimeState,
    scheduler_debug_state: dict[str, Any],
) -> dict[str, Any]:
    model_summary = scheduler_debug_state.get("crux_model_summary") or {}
    compression = scheduler_debug_state.get("crux_priority_compression") or {}
    contention_dag = scheduler_debug_state.get("crux_contention_dag") or {}
    dag_metadata = contention_dag.get("metadata") or {}
    model_input = scheduler_debug_state.get("crux_model_input") or {}
    jobs = (model_input.get("jobs") or {}) if isinstance(model_input, dict) else {}

    flow_completion_times = _collect_flow_completion_times(runtime)
    high_priority_flow_completion_time_ms, low_priority_flow_completion_time_ms = _split_high_low_priority_completion_times(runtime)
    scheduler_wall_time_ms = float(scheduler_debug_state.get("crux_scheduler_wall_time_ms", 0.0) or 0.0)
    communication_execution_time_ms = _derive_crux_communication_execution_time_ms(runtime)
    end_to_end_time_ms = scheduler_wall_time_ms + communication_execution_time_ms
    gain_ratio = 0.0
    if high_priority_flow_completion_time_ms > 1e-12:
        gain_ratio = low_priority_flow_completion_time_ms / high_priority_flow_completion_time_ms if low_priority_flow_completion_time_ms > 0.0 else 0.0

    raw_priority_ranks = [
        int((job_payload.get("priority") or {}).get("raw_priority_rank", -1))
        for job_payload in jobs.values()
        if (job_payload.get("priority") or {}).get("raw_priority_rank") is not None
    ]

    return {
        "crux_path_selection_time_ms": float(scheduler_debug_state.get("crux_path_selection_time_ms", 0.0) or 0.0),
        "crux_priority_assignment_time_ms": float(scheduler_debug_state.get("crux_priority_assignment_time_ms", 0.0) or 0.0),
        "crux_priority_compression_time_ms": float(scheduler_debug_state.get("crux_priority_compression_time_ms", 0.0) or 0.0),
        "crux_scheduler_wall_time_ms": scheduler_wall_time_ms,
        "crux_communication_execution_time_ms": communication_execution_time_ms,
        "crux_end_to_end_time_ms": end_to_end_time_ms,
        "job_count": int(model_summary.get("job_count", 0) or 0),
        "flow_count": int(model_summary.get("flow_count", 0) or 0),
        "path_candidate_count": int(model_summary.get("path_candidate_count", 0) or 0),
        "unique_link_count": int(model_summary.get("unique_link_count", 0) or 0),
        "overlapping_link_pair_count": int(dag_metadata.get("overlapping_link_pair_count", 0) or 0),
        "priority_level_count_raw": len({rank for rank in raw_priority_ranks if rank >= 0}),
        "hardware_priority_count": int(model_summary.get("hardware_priority_count", 0) or 0),
        "contention_dag_node_count": int(dag_metadata.get("node_count", 0) or 0),
        "contention_dag_edge_count": int(dag_metadata.get("edge_count", 0) or 0),
        "topological_order_sample_count": int(compression.get("topological_order_sample_count", 0) or 0),
        "average_intensity": float(model_summary.get("average_intensity", 0.0) or 0.0),
        "max_intensity": float(model_summary.get("max_intensity", 0.0) or 0.0),
        "average_priority_score": float(model_summary.get("average_priority_score", 0.0) or 0.0),
        "max_priority_score": float(model_summary.get("max_priority_score", 0.0) or 0.0),
        "total_cut_weight": float(compression.get("total_cut_weight", 0.0) or 0.0),
        "lost_cut_weight": float(compression.get("lost_cut_weight", 0.0) or 0.0),
        "average_high_priority_flow_completion_time_ms": high_priority_flow_completion_time_ms,
        "average_low_priority_flow_completion_time_ms": low_priority_flow_completion_time_ms,
        "priority_execution_gain_ratio": gain_ratio,
        "completed_flow_duration_count": len(flow_completion_times),
        "crux_priority_aware_bandwidth_enabled": bool(runtime.metadata.get("priority_aware_bandwidth_enabled", False)),
    }


def build_crux_scheduler_stats_payload(
    experiment_name: str,
    scheduler_type: str,
    run_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    repetition_stats: list[dict[str, Any]] = []
    for record in run_records:
        runtime = record.get("runtime")
        scheduler_debug_state = record.get("scheduler_debug_state") or {}
        if runtime is None:
            continue
        repetition_stats.append(
            {
                "repetition_index": record["repetition_index"],
                **build_crux_run_metrics(runtime, scheduler_debug_state),
            }
        )

    if not repetition_stats:
        return None

    aggregate_metrics: dict[str, Any] = {
        "experiment_name": experiment_name,
        "scheduler_type": scheduler_type,
        "repetition_count": len(repetition_stats),
    }
    numeric_keys = {
        key
        for item in repetition_stats
        for key, value in item.items()
        if isinstance(value, int | float) and key != "repetition_index"
    }
    for key in sorted(numeric_keys):
        values = [float(item[key]) for item in repetition_stats if isinstance(item.get(key), int | float)]
        if not values:
            continue
        aggregate_metrics[f"avg_{key}"] = mean(values)
        aggregate_metrics[f"max_{key}"] = max(values)
        aggregate_metrics[f"min_{key}"] = min(values)

    return {
        "experiment_name": experiment_name,
        "scheduler_type": scheduler_type,
        "aggregate_metrics": aggregate_metrics,
        "repetitions": repetition_stats,
    }


def _collect_flow_completion_times(runtime: RuntimeState) -> list[float]:
    durations: list[float] = []
    for flow in runtime.flow_states.values():
        if flow.start_time_ms is None or flow.end_time_ms is None:
            continue
        durations.append(max(0.0, flow.end_time_ms - flow.start_time_ms))
    return durations


def _derive_crux_communication_execution_time_ms(runtime: RuntimeState) -> float:
    start_times = [flow.start_time_ms for flow in runtime.flow_states.values() if flow.start_time_ms is not None]
    end_times = [flow.end_time_ms for flow in runtime.flow_states.values() if flow.end_time_ms is not None]
    if not start_times or not end_times:
        return 0.0
    return max(0.0, max(end_times) - min(start_times))


def _split_high_low_priority_completion_times(runtime: RuntimeState) -> tuple[float, float]:
    durations_by_priority: dict[int, list[float]] = {}
    for flow in runtime.flow_states.values():
        if flow.priority is None or flow.start_time_ms is None or flow.end_time_ms is None:
            continue
        durations_by_priority.setdefault(int(flow.priority), []).append(max(0.0, flow.end_time_ms - flow.start_time_ms))

    if not durations_by_priority:
        return 0.0, 0.0
    high_priority = min(durations_by_priority)
    low_priority = max(durations_by_priority)
    high_avg = mean(durations_by_priority.get(high_priority, [0.0])) if durations_by_priority.get(high_priority) else 0.0
    low_avg = mean(durations_by_priority.get(low_priority, [0.0])) if durations_by_priority.get(low_priority) else 0.0
    return high_avg, low_avg