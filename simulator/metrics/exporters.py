from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

from simulator.config.models import ExperimentConfig
from simulator.core.models import LinkState
from simulator.core.models import RuntimeState


def export_experiment_results(
    experiment: ExperimentConfig,
    output_dir: Path,
    run_records: list[dict[str, Any]],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    per_run_summaries = [
        _build_run_summary(experiment, record["repetition_index"], record["runtime"], record["scheduler_debug_state"])
        for record in run_records
    ]
    aggregate_summary = _build_aggregate_summary(experiment, per_run_summaries)
    link_load_rows = [
        row
        for record in run_records
        for row in _build_link_load_rows(record["repetition_index"], record["runtime"])
    ]
    flow_rows = [
        row
        for record in run_records
        for row in _build_flow_rows(record["repetition_index"], record["runtime"])
    ]
    scheduler_debug_payload = {
        "experiment_name": experiment.meta.name,
        "scheduler_type": experiment.scheduler.type,
        "repetitions": [
            {
                "repetition_index": record["repetition_index"],
                "scheduler_debug_state": record["scheduler_debug_state"],
                "schedule_history": list(record["runtime"].metadata.get("schedule_history", [])),
            }
            for record in run_records
        ],
    }
    summary_payload = {
        "experiment_name": experiment.meta.name,
        "scheduler_type": experiment.scheduler.type,
        "aggregate_metrics": aggregate_summary,
        "repetitions": per_run_summaries,
    }

    exported_files: dict[str, str] = {}
    if experiment.metrics.export_json:
        summary_path = output_dir / "summary.json"
        scheduler_debug_path = output_dir / "scheduler_debug.json"
        link_trace_path = output_dir / "link_load_trace.json"
        summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        scheduler_debug_path.write_text(json.dumps(scheduler_debug_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        link_trace_path.write_text(json.dumps(link_load_rows, indent=2, ensure_ascii=False), encoding="utf-8")
        exported_files["summary_json"] = str(summary_path)
        exported_files["scheduler_debug_json"] = str(scheduler_debug_path)
        exported_files["link_load_trace_json"] = str(link_trace_path)

    if experiment.metrics.export_csv:
        summary_csv_path = output_dir / "summary.csv"
        link_csv_path = output_dir / "link_load_trace.csv"
        _write_csv(summary_csv_path, per_run_summaries)
        _write_csv(link_csv_path, link_load_rows)
        exported_files["summary_csv"] = str(summary_csv_path)
        exported_files["link_load_trace_csv"] = str(link_csv_path)

    if experiment.metrics.export_trace:
        flow_trace_path = output_dir / "flow_trace.csv"
        schedule_history_path = output_dir / "schedule_history.json"
        _write_csv(flow_trace_path, flow_rows)
        schedule_history_path.write_text(
            json.dumps(
                [
                    {
                        "repetition_index": record["repetition_index"],
                        "schedule_history": list(record["runtime"].metadata.get("schedule_history", [])),
                    }
                    for record in run_records
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        exported_files["flow_trace_csv"] = str(flow_trace_path)
        exported_files["schedule_history_json"] = str(schedule_history_path)

    return exported_files


def _build_run_summary(
    experiment: ExperimentConfig,
    repetition_index: int,
    runtime: RuntimeState,
    scheduler_debug_state: dict[str, Any],
) -> dict[str, Any]:
    schedule_history = list(runtime.metadata.get("schedule_history", []))
    flow_states = list(runtime.flow_states.values())
    link_states = list(runtime.link_states.values())
    link_utilizations = [_time_weighted_average_utilization(link_state) for link_state in link_states]
    common_metrics = {
        "repetition_index": repetition_index,
        "experiment_name": experiment.meta.name,
        "scheduler_type": experiment.scheduler.type,
        "completion_time_ms": runtime.now_ms,
        "total_job_count": len(runtime.active_jobs),
        "completed_job_count": len(runtime.completed_job_ids),
        "total_flow_count": len(flow_states),
        "completed_flow_count": len(runtime.completed_flow_ids),
        "schedule_invocation_count": len(schedule_history),
        "epoch_action_count": sum(int(item.get("epoch_action_count", 0)) for item in schedule_history),
        "total_transmitted_mb": sum(link_state.transmitted_mb for link_state in link_states),
        "average_link_utilization": mean(link_utilizations) if link_utilizations else 0.0,
        "max_link_utilization": max(link_utilizations, default=0.0),
        "active_link_count": sum(1 for link_state in link_states if link_state.transmitted_mb > 1e-9),
    }
    summary = dict(common_metrics)
    if experiment.scheduler.type == "crux":
        summary.update(_build_crux_metrics(scheduler_debug_state))
    if experiment.scheduler.type == "teccl":
        summary.update(_build_teccl_metrics(scheduler_debug_state, schedule_history))
    return summary


def _build_crux_metrics(scheduler_debug_state: dict[str, Any]) -> dict[str, Any]:
    observed_comm_times = list((scheduler_debug_state.get("observed_comm_time_ms") or {}).values())
    intensity_scores = list((scheduler_debug_state.get("last_intensity_scores") or {}).values())
    priority_assignments = scheduler_debug_state.get("last_priority_assignments") or {}
    return {
        "crux_avg_observed_comm_time_ms": mean(observed_comm_times) if observed_comm_times else 0.0,
        "crux_avg_intensity_score": mean(intensity_scores) if intensity_scores else 0.0,
        "crux_path_assignment_count": len(scheduler_debug_state.get("last_path_assignments") or {}),
        "crux_priority_level_count": len(set(priority_assignments.values())),
    }


def _build_teccl_metrics(
    scheduler_debug_state: dict[str, Any],
    schedule_history: list[dict[str, Any]],
) -> dict[str, Any]:
    strategy = scheduler_debug_state.get("strategy") or {}
    job_states = scheduler_debug_state.get("job_states") or {}
    replica_count = sum(len(job_state.get("chunk_replicas") or {}) for job_state in job_states.values())
    completed_replica_count = sum(len(job_state.get("completed_replica_ids") or []) for job_state in job_states.values())
    return {
        "teccl_solver_backend": strategy.get("solver_backend", "unknown"),
        "teccl_epoch_size_ms": strategy.get("epoch_size_ms", 0.0),
        "teccl_solver_report_count": len(scheduler_debug_state.get("solver_reports") or {}),
        "teccl_total_epoch_action_count": sum(int(item.get("epoch_action_count", 0)) for item in schedule_history),
        "teccl_replica_count": replica_count,
        "teccl_completed_replica_count": completed_replica_count,
    }


def _build_aggregate_summary(experiment: ExperimentConfig, per_run_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {
        "experiment_name": experiment.meta.name,
        "scheduler_type": experiment.scheduler.type,
        "repetition_count": len(per_run_summaries),
    }
    if not per_run_summaries:
        return aggregate

    numeric_keys = {
        key
        for summary in per_run_summaries
        for key, value in summary.items()
        if isinstance(value, int | float) and key != "repetition_index"
    }
    for key in sorted(numeric_keys):
        values = [float(summary[key]) for summary in per_run_summaries if isinstance(summary.get(key), int | float)]
        if not values:
            continue
        aggregate[f"avg_{key}"] = mean(values)
        aggregate[f"max_{key}"] = max(values)
        aggregate[f"min_{key}"] = min(values)

    non_numeric_keys = {key for key in per_run_summaries[0] if key not in numeric_keys and key != "repetition_index"}
    for key in sorted(non_numeric_keys):
        aggregate[key] = per_run_summaries[0][key]
    return aggregate


def _build_link_load_rows(repetition_index: int, runtime: RuntimeState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for link_id, link_state in sorted(runtime.link_states.items()):
        for snapshot in link_state.utilization_history:
            rows.append(
                {
                    "repetition_index": repetition_index,
                    "link_id": link_id,
                    "time_ms": snapshot["time_ms"],
                    "utilization": snapshot["utilization"],
                    "active_flow_count": snapshot["active_flow_count"],
                    "queue_backlog_mb": snapshot["queue_backlog_mb"],
                    "transmitted_mb": snapshot["transmitted_mb"],
                    "reason": snapshot["reason"],
                }
            )
    return rows


def _build_flow_rows(repetition_index: int, runtime: RuntimeState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for flow_id, flow in sorted(runtime.flow_states.items()):
        rows.append(
            {
                "repetition_index": repetition_index,
                "flow_id": flow_id,
                "owner_job_id": flow.owner_job_id,
                "status": flow.status,
                "source_node": flow.source_node or "",
                "destination_node": flow.destination_node or "",
                "start_time_ms": flow.start_time_ms if flow.start_time_ms is not None else "",
                "end_time_ms": flow.end_time_ms if flow.end_time_ms is not None else "",
                "total_size_mb": flow.total_size_mb,
                "remaining_size_mb": flow.remaining_size_mb,
                "assigned_bandwidth_gbps": flow.assigned_bandwidth_gbps,
                "path": "->".join(flow.path),
                "scheduler": flow.metadata.get("scheduler", ""),
                "chunk_id": flow.chunk_id or "",
                "demand_id": flow.demand_id or "",
            }
        )
    return rows


def _time_weighted_average_utilization(link_state: LinkState) -> float:
    history = link_state.utilization_history
    if len(history) < 2:
        return history[0]["utilization"] if history else 0.0

    total_duration = 0.0
    weighted_utilization = 0.0
    for current, nxt in zip(history, history[1:]):
        duration = max(0.0, float(nxt["time_ms"]) - float(current["time_ms"]))
        if duration <= 0.0:
            continue
        total_duration += duration
        weighted_utilization += float(current["utilization"]) * duration
    if total_duration <= 0.0:
        return float(history[-1]["utilization"])
    return weighted_utilization / total_duration


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)