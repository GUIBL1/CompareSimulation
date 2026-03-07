from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt


def generate_crux_teccl_comparison_visuals(
    crux_result_dir: str | Path,
    teccl_result_dir: str | Path,
    output_dir: str | Path,
    title: str = "CRUX vs TE-CCL Comparison",
) -> dict[str, str]:
    crux_dir = Path(crux_result_dir).resolve()
    teccl_dir = Path(teccl_result_dir).resolve()
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    crux_summary = _load_json(crux_dir / "summary.json")
    teccl_summary = _load_json(teccl_dir / "summary.json")
    crux_trace = _load_csv(crux_dir / "link_load_trace.csv")
    teccl_trace = _load_csv(teccl_dir / "link_load_trace.csv")
    crux_schedule = _load_json(crux_dir / "schedule_history.json")
    teccl_schedule = _load_json(teccl_dir / "schedule_history.json")

    summary_png = output_path / "comparison_summary.png"
    link_png = output_path / "comparison_link_utilization.png"
    scheduler_png = output_path / "comparison_scheduler_activity.png"
    summary_json = output_path / "comparison_summary.json"

    comparison_summary = _build_comparison_summary(crux_summary, teccl_summary)
    summary_json.write_text(json.dumps(comparison_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    _plot_summary_bars(crux_summary, teccl_summary, summary_png, title)
    _plot_link_utilization(crux_trace, teccl_trace, link_png, title)
    _plot_scheduler_activity(crux_schedule, teccl_schedule, scheduler_png, title)

    return {
        "summary_json": str(summary_json),
        "summary_png": str(summary_png),
        "link_utilization_png": str(link_png),
        "scheduler_activity_png": str(scheduler_png),
    }


def _build_comparison_summary(crux_summary: dict[str, Any], teccl_summary: dict[str, Any]) -> dict[str, Any]:
    crux_rep = (crux_summary.get("repetitions") or [{}])[0]
    teccl_rep = (teccl_summary.get("repetitions") or [{}])[0]
    return {
        "title": "CRUX vs TE-CCL Comparison",
        "crux": {
            "experiment_name": crux_summary.get("experiment_name", "crux"),
            "completion_time_ms": crux_rep.get("completion_time_ms", 0.0),
            "completed_flow_count": crux_rep.get("completed_flow_count", 0),
            "schedule_invocation_count": crux_rep.get("schedule_invocation_count", 0),
            "active_link_count": crux_rep.get("active_link_count", 0),
            "average_link_utilization": crux_rep.get("average_link_utilization", 0.0),
        },
        "teccl": {
            "experiment_name": teccl_summary.get("experiment_name", "teccl"),
            "completion_time_ms": teccl_rep.get("completion_time_ms", 0.0),
            "completed_flow_count": teccl_rep.get("completed_flow_count", 0),
            "schedule_invocation_count": teccl_rep.get("schedule_invocation_count", 0),
            "active_link_count": teccl_rep.get("active_link_count", 0),
            "average_link_utilization": teccl_rep.get("average_link_utilization", 0.0),
            "epoch_action_count": teccl_rep.get("epoch_action_count", 0),
        },
    }


def _plot_summary_bars(crux_summary: dict[str, Any], teccl_summary: dict[str, Any], output_path: Path, title: str) -> None:
    crux_rep = (crux_summary.get("repetitions") or [{}])[0]
    teccl_rep = (teccl_summary.get("repetitions") or [{}])[0]
    metrics = [
        ("Completion Time (ms)", float(crux_rep.get("completion_time_ms", 0.0)), float(teccl_rep.get("completion_time_ms", 0.0))),
        ("Completed Flows", float(crux_rep.get("completed_flow_count", 0)), float(teccl_rep.get("completed_flow_count", 0))),
        ("Schedule Invocations", float(crux_rep.get("schedule_invocation_count", 0)), float(teccl_rep.get("schedule_invocation_count", 0))),
        ("Avg Link Utilization", float(crux_rep.get("average_link_utilization", 0.0)), float(teccl_rep.get("average_link_utilization", 0.0))),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    fig.suptitle(title)
    labels = ["CRUX", "TE-CCL"]
    colors = ["#1f77b4", "#d62728"]
    for axis, (metric_name, crux_value, teccl_value) in zip(axes.flatten(), metrics):
        axis.bar(labels, [crux_value, teccl_value], color=colors)
        axis.set_title(metric_name)
        axis.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_link_utilization(crux_trace: list[dict[str, str]], teccl_trace: list[dict[str, str]], output_path: Path, title: str) -> None:
    crux_series = _hottest_link_series(crux_trace)
    teccl_series = _hottest_link_series(teccl_trace)
    fig, axis = plt.subplots(figsize=(11, 5))
    if crux_series["points"]:
        axis.plot(
            [point[0] for point in crux_series["points"]],
            [point[1] for point in crux_series["points"]],
            label=f"CRUX: {crux_series['link_id']}",
            color="#1f77b4",
        )
    if teccl_series["points"]:
        axis.plot(
            [point[0] for point in teccl_series["points"]],
            [point[1] for point in teccl_series["points"]],
            label=f"TE-CCL: {teccl_series['link_id']}",
            color="#d62728",
        )
    axis.set_title(f"{title} - Hottest Link Utilization")
    axis.set_xlabel("Time (ms)")
    axis.set_ylabel("Utilization")
    axis.grid(alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_scheduler_activity(crux_schedule_bundle: list[dict[str, Any]], teccl_schedule_bundle: list[dict[str, Any]], output_path: Path, title: str) -> None:
    crux_schedule = crux_schedule_bundle[0].get("schedule_history", []) if crux_schedule_bundle else []
    teccl_schedule = teccl_schedule_bundle[0].get("schedule_history", []) if teccl_schedule_bundle else []
    fig, axis = plt.subplots(figsize=(11, 5))
    if crux_schedule:
        axis.step(
            [float(item.get("time_ms", 0.0)) for item in crux_schedule],
            [int(item.get("path_assignment_count", 0)) for item in crux_schedule],
            where="post",
            label="CRUX path assignments",
            color="#1f77b4",
        )
    if teccl_schedule:
        axis.step(
            [float(item.get("time_ms", 0.0)) for item in teccl_schedule],
            [int(item.get("epoch_action_count", 0)) for item in teccl_schedule],
            where="post",
            label="TE-CCL epoch actions",
            color="#d62728",
        )
    axis.set_title(f"{title} - Scheduler Activity")
    axis.set_xlabel("Time (ms)")
    axis.set_ylabel("Activity Count")
    axis.grid(alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _hottest_link_series(rows: list[dict[str, str]]) -> dict[str, Any]:
    per_link: dict[str, list[tuple[float, float, float]]] = {}
    for row in rows:
        link_id = row.get("link_id", "")
        if not link_id:
            continue
        per_link.setdefault(link_id, []).append(
            (
                float(row.get("time_ms", 0.0) or 0.0),
                float(row.get("utilization", 0.0) or 0.0),
                float(row.get("transmitted_mb", 0.0) or 0.0),
            )
        )
    if not per_link:
        return {"link_id": "", "points": []}

    best_link_id = max(
        per_link,
        key=lambda link_id: (
            max(point[1] for point in per_link[link_id]),
            max(point[2] for point in per_link[link_id]),
        ),
    )
    points = sorted([(time_ms, utilization) for time_ms, utilization, _ in per_link[best_link_id]], key=lambda item: item[0])
    return {"link_id": best_link_id, "points": points}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))