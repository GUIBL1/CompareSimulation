from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.experiment.runner import ExperimentRunner


@dataclass
class TrialResult:
    trial_index: int
    params: dict[str, Any]
    score: float
    comm_mean_ms: float
    comm_std_ms: float
    planning_mean_ms: float
    completion_ratio_mean: float


PARAM_SPACE: dict[str, dict[str, Any]] = {
    "headroom_ratio": {"type": "float", "low": 0.02, "high": 0.15},
    "epsilon": {"type": "float", "low": 0.03, "high": 0.16},
    "gamma": {"type": "float", "low": 0.02, "high": 0.09},
    "stage1_max_iterations": {"type": "int", "low": 16, "high": 44},
    "stage2_max_iterations": {"type": "int", "low": 24, "high": 84},
    "stage2_binary_search_rounds": {"type": "int", "low": 16, "high": 40},
    "cross_path_ecmp_k": {"type": "int", "low": 2, "high": 6},
    "stage2_path_split_k": {"type": "int", "low": 2, "high": 6},
    "stage2_initial_max_paths": {"type": "int", "low": 6, "high": 16},
    "stage2_max_path_expansion": {"type": "int", "low": 16, "high": 40},
    "stage2_path_expansion_step": {"type": "int", "low": 2, "high": 10},
    "queue_wait_estimation_mode": {"type": "categorical", "choices": ["zero", "observed"]},
}


def _read_text(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    if not raw and default is not None:
        return default
    return raw


def _read_int(prompt: str, default: int) -> int:
    while True:
        raw = _read_text(prompt, str(default))
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
        print("请输入正整数。")


def _read_seeds(prompt: str, defaults: list[int]) -> list[int]:
    default_text = ",".join(str(item) for item in defaults)
    raw = _read_text(prompt, default_text)
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            pass
    return values or defaults


def _read_bool(prompt: str, default: bool) -> bool:
    default_text = "y" if default else "n"
    raw = _read_text(prompt + " (y/n)", default_text).lower()
    if raw in {"y", "yes", "1", "true"}:
        return True
    if raw in {"n", "no", "0", "false"}:
        return False
    return default


def _read_method(prompt: str, default: str = "bayes") -> str:
    while True:
        raw = _read_text(prompt + " (bayes/random)", default).lower()
        if raw in {"bayes", "random"}:
            return raw
        print("请输入 bayes 或 random。")


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


def _extract_metrics(summary: dict[str, Any]) -> tuple[float, float, float]:
    aggregate = summary.get("aggregate_metrics", {})
    repetitions = summary.get("repetitions", [])
    if not repetitions:
        return float("inf"), float("inf"), 0.0

    comm = float(aggregate.get("avg_crossweaver_communication_execution_time_ms", repetitions[0].get("completion_time_ms", float("inf"))))
    planning = float(aggregate.get("avg_crossweaver_scheduler_wall_time_ms", 0.0))

    total_jobs = float(repetitions[0].get("total_job_count", 0))
    completed_jobs = float(repetitions[0].get("completed_job_count", 0))
    ratio = completed_jobs / total_jobs if total_jobs > 0 else 0.0
    return comm, planning, ratio


def _sample_random(rng: random.Random) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, spec in PARAM_SPACE.items():
        if spec["type"] == "float":
            params[key] = round(rng.uniform(float(spec["low"]), float(spec["high"])), 5)
        elif spec["type"] == "int":
            params[key] = int(rng.randint(int(spec["low"]), int(spec["high"])))
        else:
            params[key] = rng.choice(list(spec["choices"]))
    return _normalize_params(params)


def _sample_bayes_like(rng: random.Random, history: list[TrialResult]) -> dict[str, Any]:
    if len(history) < 5:
        return _sample_random(rng)

    ranked = sorted(history, key=lambda item: item.score)
    top_count = max(3, int(len(ranked) * 0.25))
    top = ranked[:top_count]

    params: dict[str, Any] = {}
    for key, spec in PARAM_SPACE.items():
        if spec["type"] == "categorical":
            counts: dict[str, float] = {choice: 1.0 for choice in spec["choices"]}
            for item in top:
                counts[str(item.params[key])] += 2.0
            population = list(counts.keys())
            weights = [counts[item] for item in population]
            params[key] = rng.choices(population=population, weights=weights, k=1)[0]
            continue

        values = [float(item.params[key]) for item in top]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / max(len(values) - 1, 1)
        std = math.sqrt(max(variance, 1e-9))
        sampled = rng.gauss(mean, max(std, (float(spec["high"]) - float(spec["low"])) * 0.08))
        sampled = min(max(sampled, float(spec["low"])), float(spec["high"]))
        if spec["type"] == "int":
            params[key] = int(round(sampled))
        else:
            params[key] = round(float(sampled), 5)

    return _normalize_params(params)


def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    params = dict(params)
    params["stage2_initial_max_paths"] = max(4, int(params["stage2_initial_max_paths"]))
    params["stage2_path_expansion_step"] = max(1, int(params["stage2_path_expansion_step"]))
    params["stage2_max_path_expansion"] = max(
        int(params["stage2_initial_max_paths"]) + int(params["stage2_path_expansion_step"]),
        int(params["stage2_max_path_expansion"]),
    )
    params["stage2_path_split_k"] = max(1, min(int(params["stage2_path_split_k"]), int(params["cross_path_ecmp_k"]) + 1))
    return params


def _build_trial_config(base_cfg: dict[str, Any], params: dict[str, Any], seed: int, output_dir: str) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base_cfg))
    cfg.setdefault("scheduler", {}).setdefault("crossweaver", {})
    cfg["scheduler"]["type"] = "crossweaver"
    cfg["scheduler"]["crossweaver"].update(params)
    cfg.setdefault("simulation", {})["random_seed"] = seed
    cfg["simulation"]["repetitions"] = 1
    cfg.setdefault("metrics", {})["output_dir"] = output_dir
    return cfg


def _resolve_input_path(base_exp_path: Path, raw_path: str) -> str:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return str(candidate)
    direct = (base_exp_path.parent / candidate).resolve()
    if direct.exists():
        return str(direct)
    workspace_relative = (ROOT / candidate).resolve()
    return str(workspace_relative)


def _evaluate_trial(
    base_cfg: dict[str, Any],
    base_exp_path: Path,
    trial_index: int,
    params: dict[str, Any],
    seeds: list[int],
    work_dir: Path,
) -> TrialResult:
    comm_values: list[float] = []
    planning_values: list[float] = []
    ratios: list[float] = []

    trial_dir = work_dir / f"trial_{trial_index:03d}"
    trial_dir.mkdir(parents=True, exist_ok=True)

    for idx, seed in enumerate(seeds):
        out_dir = trial_dir / f"seed_{seed}"
        out_dir_rel = str(out_dir.relative_to(ROOT)) if out_dir.is_relative_to(ROOT) else str(out_dir)
        cfg = _build_trial_config(base_cfg, params, seed, out_dir_rel)
        cfg.setdefault("inputs", {})
        cfg["inputs"]["topology_file"] = _resolve_input_path(base_exp_path, str(cfg["inputs"].get("topology_file", "")))
        cfg["inputs"]["workload_file"] = _resolve_input_path(base_exp_path, str(cfg["inputs"].get("workload_file", "")))
        temp_exp_path = trial_dir / f"exp_seed_{idx}.yaml"
        _save_yaml(temp_exp_path, cfg)

        runner = ExperimentRunner(temp_exp_path)
        run_result = runner.export_results()
        summary_path = run_result.output_dir / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        comm, planning, ratio = _extract_metrics(summary)
        comm_values.append(comm)
        planning_values.append(planning)
        ratios.append(ratio)

    comm_mean = sum(comm_values) / len(comm_values)
    planning_mean = sum(planning_values) / len(planning_values)
    ratio_mean = sum(ratios) / len(ratios)
    comm_std = 0.0
    if len(comm_values) > 1:
        mean = comm_mean
        comm_std = math.sqrt(sum((value - mean) ** 2 for value in comm_values) / (len(comm_values) - 1))

    penalty = 0.0
    if ratio_mean < 1.0:
        penalty += (1.0 - ratio_mean) * 1_000_000.0
    score = comm_mean + 0.25 * planning_mean + 0.15 * comm_std + penalty

    return TrialResult(
        trial_index=trial_index,
        params=params,
        score=score,
        comm_mean_ms=comm_mean,
        comm_std_ms=comm_std,
        planning_mean_ms=planning_mean,
        completion_ratio_mean=ratio_mean,
    )


def _resolve_path(path_text: str) -> Path:
    candidate = Path(path_text).expanduser()
    if not candidate.is_absolute():
        candidate = (ROOT / candidate).resolve()
    return candidate


def main() -> None:
    print("CrossWeaver 参数搜索（交互式）")
    print("- 输入一个 crossweaver 实验 YAML")
    print("- 脚本会基于其 topology/workload 做参数搜索")
    print("- 最后可将最优参数回写到该实验文件")
    print()

    exp_text = _read_text("请输入 crossweaver 实验文件路径", "configs/experiment/inter_dc_triple_parallel_heavy_crossweaver.yaml")
    exp_path = _resolve_path(exp_text)
    if not exp_path.exists():
        raise FileNotFoundError(f"实验文件不存在: {exp_path}")

    base_cfg = _load_yaml(exp_path)
    scheduler_type = str(base_cfg.get("scheduler", {}).get("type", "")).lower()
    if scheduler_type != "crossweaver":
        raise ValueError("输入实验文件的 scheduler.type 必须是 crossweaver")

    base_seed = int(base_cfg.get("simulation", {}).get("random_seed", 42))
    default_seeds = [base_seed, base_seed + 17, base_seed + 29]

    trials = _read_int("搜索 trial 数", 20)
    seeds = _read_seeds("评估 seeds（逗号分隔）", default_seeds)
    method = _read_method("搜索方式")
    apply_best = _read_bool("是否把最优参数直接写回原实验文件", True)

    topology_file = base_cfg.get("inputs", {}).get("topology_file", "")
    workload_file = base_cfg.get("inputs", {}).get("workload_file", "")
    print(f"\n拓扑: {topology_file}")
    print(f"工作负载: {workload_file}")
    print(f"trial 数: {trials}, seeds: {seeds}, 方式: {method}\n")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    search_root = ROOT / "results" / "crossweaver_param_search"
    search_root.mkdir(parents=True, exist_ok=True)
    run_dir = search_root / f"{exp_path.stem}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(base_seed)
    history: list[TrialResult] = []

    for trial in range(1, trials + 1):
        if method == "random":
            params = _sample_random(rng)
        else:
            params = _sample_bayes_like(rng, history)

        result = _evaluate_trial(
            base_cfg=base_cfg,
            base_exp_path=exp_path,
            trial_index=trial,
            params=params,
            seeds=seeds,
            work_dir=run_dir,
        )
        history.append(result)

        print(
            f"trial={trial:02d} score={result.score:.3f} "
            f"comm={result.comm_mean_ms:.3f}±{result.comm_std_ms:.3f} "
            f"plan={result.planning_mean_ms:.3f} ratio={result.completion_ratio_mean:.3f}"
        )

    best = min(history, key=lambda item: item.score)

    report = {
        "experiment_file": str(exp_path),
        "topology_file": topology_file,
        "workload_file": workload_file,
        "search_method": method,
        "trials": trials,
        "seeds": seeds,
        "best": {
            "trial_index": best.trial_index,
            "score": best.score,
            "comm_mean_ms": best.comm_mean_ms,
            "comm_std_ms": best.comm_std_ms,
            "planning_mean_ms": best.planning_mean_ms,
            "completion_ratio_mean": best.completion_ratio_mean,
            "params": best.params,
        },
        "all_trials": [
            {
                "trial_index": item.trial_index,
                "score": item.score,
                "comm_mean_ms": item.comm_mean_ms,
                "comm_std_ms": item.comm_std_ms,
                "planning_mean_ms": item.planning_mean_ms,
                "completion_ratio_mean": item.completion_ratio_mean,
                "params": item.params,
            }
            for item in sorted(history, key=lambda value: value.score)
        ],
    }
    report_path = run_dir / "search_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== 搜索完成 ===")
    print(f"best trial: {best.trial_index}")
    print(f"best score: {best.score:.3f}")
    print(f"best comm mean: {best.comm_mean_ms:.3f} ms")
    print(f"best planning mean: {best.planning_mean_ms:.3f} ms")
    print(f"best completion ratio: {best.completion_ratio_mean:.3f}")
    print(f"report: {report_path}")
    print("best params:")
    print(json.dumps(best.params, indent=2, ensure_ascii=False))

    if apply_best:
        cfg = _load_yaml(exp_path)
        cfg.setdefault("scheduler", {}).setdefault("crossweaver", {})
        cfg["scheduler"]["crossweaver"].update(best.params)
        _save_yaml(exp_path, cfg)
        print(f"\n已回写最优参数到: {exp_path}")
    else:
        print("\n未回写原实验文件（按你的选择保留不改）。")


if __name__ == "__main__":
    main()
