from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from simulator.experiment.matrix import enumerate_parameter_sweep_runs
from simulator.experiment.matrix import enumerate_public_run_pairs
from simulator.experiment.matrix import load_fair_comparison_matrix


def build_result_attribution_report(result_dir: str | Path) -> dict[str, Any]:
    result_path = Path(result_dir).resolve()
    summary = _load_json_if_exists(result_path / "summary.json") or {}
    scheduler_debug = _load_json_if_exists(result_path / "scheduler_debug.json") or {}
    schedule_history_bundle = _load_json_if_exists(result_path / "schedule_history.json") or []
    link_trace_rows = _load_csv_if_exists(result_path / "link_load_trace.csv")
    flow_rows = _load_csv_if_exists(result_path / "flow_trace.csv")

    repetition_summary = _extract_first_repetition_summary(summary)
    schedule_history = schedule_history_bundle[0]["schedule_history"] if schedule_history_bundle else []
    scheduler_debug_state = (
        scheduler_debug.get("repetitions", [{}])[0].get("scheduler_debug_state", {})
        if scheduler_debug.get("repetitions")
        else {}
    )
    scheduler_type = str(summary.get("scheduler_type", repetition_summary.get("scheduler_type", "unknown")))

    report = {
        "result_dir": str(result_path),
        "experiment_name": summary.get("experiment_name", result_path.name),
        "scheduler_type": scheduler_type,
        "aggregate_metrics": dict(summary.get("aggregate_metrics", {})),
        "phase_timing": _build_phase_timing_summary(repetition_summary, scheduler_type, schedule_history, scheduler_debug_state, flow_rows),
        "link_curve_summary": _build_link_curve_summary(link_trace_rows),
        "epoch_action_summary": _build_epoch_action_summary(schedule_history, scheduler_debug_state),
        "references": {
            "summary_json": str(result_path / "summary.json"),
            "scheduler_debug_json": str(result_path / "scheduler_debug.json"),
            "schedule_history_json": str(result_path / "schedule_history.json"),
            "link_load_trace_csv": str(result_path / "link_load_trace.csv"),
            "flow_trace_csv": str(result_path / "flow_trace.csv"),
        },
    }
    return report


def build_project_handoff_report(
    result_dirs: list[str | Path],
    matrix_path: str | Path | None = None,
) -> dict[str, Any]:
    result_reports = [build_result_attribution_report(result_dir) for result_dir in result_dirs]
    handoff_report: dict[str, Any] = {
        "project": "simulation",
        "validated_results": result_reports,
        "current_stage_status": {
            "stage_09": "completed",
            "stage_10": "completed",
            "stage_11": "completed",
            "stage_12": "completed",
        },
        "key_design_decisions": [
            "所有拓扑、工作负载和实验输入都保持文件驱动，调度器内部不写死拓扑常量。",
            "CRUX 维持 job-level 的路径与优先级决策，而 TE-CCL 保留 chunk/epoch 语义、GPU 复制能力和交换机无长期 buffer 约束。",
            "统一结果契约由 export_results 提供，因此 CRUX 和 TE-CCL 都会输出 summary、trace、scheduler debug 和链路时间线产物。",
            "公平对比由共享 topology_file、workload_file、random_seed 和公共 simulation/metrics 设置定义，只有调度器私有参数允许变化。",
        ],
        "next_recommended_steps": [
            "将公平矩阵条目物化为可运行实验配置，或直接补一个基于矩阵规格的批处理 runner。",
            "执行部分矩阵案例，把当前归因报告从 minimal_e2e 基线扩展到规模扩展和负载敏感性实验。",
            "基于现有链路时间线生成绘图脚本或 notebook，产出论文所需的链路曲线和阶段耗时图。",
        ],
    }
    if matrix_path is not None:
        matrix = load_fair_comparison_matrix(matrix_path)
        handoff_report["fair_matrix"] = {
            "source_path": str(Path(matrix_path).resolve()),
            "public_case_count": len(matrix.public_cases),
            "public_run_count": len(enumerate_public_run_pairs(matrix)),
            "parameter_sweep_count": len(matrix.parameter_sweeps),
            "parameter_sweep_run_count": len(enumerate_parameter_sweep_runs(matrix)),
            "families": sorted({case.family for case in matrix.public_cases}),
            "results_root": matrix.defaults.results_root,
            "repeatability": dict(matrix.defaults.repeatability),
        }
    return handoff_report


def render_project_handoff_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 项目交接摘要")
    lines.append("")
    lines.append("## 当前状态")
    lines.append("")
    for stage_id, status in report.get("current_stage_status", {}).items():
        lines.append(f"- {stage_id}: {status}")
    lines.append("")

    fair_matrix = report.get("fair_matrix")
    if fair_matrix:
        lines.append("## 公平对比矩阵")
        lines.append("")
        lines.append(f"- 配置文件: {fair_matrix['source_path']}")
        lines.append(f"- 公共案例数: {fair_matrix['public_case_count']}")
        lines.append(f"- 公共运行规格数: {fair_matrix['public_run_count']}")
        lines.append(f"- 参数扫频定义数: {fair_matrix['parameter_sweep_count']}")
        lines.append(f"- 参数扫频运行规格数: {fair_matrix['parameter_sweep_run_count']}")
        lines.append(f"- 结果根目录: {fair_matrix['results_root']}")
        lines.append("")

    lines.append("## 已验证结果")
    lines.append("")
    for result in report.get("validated_results", []):
        phase_timing = result.get("phase_timing", {})
        link_curve_summary = result.get("link_curve_summary", {})
        epoch_action_summary = result.get("epoch_action_summary", {})
        lines.append(f"### {result['experiment_name']} ({result['scheduler_type']})")
        lines.append("")
        lines.append(f"- 完成时间: {phase_timing.get('completion_time_ms', 0.0)} ms")
        lines.append(f"- 调度调用次数: {phase_timing.get('schedule_invocation_count', 0)}")
        lines.append(f"- 活跃链路数: {link_curve_summary.get('active_link_count', 0)}")
        lines.append(f"- 峰值链路利用率: {link_curve_summary.get('peak_utilization', 0.0)}")
        lines.append(f"- epoch 动作总数: {epoch_action_summary.get('total_epoch_actions', 0)}")
        hottest_links = link_curve_summary.get("hottest_links", [])
        if hottest_links:
            hottest = hottest_links[0]
            lines.append(
                f"- 最热点链路: {hottest['link_id']}，峰值利用率 {hottest['peak_utilization']}，累计传输 {hottest['final_transmitted_mb']} MB"
            )
        sample_actions = epoch_action_summary.get("sample_actions", [])
        if sample_actions:
            first_action = sample_actions[0]
            lines.append(
                f"- 首个 epoch 动作样例: t={first_action['time_ms']}，{first_action['current_node']} -> {first_action['next_node']}，chunk={first_action['chunk_id']}"
            )
        lines.append("")

    lines.append("## 关键设计决策")
    lines.append("")
    for item in report.get("key_design_decisions", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 下一步")
    lines.append("")
    for item in report.get("next_recommended_steps", []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def write_project_handoff_report(
    output_dir: str | Path,
    result_dirs: list[str | Path],
    matrix_path: str | Path | None = None,
) -> dict[str, str]:
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    report = build_project_handoff_report(result_dirs=result_dirs, matrix_path=matrix_path)
    json_path = output_path / "project_handoff_report.json"
    markdown_path = output_path / "project_handoff_report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(render_project_handoff_markdown(report), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


def _build_phase_timing_summary(
    repetition_summary: dict[str, Any],
    scheduler_type: str,
    schedule_history: list[dict[str, Any]],
    scheduler_debug_state: dict[str, Any],
    flow_rows: list[dict[str, str]],
) -> dict[str, Any]:
    completion_time_ms = float(repetition_summary.get("completion_time_ms", 0.0) or 0.0)
    schedule_invocation_count = int(repetition_summary.get("schedule_invocation_count", 0) or 0)
    first_flow_start = min(
        (float(row["start_time_ms"]) for row in flow_rows if row.get("start_time_ms") not in {None, ""}),
        default=0.0,
    )
    last_flow_end = max(
        (float(row["end_time_ms"]) for row in flow_rows if row.get("end_time_ms") not in {None, ""}),
        default=completion_time_ms,
    )
    phase_summary = {
        "completion_time_ms": completion_time_ms,
        "schedule_invocation_count": schedule_invocation_count,
        "communication_window_ms": max(0.0, last_flow_end - first_flow_start),
        "first_flow_start_ms": first_flow_start,
        "last_flow_end_ms": last_flow_end,
    }
    if scheduler_type == "crux":
        observed = scheduler_debug_state.get("observed_comm_time_ms", {})
        phase_summary["job_comm_time_ms"] = max((float(value) for value in observed.values()), default=0.0)
    if scheduler_type == "teccl":
        epoch_size_ms = float((scheduler_debug_state.get("strategy") or {}).get("epoch_size_ms", 0.0) or 0.0)
        active_epochs = [item for item in schedule_history if int(item.get("epoch_action_count", 0)) > 0]
        phase_summary["epoch_size_ms"] = epoch_size_ms
        phase_summary["active_epoch_count"] = len(active_epochs)
        phase_summary["epoch_runtime_ms"] = len(active_epochs) * epoch_size_ms
    return phase_summary


def _build_link_curve_summary(link_trace_rows: list[dict[str, str]]) -> dict[str, Any]:
    per_link: dict[str, dict[str, Any]] = {}
    for row in link_trace_rows:
        link_id = row.get("link_id", "")
        if not link_id:
            continue
        bucket = per_link.setdefault(
            link_id,
            {
                "link_id": link_id,
                "peak_utilization": 0.0,
                "max_active_flow_count": 0,
                "final_transmitted_mb": 0.0,
                "first_active_time_ms": None,
                "last_active_time_ms": None,
            },
        )
        utilization = float(row.get("utilization", 0.0) or 0.0)
        active_flow_count = int(float(row.get("active_flow_count", 0) or 0))
        transmitted_mb = float(row.get("transmitted_mb", 0.0) or 0.0)
        time_ms = float(row.get("time_ms", 0.0) or 0.0)
        bucket["peak_utilization"] = max(bucket["peak_utilization"], utilization)
        bucket["max_active_flow_count"] = max(bucket["max_active_flow_count"], active_flow_count)
        bucket["final_transmitted_mb"] = max(bucket["final_transmitted_mb"], transmitted_mb)
        if active_flow_count > 0 or utilization > 0.0:
            bucket["first_active_time_ms"] = time_ms if bucket["first_active_time_ms"] is None else min(bucket["first_active_time_ms"], time_ms)
            bucket["last_active_time_ms"] = time_ms if bucket["last_active_time_ms"] is None else max(bucket["last_active_time_ms"], time_ms)

    hottest_links = sorted(
        per_link.values(),
        key=lambda item: (-float(item["peak_utilization"]), -float(item["final_transmitted_mb"]), item["link_id"]),
    )[:5]
    return {
        "active_link_count": sum(1 for item in per_link.values() if float(item["final_transmitted_mb"]) > 0.0),
        "peak_utilization": max((float(item["peak_utilization"]) for item in per_link.values()), default=0.0),
        "hottest_links": hottest_links,
    }


def _build_epoch_action_summary(schedule_history: list[dict[str, Any]], scheduler_debug_state: dict[str, Any]) -> dict[str, Any]:
    total_epoch_actions = sum(int(item.get("epoch_action_count", 0)) for item in schedule_history)
    active_epochs = [item for item in schedule_history if int(item.get("epoch_action_count", 0)) > 0]
    node_kind_counts: dict[str, int] = {}
    sample_actions: list[dict[str, Any]] = []
    for item in schedule_history:
        time_ms = float(item.get("time_ms", 0.0) or 0.0)
        solver_reports = ((item.get("metadata") or {}).get("solver_reports") or {})
        for report in solver_reports.values():
            for candidate in report.get("selected_candidates", []):
                node_kind = str(candidate.get("node_kind", "unknown"))
                node_kind_counts[node_kind] = node_kind_counts.get(node_kind, 0) + 1
                if len(sample_actions) < 8:
                    sample_actions.append(
                        {
                            "time_ms": time_ms,
                            "chunk_id": _candidate_chunk_id_from_replica(str(candidate.get("replica_id", ""))),
                            "current_node": candidate.get("current_node", ""),
                            "next_node": candidate.get("next_node", ""),
                            "ultimate_destination": candidate.get("ultimate_destination", ""),
                            "node_kind": node_kind,
                        }
                    )
    job_states = scheduler_debug_state.get("job_states", {})
    completed_replica_count = sum(len(job_state.get("completed_replica_ids", [])) for job_state in job_states.values())
    return {
        "total_epoch_actions": total_epoch_actions,
        "active_epoch_count": len(active_epochs),
        "first_active_time_ms": float(active_epochs[0]["time_ms"]) if active_epochs else 0.0,
        "last_active_time_ms": float(active_epochs[-1]["time_ms"]) if active_epochs else 0.0,
        "node_kind_counts": node_kind_counts,
        "completed_replica_count": completed_replica_count,
        "sample_actions": sample_actions,
    }


def _candidate_chunk_id_from_replica(replica_id: str) -> str:
    if "::" not in replica_id:
        return replica_id
    return replica_id.split("::", 1)[0]


def _extract_first_repetition_summary(summary_payload: dict[str, Any]) -> dict[str, Any]:
    repetitions = summary_payload.get("repetitions") or []
    return repetitions[0] if repetitions else {}


def _load_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_csv_if_exists(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))