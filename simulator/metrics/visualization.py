from __future__ import annotations

import csv
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager


PERCENTILE_LEVELS = [(0.50, "P50"), (0.95, "P95"), (0.99, "P99")]
QUEUE_PERCENTILE_LEVELS = [(0.95, "P95"), (0.99, "P99")]
CONGESTION_THRESHOLD = 0.90
EPSILON = 1e-9
PREFERRED_CJK_FONTS = [
    "AR PL UMing CN",
    "AR PL SungtiL GB",
    "AR PL KaitiM GB",
    "Droid Sans Fallback",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Zen Hei",
]


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
    _cleanup_legacy_outputs(output_path)

    summary_a = _load_json(dir_a / "summary.json")
    summary_b = _load_json(dir_b / "summary.json")
    trace_a = _load_csv(dir_a / "link_load_trace.csv")
    trace_b = _load_csv(dir_b / "link_load_trace.csv")
    flow_a = _load_csv(dir_a / "flow_trace.csv")
    flow_b = _load_csv(dir_b / "flow_trace.csv")

    label_a = label_a or str(summary_a.get("experiment_name", dir_a.name))
    label_b = label_b or str(summary_b.get("experiment_name", dir_b.name))

    metrics_a = _compute_comparison_metrics(summary_a, trace_a, flow_a)
    metrics_b = _compute_comparison_metrics(summary_b, trace_b, flow_b)

    plot_dir = output_path / "metric_plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    plot_outputs: dict[str, str] = {}
    for spec in _metric_specs():
        plot_path = plot_dir / f"{spec['id']}.png"
        _render_metric_plot(
            spec=spec,
            metrics_a=metrics_a,
            metrics_b=metrics_b,
            label_a=label_a,
            label_b=label_b,
            output_path=plot_path,
            title=title,
        )
        plot_outputs[spec["id"]] = str(plot_path)

    comparison_summary = _build_comparison_summary(summary_a, summary_b, label_a, label_b, metrics_a, metrics_b, title)
    summary_json = output_path / "comparison_summary.json"
    summary_json.write_text(json.dumps(comparison_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "summary_json": str(summary_json),
        "metric_plots": plot_outputs,
        "scalar_metric_plots": plot_outputs,
        "schedule_metric_plots": {},
        "removed_default_metrics": [
            "epoch_action_count",
            "schedule_invocation_count",
            "total_flow_count",
            "completed_flow_count",
            "total_transmitted_mb",
            "path_assignment_count",
            "priority_assignment_count",
            "active_link_count",
            "total_job_count",
        ],
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


def _metric_specs() -> list[dict[str, str]]:
    return [
        {
            "id": "completion_time_ms",
            "display_name": "Completion Time（完成时间）",
            "chart_type": "bar",
            "value_key": "completion_time_ms",
            "axis_label": "Time (ms)（时间，毫秒）",
        },
        {
            "id": "job_completion_ratio",
            "display_name": "Job Completion Ratio（作业完成率）",
            "chart_type": "bar",
            "value_key": "job_completion_ratio",
            "axis_label": "Ratio（比例）",
        },
        {
            "id": "bottleneck_link_peak_utilization",
            "display_name": "Bottleneck Link Peak Utilization（瓶颈链路峰值利用率）",
            "chart_type": "bar",
            "value_key": "bottleneck_link_peak_utilization",
            "axis_label": "Utilization（利用率）",
        },
        {
            "id": "bottleneck_link_average_utilization",
            "display_name": "Bottleneck Link Average Utilization（瓶颈链路平均利用率）",
            "chart_type": "bar",
            "value_key": "bottleneck_link_average_utilization",
            "axis_label": "Utilization（利用率）",
        },
        {
            "id": "bottleneck_busy_time_ms",
            "display_name": "Bottleneck Busy Time（瓶颈忙时长）",
            "chart_type": "bar",
            "value_key": "bottleneck_busy_time_ms",
            "axis_label": "Time (ms)（时间，毫秒）",
        },
        {
            "id": "queue_backlog_percentiles_mb",
            "display_name": "Queue Backlog P95 / P99（队列积压 P95 / P99）",
            "chart_type": "grouped_bar",
            "value_key": "queue_backlog_percentiles_mb",
            "axis_label": "Backlog (MB)（积压，MB）",
        },
        {
            "id": "flow_completion_time_percentiles_ms",
            "display_name": "Flow Completion Time P50 / P95 / P99（流完成时延 P50 / P95 / P99）",
            "chart_type": "ecdf",
            "value_key": "flow_completion_times_ms",
            "axis_label": "Time (ms)（时间，毫秒）",
        },
        {
            "id": "job_completion_time_percentiles_ms",
            "display_name": "Job Completion Time P50 / P95 / P99（作业完成时延 P50 / P95 / P99）",
            "chart_type": "ecdf",
            "value_key": "job_completion_times_ms",
            "axis_label": "Time (ms)（时间，毫秒）",
        },
        {
            "id": "completion_time_spread_ms",
            "display_name": "Completion Time Spread（完成时间离散度）",
            "chart_type": "bar",
            "value_key": "completion_time_spread_ms",
            "axis_label": "Std Dev (ms)（标准差，毫秒）",
        },
        {
            "id": "congestion_duration_ms",
            "display_name": "Congestion Duration（拥塞持续时间）",
            "chart_type": "bar",
            "value_key": "congestion_duration_ms",
            "axis_label": "Time (ms)（时间，毫秒）",
        },
    ]


def _configure_plot_fonts() -> None:
    available_fonts = {font_manager.FontProperties(fname=path).get_name() for path in font_manager.findSystemFonts()}
    for font_name in PREFERRED_CJK_FONTS:
        if font_name in available_fonts:
            plt.rcParams["font.family"] = "sans-serif"
            plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans", "Liberation Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Liberation Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def _cleanup_legacy_outputs(output_path: Path) -> None:
    legacy_dirs = [output_path / "scalar_metrics", output_path / "schedule_metrics", output_path / "metric_plots"]
    for directory in legacy_dirs:
        if directory.exists():
            shutil.rmtree(directory)
    legacy_files = [output_path / "hottest_link_utilization.png", output_path / "comparison_summary.json"]
    for file_path in legacy_files:
        if file_path.exists():
            file_path.unlink()


_configure_plot_fonts()


def _build_comparison_summary(
    summary_a: dict[str, Any],
    summary_b: dict[str, Any],
    label_a: str,
    label_b: str,
    metrics_a: dict[str, Any],
    metrics_b: dict[str, Any],
    title: str,
) -> dict[str, Any]:
    metric_entries: list[dict[str, Any]] = []
    for spec in _metric_specs():
        metric_entries.append(
            {
                "id": spec["id"],
                "display_name": spec["display_name"],
                "chart_type": spec["chart_type"],
                "left": _summary_value(metrics_a, spec),
                "right": _summary_value(metrics_b, spec),
            }
        )
    return {
        "title": title,
        "left": {
            "label": label_a,
            "experiment_name": summary_a.get("experiment_name", label_a),
            "scheduler_type": summary_a.get("scheduler_type", "unknown"),
        },
        "right": {
            "label": label_b,
            "experiment_name": summary_b.get("experiment_name", label_b),
            "scheduler_type": summary_b.get("scheduler_type", "unknown"),
        },
        "metrics": metric_entries,
        "removed_default_metrics": [
            "epoch_action_count",
            "schedule_invocation_count",
            "total_flow_count",
            "completed_flow_count",
            "total_transmitted_mb",
            "path_assignment_count",
            "priority_assignment_count",
            "active_link_count",
            "total_job_count",
        ],
    }


def _summary_value(metrics: dict[str, Any], spec: dict[str, str]) -> Any:
    if spec["chart_type"] == "ecdf":
        return _format_percentiles(metrics.get(spec["id"], {}), [label for _, label in PERCENTILE_LEVELS])
    if spec["chart_type"] == "grouped_bar":
        return _format_percentiles(metrics.get(spec["value_key"], {}), [label for _, label in QUEUE_PERCENTILE_LEVELS])
    return metrics.get(spec["value_key"])


def _render_metric_plot(
    spec: dict[str, str],
    metrics_a: dict[str, Any],
    metrics_b: dict[str, Any],
    label_a: str,
    label_b: str,
    output_path: Path,
    title: str,
) -> None:
    chart_type = spec["chart_type"]
    if chart_type == "dumbbell":
        _plot_dumbbell_metric(
            metric_name=spec["display_name"],
            value_a=float(metrics_a.get(spec["value_key"], 0.0) or 0.0),
            value_b=float(metrics_b.get(spec["value_key"], 0.0) or 0.0),
            label_a=label_a,
            label_b=label_b,
            axis_label=spec["axis_label"],
            output_path=output_path,
            title=title,
        )
        return
    if chart_type == "bar":
        _plot_bar_metric(
            metric_name=spec["display_name"],
            value_a=float(metrics_a.get(spec["value_key"], 0.0) or 0.0),
            value_b=float(metrics_b.get(spec["value_key"], 0.0) or 0.0),
            label_a=label_a,
            label_b=label_b,
            axis_label=spec["axis_label"],
            output_path=output_path,
            title=title,
        )
        return
    if chart_type == "grouped_bar":
        _plot_grouped_bar_metric(
            metric_name=spec["display_name"],
            percentiles_a=metrics_a.get(spec["value_key"], {}),
            percentiles_b=metrics_b.get(spec["value_key"], {}),
            label_a=label_a,
            label_b=label_b,
            axis_label=spec["axis_label"],
            output_path=output_path,
            title=title,
        )
        return
    if chart_type == "ecdf":
        _plot_ecdf_metric(
            metric_name=spec["display_name"],
            samples_a=metrics_a.get(spec["value_key"], []),
            samples_b=metrics_b.get(spec["value_key"], []),
            percentiles_a=metrics_a.get(spec["id"], {}),
            percentiles_b=metrics_b.get(spec["id"], {}),
            label_a=label_a,
            label_b=label_b,
            axis_label=spec["axis_label"],
            output_path=output_path,
            title=title,
        )
        return
    raise ValueError(f"Unsupported chart type: {chart_type}")


def _plot_bar_metric(
    metric_name: str,
    value_a: float,
    value_b: float,
    label_a: str,
    label_b: str,
    axis_label: str,
    output_path: Path,
    title: str,
) -> None:
    fig, axis = plt.subplots(figsize=(7, 5))
    labels = [label_a, label_b]
    values = [value_a, value_b]
    bars = axis.bar(labels, values, color=["#1f77b4", "#d62728"], width=0.6)
    axis.set_title(f"{title} - {metric_name}")
    axis.set_ylabel(axis_label)
    axis.grid(axis="y", alpha=0.3)
    for bar, value in zip(bars, values):
        axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), _format_number(value), ha="center", va="bottom")
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_dumbbell_metric(
    metric_name: str,
    value_a: float,
    value_b: float,
    label_a: str,
    label_b: str,
    axis_label: str,
    output_path: Path,
    title: str,
) -> None:
    fig, axis = plt.subplots(figsize=(8.5, 3.6))
    low = min(value_a, value_b)
    high = max(value_a, value_b)
    axis.hlines(y=0, xmin=low, xmax=high, color="#b7b7b7", linewidth=2)
    axis.scatter(value_a, 0, color="#1f77b4", s=90, label=label_a, zorder=3)
    axis.scatter(value_b, 0, color="#d62728", s=90, label=label_b, zorder=3)
    axis.annotate(_format_number(value_a), (value_a, 0), xytext=(0, 10), textcoords="offset points", ha="center")
    axis.annotate(_format_number(value_b), (value_b, 0), xytext=(0, -18), textcoords="offset points", ha="center")
    axis.set_title(f"{title} - {metric_name}")
    axis.set_xlabel(axis_label)
    axis.set_yticks([])
    axis.grid(axis="x", alpha=0.3)
    margin = max(abs(high) * 0.1, 1.0 if high > 0 else 0.1)
    axis.set_xlim(left=min(0.0, low - margin * 0.2), right=high + margin)
    axis.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_grouped_bar_metric(
    metric_name: str,
    percentiles_a: dict[str, float],
    percentiles_b: dict[str, float],
    label_a: str,
    label_b: str,
    axis_label: str,
    output_path: Path,
    title: str,
) -> None:
    categories = [label for _, label in QUEUE_PERCENTILE_LEVELS]
    values_a = [float(percentiles_a.get(label, 0.0) or 0.0) for label in categories]
    values_b = [float(percentiles_b.get(label, 0.0) or 0.0) for label in categories]
    positions = list(range(len(categories)))
    width = 0.34

    fig, axis = plt.subplots(figsize=(8.5, 5))
    bars_a = axis.bar([pos - width / 2 for pos in positions], values_a, width=width, color="#1f77b4", label=label_a)
    bars_b = axis.bar([pos + width / 2 for pos in positions], values_b, width=width, color="#d62728", label=label_b)
    axis.set_title(f"{title} - {metric_name}")
    axis.set_xticks(positions)
    axis.set_xticklabels(categories)
    axis.set_ylabel(axis_label)
    axis.grid(axis="y", alpha=0.3)
    axis.legend()
    for bars in [bars_a, bars_b]:
        for bar in bars:
            axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), _format_number(bar.get_height()), ha="center", va="bottom")
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_ecdf_metric(
    metric_name: str,
    samples_a: list[float],
    samples_b: list[float],
    percentiles_a: dict[str, float],
    percentiles_b: dict[str, float],
    label_a: str,
    label_b: str,
    axis_label: str,
    output_path: Path,
    title: str,
) -> None:
    fig, axis = plt.subplots(figsize=(9.5, 5.5))
    _plot_ecdf_series(axis, samples_a, label_a, "#1f77b4")
    _plot_ecdf_series(axis, samples_b, label_b, "#d62728")
    _plot_percentile_markers(axis, percentiles_a, "#1f77b4")
    _plot_percentile_markers(axis, percentiles_b, "#d62728")
    axis.set_title(f"{title} - {metric_name}")
    axis.set_xlabel(axis_label)
    axis.set_ylabel("Cumulative Probability（累计概率）")
    axis.grid(alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_ecdf_series(axis: Any, samples: list[float], label: str, color: str) -> None:
    if not samples:
        axis.plot([], [], label=f"{label} (no completed samples)")
        return
    sorted_samples = sorted(samples)
    y_values = [(index + 1) / len(sorted_samples) for index in range(len(sorted_samples))]
    axis.step(sorted_samples, y_values, where="post", label=label, color=color)


def _plot_percentile_markers(axis: Any, percentiles: dict[str, float], color: str) -> None:
    y_positions = {"P50": 0.50, "P95": 0.95, "P99": 0.99}
    for label, y_pos in y_positions.items():
        value = float(percentiles.get(label, 0.0) or 0.0)
        if value <= 0.0:
            continue
        axis.scatter([value], [y_pos], color=color, s=35, zorder=4)
        axis.annotate(label, (value, y_pos), xytext=(6, 0), textcoords="offset points", color=color, va="center")


def _compute_comparison_metrics(
    summary: dict[str, Any],
    trace_rows: list[dict[str, str]],
    flow_rows: list[dict[str, str]],
) -> dict[str, Any]:
    repetition_summary = (summary.get("repetitions") or [{}])[0]
    total_jobs = int(repetition_summary.get("total_job_count", 0) or 0)
    completed_jobs = int(repetition_summary.get("completed_job_count", 0) or 0)

    bottleneck = _build_bottleneck_metrics(trace_rows)
    logical_flow_durations = _extract_logical_transfer_durations(flow_rows)
    job_completion_durations = _extract_job_completion_durations(flow_rows)

    return {
        "completion_time_ms": float(repetition_summary.get("completion_time_ms", 0.0) or 0.0),
        "job_completion_ratio": (completed_jobs / total_jobs) if total_jobs > 0 else 0.0,
        "bottleneck_link_peak_utilization": bottleneck["peak_utilization"],
        "bottleneck_link_average_utilization": bottleneck["average_utilization"],
        "bottleneck_busy_time_ms": bottleneck["busy_time_ms"],
        "queue_backlog_percentiles_mb": bottleneck["queue_backlog_percentiles_mb"],
        "flow_completion_times_ms": logical_flow_durations,
        "flow_completion_time_percentiles_ms": _compute_percentiles(logical_flow_durations, PERCENTILE_LEVELS),
        "job_completion_times_ms": job_completion_durations,
        "job_completion_time_percentiles_ms": _compute_percentiles(job_completion_durations, PERCENTILE_LEVELS),
        "completion_time_spread_ms": _population_stddev(job_completion_durations),
        "congestion_duration_ms": bottleneck["congestion_duration_ms"],
        "bottleneck_link_id": bottleneck["link_id"],
    }


def _build_bottleneck_metrics(trace_rows: list[dict[str, str]]) -> dict[str, Any]:
    per_link: dict[str, list[dict[str, float]]] = defaultdict(list)
    for row in trace_rows:
        link_id = row.get("link_id", "")
        if not link_id:
            continue
        per_link[link_id].append(
            {
                "time_ms": float(row.get("time_ms", 0.0) or 0.0),
                "utilization": float(row.get("utilization", 0.0) or 0.0),
                "queue_backlog_mb": float(row.get("queue_backlog_mb", 0.0) or 0.0),
                "transmitted_mb": float(row.get("transmitted_mb", 0.0) or 0.0),
            }
        )

    if not per_link:
        return {
            "link_id": "",
            "peak_utilization": 0.0,
            "average_utilization": 0.0,
            "busy_time_ms": 0.0,
            "queue_backlog_percentiles_mb": {label: 0.0 for _, label in QUEUE_PERCENTILE_LEVELS},
            "congestion_duration_ms": 0.0,
        }

    ranked_links = []
    for link_id, raw_points in per_link.items():
        points = sorted(raw_points, key=lambda item: item["time_ms"])
        durations = _build_piecewise_durations(points)
        peak_utilization = max((point["utilization"] for point in points), default=0.0)
        final_transmitted = max((point["transmitted_mb"] for point in points), default=0.0)
        busy_time = sum(duration["duration_ms"] for duration in durations if duration["utilization"] > EPSILON)
        ranked_links.append((peak_utilization, final_transmitted, busy_time, link_id, durations))

    peak_utilization, _, _, link_id, durations = max(ranked_links, key=lambda item: (item[0], item[1], item[2], item[3]))
    return {
        "link_id": link_id,
        "peak_utilization": peak_utilization,
        "average_utilization": _weighted_average(durations, "utilization"),
        "busy_time_ms": sum(duration["duration_ms"] for duration in durations if duration["utilization"] > EPSILON),
        "queue_backlog_percentiles_mb": _weighted_percentiles(durations, "queue_backlog_mb", QUEUE_PERCENTILE_LEVELS),
        "congestion_duration_ms": sum(
            duration["duration_ms"] for duration in durations if duration["utilization"] >= CONGESTION_THRESHOLD
        ),
    }


def _extract_logical_transfer_durations(flow_rows: list[dict[str, str]]) -> list[float]:
    groups: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in flow_rows:
        repetition_index = int(float(row.get("repetition_index", 0) or 0))
        owner_job_id = row.get("owner_job_id", "")
        transfer_id = row.get("chunk_id") or row.get("demand_id") or row.get("flow_id") or ""
        if not transfer_id:
            continue
        key = (repetition_index, owner_job_id, transfer_id)
        bucket = groups.setdefault(
            key,
            {
                "start": math.inf,
                "completed_end": -math.inf,
                "has_completed_segment": False,
                "seen": False,
            },
        )
        bucket["seen"] = True
        start_time = _parse_optional_float(row.get("start_time_ms", ""))
        end_time = _parse_optional_float(row.get("end_time_ms", ""))
        if start_time is not None:
            bucket["start"] = min(bucket["start"], start_time)
        if end_time is not None and str(row.get("status", "")).lower() == "completed":
            bucket["completed_end"] = max(bucket["completed_end"], end_time)
            bucket["has_completed_segment"] = True

    durations: list[float] = []
    for bucket in groups.values():
        if not bucket["seen"] or not bucket["has_completed_segment"] or bucket["completed_end"] < bucket["start"]:
            continue
        durations.append(bucket["completed_end"] - bucket["start"])
    return sorted(durations)


def _extract_job_completion_durations(flow_rows: list[dict[str, str]]) -> list[float]:
    groups: dict[tuple[int, str], dict[str, Any]] = {}
    for row in flow_rows:
        repetition_index = int(float(row.get("repetition_index", 0) or 0))
        owner_job_id = row.get("owner_job_id", "")
        if not owner_job_id:
            continue
        key = (repetition_index, owner_job_id)
        bucket = groups.setdefault(
            key,
            {
                "start": math.inf,
                "completed_end": -math.inf,
                "has_completed_segment": False,
                "seen": False,
            },
        )
        bucket["seen"] = True
        start_time = _parse_optional_float(row.get("start_time_ms", ""))
        end_time = _parse_optional_float(row.get("end_time_ms", ""))
        if start_time is not None:
            bucket["start"] = min(bucket["start"], start_time)
        if end_time is not None and str(row.get("status", "")).lower() == "completed":
            bucket["completed_end"] = max(bucket["completed_end"], end_time)
            bucket["has_completed_segment"] = True

    durations: list[float] = []
    for bucket in groups.values():
        if not bucket["seen"] or not bucket["has_completed_segment"] or bucket["completed_end"] < bucket["start"]:
            continue
        durations.append(bucket["completed_end"] - bucket["start"])
    return sorted(durations)


def _compute_percentiles(values: list[float], levels: list[tuple[float, str]]) -> dict[str, float]:
    if not values:
        return {label: 0.0 for _, label in levels}
    sorted_values = sorted(values)
    count = len(sorted_values)
    results: dict[str, float] = {}
    for level, label in levels:
        if count == 1:
            results[label] = float(sorted_values[0])
            continue
        position = level * (count - 1)
        lower_index = int(math.floor(position))
        upper_index = int(math.ceil(position))
        lower_value = float(sorted_values[lower_index])
        upper_value = float(sorted_values[upper_index])
        if lower_index == upper_index:
            results[label] = lower_value
            continue
        fraction = position - lower_index
        results[label] = lower_value + (upper_value - lower_value) * fraction
    return results


def _weighted_percentiles(
    durations: list[dict[str, float]],
    value_key: str,
    levels: list[tuple[float, str]],
) -> dict[str, float]:
    weighted_samples = [(duration[value_key], duration["duration_ms"]) for duration in durations if duration["duration_ms"] > 0.0]
    if not weighted_samples:
        fallback_values = [duration[value_key] for duration in durations]
        return _compute_percentiles(fallback_values, levels)

    weighted_samples.sort(key=lambda item: item[0])
    total_weight = sum(weight for _, weight in weighted_samples)
    if total_weight <= 0.0:
        fallback_values = [value for value, _ in weighted_samples]
        return _compute_percentiles(fallback_values, levels)

    results: dict[str, float] = {}
    for level, label in levels:
        threshold = total_weight * level
        cumulative = 0.0
        chosen = weighted_samples[-1][0]
        for value, weight in weighted_samples:
            cumulative += weight
            if cumulative >= threshold:
                chosen = value
                break
        results[label] = float(chosen)
    return results


def _population_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _build_piecewise_durations(points: list[dict[str, float]]) -> list[dict[str, float]]:
    if len(points) < 2:
        if not points:
            return []
        return [{"duration_ms": 0.0, "utilization": points[0]["utilization"], "queue_backlog_mb": points[0]["queue_backlog_mb"]}]

    durations: list[dict[str, float]] = []
    for current, nxt in zip(points, points[1:]):
        durations.append(
            {
                "duration_ms": max(0.0, nxt["time_ms"] - current["time_ms"]),
                "utilization": current["utilization"],
                "queue_backlog_mb": current["queue_backlog_mb"],
            }
        )
    return durations


def _weighted_average(durations: list[dict[str, float]], value_key: str) -> float:
    weighted_sum = 0.0
    total_duration = 0.0
    for duration in durations:
        interval = duration["duration_ms"]
        if interval <= 0.0:
            continue
        total_duration += interval
        weighted_sum += interval * duration[value_key]
    if total_duration <= 0.0:
        return float(durations[-1][value_key]) if durations else 0.0
    return weighted_sum / total_duration


def _format_percentiles(values: dict[str, float], labels: list[str]) -> dict[str, float]:
    return {label: float(values.get(label, 0.0) or 0.0) for label in labels}


def _format_number(value: float) -> str:
    if abs(value) >= 100.0:
        return f"{value:.1f}"
    if abs(value) >= 1.0:
        return f"{value:.2f}"
    return f"{value:.3f}"


def _parse_optional_float(raw_value: str | None) -> float | None:
    if raw_value in {None, ""}:
        return None
    return float(raw_value)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))