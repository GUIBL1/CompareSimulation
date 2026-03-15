from __future__ import annotations

import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.config.loaders import load_experiment_config
from simulator.config.loaders import load_topology_config
from simulator.config.loaders import load_workload_config
from simulator.schedulers import TECCLHighsSolveConfig
from simulator.schedulers import TECCLMILPBuildConfig
from simulator.schedulers import build_teccl_milp_model
from simulator.schedulers import build_teccl_model_input
from simulator.schedulers import solve_teccl_milp
from simulator.topology.builder import build_topology
from simulator.workload.models import build_unified_job


DEFAULT_EPOCH_SIZE_CANDIDATES = [15, 20, 25, 50, 100, 200, 500, 1000]
DEFAULT_MAX_EPOCH_COUNT_CANDIDATES = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def _parse_numeric_list(raw: str, cast_type: type[int] | type[float], field_name: str) -> list[int] | list[float]:
    values: list[int] | list[float] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        value = cast_type(text)
        if value <= 0:
            raise ValueError(f"{field_name} 中的值必须为正数")
        values.append(value)
    if not values:
        raise ValueError(f"{field_name} 至少需要一个值")
    return sorted(set(values))


def _prompt_existing_file_path(prompt_text: str) -> Path:
    while True:
        raw = input(f"{prompt_text}: ").strip()
        if not raw:
            print("输入不能为空，请重新输入。")
            continue
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            print(f"文件不存在: {path}")
            continue
        return path


def _prompt_number_list(
    *,
    prompt_text: str,
    default_values: list[int] | list[float],
    cast_type: type[int] | type[float],
    field_name: str,
) -> list[int] | list[float]:
    default_text = ",".join(str(v) for v in default_values)
    while True:
        raw = input(f"{prompt_text}（逗号分隔，回车默认 {default_text}）: ").strip()
        if not raw:
            return list(default_values)
        try:
            return _parse_numeric_list(raw, cast_type=cast_type, field_name=field_name)
        except ValueError as error:
            print(f"输入无效: {error}")


def _prompt_optional_int(prompt_text: str, default_value: int | None) -> int | None:
    while True:
        default_text = str(default_value) if default_value is not None else "None"
        raw = input(f"{prompt_text}（回车默认 {default_text}）: ").strip()
        if not raw:
            return default_value
        try:
            value = int(raw)
        except ValueError:
            print("请输入整数。")
            continue
        if value <= 0:
            print("请输入正整数。")
            continue
        return value


def _prompt_optional_float(prompt_text: str, default_value: float | None) -> float | None:
    while True:
        default_text = str(default_value) if default_value is not None else "None"
        raw = input(f"{prompt_text}（回车默认 {default_text}）: ").strip()
        if not raw:
            return default_value
        try:
            value = float(raw)
        except ValueError:
            print("请输入数字。")
            continue
        if value < 0:
            print("请输入大于等于 0 的数值。")
            continue
        return value


def _prompt_yes_no(prompt_text: str, default_yes: bool = False) -> bool:
    default_text = "y" if default_yes else "n"
    while True:
        raw = input(f"{prompt_text}（y/n，默认 {default_text}）: ").strip().lower()
        if not raw:
            return default_yes
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("请输入 y 或 n。")


def _scan_candidate(
    *,
    candidate_epoch_size_ms: float,
    candidate_max_epoch_count: int,
    topology: Any,
    jobs: list[Any],
    solver_time_ms: int,
    solver_threads: int | None,
    objective_mode: str,
    switch_buffer_policy: str,
    mip_gap: float | None,
) -> dict[str, Any]:
    build_start = perf_counter()
    model_input = build_teccl_model_input(
        topology=topology,
        jobs=jobs,
        epoch_size_ms=candidate_epoch_size_ms,
        planning_horizon_epochs=candidate_max_epoch_count,
    )
    build_result = build_teccl_milp_model(
        model_input=model_input,
        config=TECCLMILPBuildConfig(
            enforce_integrality=False,
            objective_mode=objective_mode,
            switch_buffer_policy=switch_buffer_policy,
        ),
    )
    build_time_ms = (perf_counter() - build_start) * 1000.0

    solve_start = perf_counter()
    solve_result = solve_teccl_milp(
        build_result=build_result,
        config=TECCLHighsSolveConfig(
            max_solver_time_ms=solver_time_ms,
            mip_gap=mip_gap,
            solver_threads=solver_threads,
            log_to_console=False,
            extract_all_variable_values=False,
        ),
    )
    solve_time_ms = (perf_counter() - solve_start) * 1000.0

    model_status = str(solve_result.model_status)
    has_usable_solution = bool(solve_result.has_usable_solution)
    infeasible_flag = "infeasible" in model_status.lower()
    feasible = has_usable_solution and not infeasible_flag

    summary = build_result.summary
    input_summary = model_input.summary
    return {
        "epoch_size_ms": candidate_epoch_size_ms,
        "max_epoch_count": candidate_max_epoch_count,
        "planning_horizon_ms": candidate_max_epoch_count * candidate_epoch_size_ms,
        "feasible": feasible,
        "model_status": model_status,
        "has_usable_solution": has_usable_solution,
        "solve_mip_gap": solve_result.mip_gap,
        "objective_value": solve_result.objective_value,
        "best_bound": solve_result.best_bound,
        "build_time_ms": build_time_ms,
        "solve_time_ms": solve_time_ms,
        "scale": {
            "variable_count": int(summary["variable_count"]),
            "constraint_count": int(summary["constraint_count"]),
            "non_zero_count": int(summary["non_zero_count"]),
            "flow_variable_count": int(summary["flow_variable_count"]),
            "buffer_variable_count": int(summary["buffer_variable_count"]),
            "receive_variable_count": int(summary["receive_variable_count"]),
            "commodity_count": int(input_summary["commodity_count"]),
            "destination_pair_count": int(input_summary["destination_pair_count"]),
            "directed_edge_count": int(input_summary["directed_edge_count"]),
            "node_count": int(input_summary["node_count"]),
            "gpu_node_count": int(input_summary["gpu_node_count"]),
            "total_demand_mb": float(input_summary["total_demand_mb"]),
        },
    }


def main() -> None:
    print("TECCL 可行性参数扫描脚本")
    print("功能：扫描 epoch_size_ms × max_epoch_count 的组合，验证可行性并输出最小可行时域与对应规模。")

    experiment_path = _prompt_existing_file_path("请输入 TECCL 实验配置文件路径")
    experiment = load_experiment_config(experiment_path)
    if experiment.scheduler.type != "teccl":
        raise SystemExit(f"仅支持 teccl 实验配置，当前为: {experiment.scheduler.type}")

    topology = build_topology(load_topology_config(experiment.inputs.topology_file))
    workload = load_workload_config(experiment.inputs.workload_file)
    jobs = [build_unified_job(job) for job in workload.jobs]
    strategy = dict(experiment.scheduler.teccl)

    epoch_size_candidates = _prompt_number_list(
        prompt_text="请输入 epoch_size_ms 候选值",
        default_values=DEFAULT_EPOCH_SIZE_CANDIDATES,
        cast_type=float,
        field_name="epoch_size_ms",
    )
    max_epoch_count_candidates = _prompt_number_list(
        prompt_text="请输入 max_epoch_count 候选值",
        default_values=DEFAULT_MAX_EPOCH_COUNT_CANDIDATES,
        cast_type=int,
        field_name="max_epoch_count",
    )

    configured_solver_time = strategy.get("max_solver_time_ms")
    default_solver_time_ms = int(configured_solver_time) if configured_solver_time else 120000
    solver_time_ms = _prompt_optional_int("请输入 max_solver_time_ms", default_solver_time_ms)
    if solver_time_ms is None:
        solver_time_ms = default_solver_time_ms

    configured_mip_gap = float(strategy["mip_gap"]) if strategy.get("mip_gap") is not None else None
    mip_gap = _prompt_optional_float("请输入 mip_gap", configured_mip_gap)

    configured_threads = strategy.get("solver_threads")
    default_solver_threads = int(configured_threads) if configured_threads else None
    solver_threads = _prompt_optional_int("请输入 solver_threads", default_solver_threads)

    objective_mode = str(strategy.get("objective_mode", "weighted_early_completion"))
    switch_buffer_policy = str(strategy.get("switch_buffer_policy", "zero"))

    export_to_file = _prompt_yes_no("是否输出为文件", default_yes=False)
    output_json_path: Path | None = None
    if export_to_file:
        raw_output_path = input("请输入输出文件路径: ").strip()
        if not raw_output_path:
            raise SystemExit("输出文件路径不能为空")
        output_json_path = Path(raw_output_path).expanduser().resolve()

    print(f"开始扫描: {experiment_path}")
    print(f"epoch_size_ms 候选: {epoch_size_candidates}")
    print(f"max_epoch_count 候选: {max_epoch_count_candidates}")

    scan_results: list[dict[str, Any]] = []
    for epoch_size_ms in epoch_size_candidates:
        print(f"[scan] epoch_size_ms={epoch_size_ms}")
        for max_epoch_count in max_epoch_count_candidates:
            print(f"  -> max_epoch_count={max_epoch_count} ...")
            try:
                result = _scan_candidate(
                    candidate_epoch_size_ms=float(epoch_size_ms),
                    candidate_max_epoch_count=int(max_epoch_count),
                    topology=topology,
                    jobs=jobs,
                    solver_time_ms=int(solver_time_ms),
                    solver_threads=solver_threads,
                    objective_mode=objective_mode,
                    switch_buffer_policy=switch_buffer_policy,
                    mip_gap=mip_gap,
                )
            except Exception as error:  # noqa: BLE001
                result = {
                    "epoch_size_ms": float(epoch_size_ms),
                    "max_epoch_count": int(max_epoch_count),
                    "planning_horizon_ms": float(epoch_size_ms) * int(max_epoch_count),
                    "feasible": False,
                    "model_status": f"Error: {error}",
                    "has_usable_solution": False,
                    "solve_mip_gap": None,
                    "objective_value": None,
                    "best_bound": None,
                    "build_time_ms": 0.0,
                    "solve_time_ms": 0.0,
                    "scale": None,
                }
            scan_results.append(result)
            vars_text = result["scale"]["variable_count"] if result["scale"] else "-"
            cons_text = result["scale"]["constraint_count"] if result["scale"] else "-"
            print(
                f"     status={result['model_status']}, feasible={result['feasible']}, "
                f"vars={vars_text}, cons={cons_text}"
            )

    feasible_results = [item for item in scan_results if item["feasible"]]
    minimal_feasible = (
        min(
            feasible_results,
            key=lambda item: (float(item["planning_horizon_ms"]), int(item["max_epoch_count"]), float(item["epoch_size_ms"])),
        )
        if feasible_results
        else None
    )

    minimal_feasible_by_epoch_size: list[dict[str, Any]] = []
    for epoch_size_ms in epoch_size_candidates:
        candidates = [
            item
            for item in feasible_results
            if float(item["epoch_size_ms"]) == float(epoch_size_ms)
        ]
        if not candidates:
            minimal_feasible_by_epoch_size.append(
                {
                    "epoch_size_ms": float(epoch_size_ms),
                    "minimal_feasible": None,
                }
            )
            continue
        best_for_epoch_size = min(candidates, key=lambda item: int(item["max_epoch_count"]))
        minimal_feasible_by_epoch_size.append(
            {
                "epoch_size_ms": float(epoch_size_ms),
                "minimal_feasible": best_for_epoch_size,
            }
        )

    output_payload = {
        "experiment_file": str(experiment_path),
        "experiment_name": experiment.meta.name,
        "topology_file": str(experiment.inputs.topology_file),
        "workload_file": str(experiment.inputs.workload_file),
        "scan_epoch_size_ms_candidates": epoch_size_candidates,
        "scan_max_epoch_count_candidates": max_epoch_count_candidates,
        "solver_time_ms": int(solver_time_ms),
        "solver_threads": solver_threads,
        "mip_gap": mip_gap,
        "scan_results": scan_results,
        "minimal_feasible_by_epoch_size": minimal_feasible_by_epoch_size,
        "minimal_feasible": minimal_feasible,
    }

    print("\n=== 可行性扫描汇总 ===")
    if minimal_feasible is None:
        print("未找到可行时域（在当前候选和超时限制下）。")
    else:
        scale = minimal_feasible["scale"]
        print(
            "最小可行参数组合: "
            f"epoch_size_ms={minimal_feasible['epoch_size_ms']}, "
            f"max_epoch_count={minimal_feasible['max_epoch_count']}"
        )
        print(f"对应时域(ms): {minimal_feasible['planning_horizon_ms']}")
        print(
            "对应规模: "
            f"vars={scale['variable_count']}, cons={scale['constraint_count']}, nnz={scale['non_zero_count']}, "
            f"commodities={scale['commodity_count']}, dst_pairs={scale['destination_pair_count']}"
        )

    if output_json_path is None:
        print("\n=== JSON 结果 ===")
        print(json.dumps(output_payload, indent=2, ensure_ascii=False))
    else:
        output_path = output_json_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n已写出结果文件: {output_path}")


if __name__ == "__main__":
    main()
