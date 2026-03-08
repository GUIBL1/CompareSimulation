from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt


def generate_experiment_comparison_visuals(
    result_a_dir: str | Path,
    result_b_dir: str | Path,
    output_dir: str | Path,
    label_a: str | None = None,
    label_b: str | None = None,
    title: str = "Experiment Comparison",
) -> dict[str, Any]:
    dir_a = Path(result_a_dir).resolve()
    dir_b = Path(result_b_dir).resolve()
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    summary_a = _load_json(dir_a / "summary.json")
    summary_b = _load_json(dir_b / "summary.json")
    trace_a = _load_csv(dir_a / "link_load_trace.csv")
    trace_b = _load_csv(dir_b / "link_load_trace.csv")
    schedule_a = _load_json(dir_a / "schedule_history.json")
    schedule_b = _load_json(dir_b / "schedule_history.json")

    label_a = label_a or str(summary_a.get("experiment_name", dir_a.name))
    label_b = label_b or str(summary_b.get("experiment_name", dir_b.name))

    repetition_a = (summary_a.get("repetitions") or [{}])[0]
    repetition_b = (summary_b.get("repetitions") or [{}])[0]
    shared_metrics = _shared_numeric_metrics(repetition_a, repetition_b)

    scalar_plot_dir = output_path / "scalar_metrics"
    scalar_plot_dir.mkdir(parents=True, exist_ok=True)
    scalar_plots: dict[str, str] = {}
    for metric_name in shared_metrics:
        plot_path = scalar_plot_dir / f"{_slugify(metric_name)}.png"
        _plot_scalar_metric(
            metric_name=metric_name,
            value_a=float(repetition_a.get(metric_name, 0.0)),
            value_b=float(repetition_b.get(metric_name, 0.0)),
            label_a=label_a,
            label_b=label_b,
            output_path=plot_path,
            title=title,
        )
        scalar_plots[metric_name] = str(plot_path)

    schedule_plot_dir = output_path / "schedule_metrics"
    schedule_plot_dir.mkdir(parents=True, exist_ok=True)
    schedule_plots: dict[str, str] = {}
    for metric_name in _shared_schedule_metrics(schedule_a, schedule_b):
        plot_path = schedule_plot_dir / f"{_slugify(metric_name)}.png"
        _plot_schedule_metric(schedule_a, schedule_b, metric_name, label_a, label_b, plot_path, title)
        schedule_plots[metric_name] = str(plot_path)

    hottest_link_path = output_path / "hottest_link_utilization.png"
    _plot_link_utilization(trace_a, trace_b, hottest_link_path, f"{title} - Hottest Link Utilization", label_a, label_b)

    comparison_summary = _build_generic_comparison_summary(summary_a, summary_b, label_a, label_b, shared_metrics, title)
    summary_json = output_path / "comparison_summary.json"
    summary_json.write_text(json.dumps(comparison_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "summary_json": str(summary_json),
        "scalar_metric_plots": scalar_plots,
        "schedule_metric_plots": schedule_plots,
        "hottest_link_utilization_png": str(hottest_link_path),
    }


def generate_crux_teccl_comparison_visuals(
    crux_result_dir: str | Path,
    teccl_result_dir: str | Path,
    output_dir: str | Path,
    title: str = "CRUX vs TE-CCL Comparison",
) -> dict[str, Any]:
    return generate_experiment_comparison_visuals(
        result_a_dir=crux_result_dir,
        result_b_dir=teccl_result_dir,
        output_dir=output_dir,
        label_a="CRUX",
        label_b="TE-CCL",
        title=title,
    )


def _build_generic_comparison_summary(
    summary_a: dict[str, Any],
    summary_b: dict[str, Any],
    label_a: str,
    label_b: str,
    shared_metrics: list[str],
    title: str,
) -> dict[str, Any]:
    rep_a = (summary_a.get("repetitions") or [{}])[0]
    rep_b = (summary_b.get("repetitions") or [{}])[0]
    return {
        "title": title,
        "left": {
            "label": label_a,
            "experiment_name": summary_a.get("experiment_name", label_a),
            "scheduler_type": summary_a.get("scheduler_type", "unknown"),
            "metrics": {metric: rep_a.get(metric, 0.0) for metric in shared_metrics},
        },
        "right": {
            "label": label_b,
            "experiment_name": summary_b.get("experiment_name", label_b),
            "scheduler_type": summary_b.get("scheduler_type", "unknown"),
            "metrics": {metric: rep_b.get(metric, 0.0) for metric in shared_metrics},
        },
        "shared_scalar_metrics": shared_metrics,
    }


def _plot_scalar_metric(
    metric_name: str,
    value_a: float,
    value_b: float,
    label_a: str,
    label_b: str,
    output_path: Path,
    title: str,
) -> None:
    fig, axis = plt.subplots(figsize=(7, 5))
    labels = [label_a, label_b]
    colors = ["#1f77b4", "#d62728"]
    axis.bar(labels, [value_a, value_b], color=colors)
    axis.set_title(f"{title} - {metric_name}")
    axis.set_ylabel(metric_name)
    axis.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_link_utilization(
    trace_a: list[dict[str, str]],
    trace_b: list[dict[str, str]],
    output_path: Path,
    title: str,
    label_a: str,
    label_b: str,
) -> None:
    series_a = _hottest_link_series(trace_a)
    series_b = _hottest_link_series(trace_b)
    fig, axis = plt.subplots(figsize=(11, 5))
    if series_a["points"]:
        axis.plot(
            [point[0] for point in series_a["points"]],
            [point[1] for point in series_a["points"]],
            label=f"{label_a}: {series_a['link_id']}",
            color="#1f77b4",
        )
    if series_b["points"]:
        axis.plot(
            [point[0] for point in series_b["points"]],
            [point[1] for point in series_b["points"]],
            label=f"{label_b}: {series_b['link_id']}",
            color="#d62728",
        )
    axis.set_title(title)
    axis.set_xlabel("Time (ms)")
    axis.set_ylabel("Utilization")
    axis.grid(alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_schedule_metric(
    schedule_bundle_a: list[dict[str, Any]],
    schedule_bundle_b: list[dict[str, Any]],
    metric_name: str,
    label_a: str,
    label_b: str,
    output_path: Path,
    title: str,
) -> None:
    schedule_a = schedule_bundle_a[0].get("schedule_history", []) if schedule_bundle_a else []
    schedule_b = schedule_bundle_b[0].get("schedule_history", []) if schedule_bundle_b else []
    fig, axis = plt.subplots(figsize=(11, 5))
    if schedule_a:
        axis.step(
            [float(item.get("time_ms", 0.0)) for item in schedule_a],
            [int(item.get(metric_name, 0)) for item in schedule_a],
            where="post",
            label=label_a,
            color="#1f77b4",
        )
    if schedule_b:
        axis.step(
            [float(item.get("time_ms", 0.0)) for item in schedule_b],
            [int(item.get(metric_name, 0)) for item in schedule_b],
            where="post",
            label=label_b,
            color="#d62728",
        )
    axis.set_title(f"{title} - {metric_name}")
    axis.set_xlabel("Time (ms)")
    axis.set_ylabel(metric_name)
    axis.grid(alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _shared_numeric_metrics(summary_a: dict[str, Any], summary_b: dict[str, Any]) -> list[str]:
    excluded = {"repetition_index"}
    keys = sorted(set(summary_a) & set(summary_b))
    return [
        key
        for key in keys
        if key not in excluded and isinstance(summary_a.get(key), int | float) and isinstance(summary_b.get(key), int | float)
    ]


def _shared_schedule_metrics(schedule_bundle_a: list[dict[str, Any]], schedule_bundle_b: list[dict[str, Any]]) -> list[str]:
    schedule_a = schedule_bundle_a[0].get("schedule_history", []) if schedule_bundle_a else []
    schedule_b = schedule_bundle_b[0].get("schedule_history", []) if schedule_bundle_b else []
    if not schedule_a and not schedule_b:
        return []
    candidate_metrics = [
        "flow_assignment_count",
        "path_assignment_count",
        "priority_assignment_count",
        "epoch_action_count",
    ]
    shared: list[str] = []
    for metric_name in candidate_metrics:
        max_a = max((int(item.get(metric_name, 0)) for item in schedule_a), default=0)
        max_b = max((int(item.get(metric_name, 0)) for item in schedule_b), default=0)
        if max_a > 0 or max_b > 0:
            shared.append(metric_name)
    return shared


def _slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower() or "metric"


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