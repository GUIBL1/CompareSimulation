from __future__ import annotations

import json
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.experiment.runner import ExperimentRunner


@dataclass
class TrialResult:
    trial_index: int
    params: dict[str, Any]
    vector: list[float]
    score: float
    comm_mean_ms: float
    comm_std_ms: float
    planning_mean_ms: float
    completion_ratio_mean: float


PARAM_SPACE: dict[str, dict[str, Any]] = {
    "headroom_ratio": {"type": "float", "low": 0.02, "high": 0.15},
    "epsilon": {"type": "float", "low": 0.03, "high": 0.18},
    "gamma": {"type": "float", "low": 0.01, "high": 0.12},
    "stage1_max_iterations": {"type": "int", "low": 16, "high": 56},
    "stage1_binary_search_rounds": {"type": "int", "low": 24, "high": 56},
    "stage1_backoff_ratio": {"type": "float", "low": 0.75, "high": 0.98},
    "stage1_backoff_max_rounds": {"type": "int", "low": 2, "high": 8},
    "stage1_safety_margin_ratio": {"type": "float", "low": 0.0, "high": 0.25},
    "stage1_rate_smoothing_alpha": {"type": "float", "low": 0.4, "high": 1.0},
    "stage1_rate_change_cap_ratio": {"type": "float", "low": 0.2, "high": 1.5},
    "stage2_max_iterations": {"type": "int", "low": 24, "high": 96},
    "stage2_binary_search_rounds": {"type": "int", "low": 16, "high": 48},
    "cross_path_ecmp_k": {"type": "int", "low": 2, "high": 8},
    "stage2_path_split_k": {"type": "int", "low": 2, "high": 8},
    "stage2_initial_max_paths": {"type": "int", "low": 6, "high": 20},
    "stage2_max_path_expansion": {"type": "int", "low": 12, "high": 48},
    "stage2_path_expansion_step": {"type": "int", "low": 2, "high": 12},
    "stage2_softmin_temperature": {"type": "float", "low": 0.05, "high": 1.2},
    "wcmp_update_threshold_l1": {"type": "float", "low": 0.0, "high": 0.5},
    "queue_wait_estimation_mode": {"type": "categorical", "choices": ["zero", "observed"]},
}

PARAM_ORDER = list(PARAM_SPACE.keys())


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

    first_rep = repetitions[0]
    comm = float(
        aggregate.get(
            "avg_crossweaver_communication_execution_time_ms",
            first_rep.get("crossweaver_communication_execution_time_ms", first_rep.get("completion_time_ms", float("inf"))),
        )
    )
    planning = float(
        aggregate.get(
            "avg_crossweaver_scheduler_wall_time_ms",
            first_rep.get("crossweaver_scheduler_wall_time_ms", 0.0),
        )
    )

    total_jobs = float(aggregate.get("avg_total_job_count", first_rep.get("total_job_count", 0.0)))
    completed_jobs = float(aggregate.get("avg_completed_job_count", first_rep.get("completed_job_count", 0.0)))
    ratio = completed_jobs / total_jobs if total_jobs > 0 else 0.0
    return comm, planning, ratio


def _sample_random(rng: random.Random) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in PARAM_ORDER:
        spec = PARAM_SPACE[key]
        if spec["type"] == "float":
            params[key] = round(rng.uniform(float(spec["low"]), float(spec["high"])), 6)
        elif spec["type"] == "int":
            params[key] = int(rng.randint(int(spec["low"]), int(spec["high"])))
        else:
            params[key] = rng.choice(list(spec["choices"]))
    return _normalize_params(params)


def _vector_from_params(params: dict[str, Any]) -> list[float]:
    vector: list[float] = []
    for key in PARAM_ORDER:
        spec = PARAM_SPACE[key]
        value = params[key]
        if spec["type"] == "categorical":
            choices = list(spec["choices"])
            if len(choices) <= 1:
                vector.append(0.0)
            else:
                index = choices.index(str(value))
                vector.append(index / (len(choices) - 1))
            continue
        low = float(spec["low"])
        high = float(spec["high"])
        if high <= low:
            vector.append(0.0)
            continue
        vector.append((float(value) - low) / (high - low))
    return [min(1.0, max(0.0, item)) for item in vector]


def _params_from_vector(vector: list[float]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for index, key in enumerate(PARAM_ORDER):
        spec = PARAM_SPACE[key]
        value = min(1.0, max(0.0, float(vector[index])))
        if spec["type"] == "categorical":
            choices = list(spec["choices"])
            if len(choices) <= 1:
                params[key] = choices[0]
            else:
                choice_index = int(round(value * (len(choices) - 1)))
                choice_index = min(max(choice_index, 0), len(choices) - 1)
                params[key] = choices[choice_index]
            continue
        low = float(spec["low"])
        high = float(spec["high"])
        decoded = low + value * (high - low)
        if spec["type"] == "int":
            params[key] = int(round(decoded))
        else:
            params[key] = round(decoded, 6)
    return _normalize_params(params)


def _kernel_rbf(x: list[float], y: list[float], length_scale: float = 0.33) -> float:
    squared_distance = 0.0
    for x_i, y_i in zip(x, y, strict=False):
        diff = x_i - y_i
        squared_distance += diff * diff
    return math.exp(-0.5 * squared_distance / max(length_scale * length_scale, 1e-12))


def _cholesky_decompose(matrix: list[list[float]]) -> list[list[float]]:
    size = len(matrix)
    lower = [[0.0 for _ in range(size)] for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            total = matrix[i][j]
            for k in range(j):
                total -= lower[i][k] * lower[j][k]
            if i == j:
                if total <= 0.0:
                    raise ValueError("Matrix is not positive definite")
                lower[i][j] = math.sqrt(total)
            else:
                lower[i][j] = total / max(lower[j][j], 1e-12)
    return lower


def _forward_substitution(lower: list[list[float]], b: list[float]) -> list[float]:
    size = len(lower)
    y = [0.0] * size
    for i in range(size):
        total = b[i]
        for j in range(i):
            total -= lower[i][j] * y[j]
        y[i] = total / max(lower[i][i], 1e-12)
    return y


def _backward_substitution(lower: list[list[float]], y: list[float]) -> list[float]:
    size = len(lower)
    x = [0.0] * size
    for i in range(size - 1, -1, -1):
        total = y[i]
        for j in range(i + 1, size):
            total -= lower[j][i] * x[j]
        x[i] = total / max(lower[i][i], 1e-12)
    return x


def _cholesky_solve(lower: list[list[float]], b: list[float]) -> list[float]:
    y = _forward_substitution(lower, b)
    return _backward_substitution(lower, y)


def _normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _expected_improvement(mean: float, std: float, best: float, xi: float = 0.01) -> float:
    if std <= 1e-12:
        return 0.0
    improvement = best - mean - xi
    z_score = improvement / std
    return improvement * _normal_cdf(z_score) + std * _normal_pdf(z_score)


def _fit_gaussian_process(history: list[TrialResult]) -> dict[str, Any] | None:
    if len(history) < 3:
        return None

    x_data = [trial.vector for trial in history]
    y_raw = [float(trial.score) for trial in history]
    y_mean = sum(y_raw) / len(y_raw)
    y_var = sum((item - y_mean) ** 2 for item in y_raw) / max(1, len(y_raw) - 1)
    y_std = math.sqrt(max(y_var, 1e-12))
    y_data = [(item - y_mean) / y_std for item in y_raw]

    length_scale = 0.33
    noise = 1e-6
    max_attempts = 8

    for _ in range(max_attempts):
        kernel_matrix = []
        for i in range(len(x_data)):
            row = []
            for j in range(len(x_data)):
                value = _kernel_rbf(x_data[i], x_data[j], length_scale=length_scale)
                if i == j:
                    value += noise
                row.append(value)
            kernel_matrix.append(row)
        try:
            lower = _cholesky_decompose(kernel_matrix)
            alpha = _cholesky_solve(lower, y_data)
            return {
                "x_data": x_data,
                "y_data": y_data,
                "y_mean": y_mean,
                "y_std": y_std,
                "best_y": min(y_data),
                "length_scale": length_scale,
                "noise": noise,
                "lower": lower,
                "alpha": alpha,
            }
        except ValueError:
            noise *= 10.0

    return None


def _predict_gaussian_process(model: dict[str, Any], x_candidate: list[float]) -> tuple[float, float]:
    x_data: list[list[float]] = model["x_data"]
    lower: list[list[float]] = model["lower"]
    alpha: list[float] = model["alpha"]
    length_scale = float(model["length_scale"])
    noise = float(model["noise"])

    k_vector = [_kernel_rbf(x_candidate, x_train, length_scale=length_scale) for x_train in x_data]
    mean = sum(k_item * alpha_item for k_item, alpha_item in zip(k_vector, alpha, strict=False))
    v_vector = _forward_substitution(lower, k_vector)
    variance = max(_kernel_rbf(x_candidate, x_candidate, length_scale=length_scale) + noise - sum(item * item for item in v_vector), 1e-12)
    return mean, variance


def _sample_bayesian(rng: random.Random, history: list[TrialResult]) -> dict[str, Any]:
    dimension = len(PARAM_ORDER)
    warmup_trials = max(8, 2 * dimension)
    if len(history) < warmup_trials:
        return _sample_random(rng)

    model = _fit_gaussian_process(history)
    if model is None:
        return _sample_random(rng)

    candidate_vectors: list[list[float]] = []
    random_pool_size = max(800, 40 * dimension)
    for _ in range(random_pool_size):
        params = _sample_random(rng)
        candidate_vectors.append(_vector_from_params(params))

    top_history = sorted(history, key=lambda item: item.score)[: max(4, len(history) // 4)]
    for trial in top_history:
        for _ in range(16):
            perturb = []
            for value in trial.vector:
                sampled = rng.gauss(value, 0.12)
                perturb.append(min(1.0, max(0.0, sampled)))
            candidate_vectors.append(perturb)

    best_vector = None
    best_score = -1.0
    history_vectors = [trial.vector for trial in history]
    for candidate in candidate_vectors:
        mean, variance = _predict_gaussian_process(model, candidate)
        std = math.sqrt(max(variance, 1e-12))
        ei = _expected_improvement(mean, std, best=float(model["best_y"]), xi=0.01)

        min_distance = min(
            math.sqrt(sum((a - b) ** 2 for a, b in zip(candidate, history_vector, strict=False)))
            for history_vector in history_vectors
        )
        score = ei + 0.01 * min_distance
        if score > best_score:
            best_score = score
            best_vector = candidate

    if best_vector is None:
        return _sample_random(rng)
    return _params_from_vector(best_vector)


def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    params = dict(params)

    params["headroom_ratio"] = float(min(max(params["headroom_ratio"], 0.0), 0.25))
    params["epsilon"] = float(min(max(params["epsilon"], 1e-4), 1.0))
    params["gamma"] = float(min(max(params["gamma"], 1e-4), 1.0))

    params["stage1_max_iterations"] = max(1, int(params["stage1_max_iterations"]))
    params["stage1_binary_search_rounds"] = max(8, int(params["stage1_binary_search_rounds"]))
    params["stage1_backoff_ratio"] = float(min(max(params["stage1_backoff_ratio"], 0.1), 0.999))
    params["stage1_backoff_max_rounds"] = max(1, int(params["stage1_backoff_max_rounds"]))
    params["stage1_safety_margin_ratio"] = float(min(max(params["stage1_safety_margin_ratio"], 0.0), 0.5))
    params["stage1_rate_smoothing_alpha"] = float(min(max(params["stage1_rate_smoothing_alpha"], 0.0), 1.0))
    params["stage1_rate_change_cap_ratio"] = float(max(params["stage1_rate_change_cap_ratio"], 0.0))

    params["stage2_max_iterations"] = max(1, int(params["stage2_max_iterations"]))
    params["stage2_binary_search_rounds"] = max(8, int(params["stage2_binary_search_rounds"]))
    params["cross_path_ecmp_k"] = max(1, int(params["cross_path_ecmp_k"]))
    params["stage2_path_split_k"] = max(1, int(params["stage2_path_split_k"]))
    params["stage2_initial_max_paths"] = max(params["stage2_path_split_k"], int(params["stage2_initial_max_paths"]))
    params["stage2_path_expansion_step"] = max(1, int(params["stage2_path_expansion_step"]))
    params["stage2_max_path_expansion"] = max(
        int(params["stage2_max_path_expansion"]),
        params["stage2_initial_max_paths"] + params["stage2_path_expansion_step"],
    )
    params["stage2_softmin_temperature"] = float(min(max(params["stage2_softmin_temperature"], 1e-4), 5.0))
    params["wcmp_update_threshold_l1"] = float(min(max(params["wcmp_update_threshold_l1"], 0.0), 2.0))

    mode = str(params.get("queue_wait_estimation_mode", "zero"))
    if mode not in {"zero", "observed"}:
        mode = "zero"
    params["queue_wait_estimation_mode"] = mode

    for key, value in list(params.items()):
        if isinstance(value, float):
            params[key] = round(value, 6)

    return params


def _build_trial_config(base_cfg: dict[str, Any], params: dict[str, Any], seed: int, output_dir: str) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base_cfg))
    cfg.setdefault("scheduler", {}).setdefault("crossweaver", {})
    cfg["scheduler"]["type"] = "crossweaver"
    cfg["scheduler"]["crossweaver"].update(params)
    cfg.setdefault("simulation", {})["random_seed"] = seed
    cfg["simulation"]["repetitions"] = 1
    cfg.setdefault("metrics", {})["output_dir"] = output_dir
    cfg["metrics"].setdefault("export_json", True)
    cfg["metrics"].setdefault("export_csv", True)
    cfg["metrics"].setdefault("export_trace", True)
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
        comm_std = math.sqrt(sum((value - comm_mean) ** 2 for value in comm_values) / (len(comm_values) - 1))

    penalty = 0.0
    if ratio_mean < 1.0:
        penalty += (1.0 - ratio_mean) * 1_000_000.0
    score = comm_mean + 0.35 * planning_mean + 0.15 * comm_std + penalty

    return TrialResult(
        trial_index=trial_index,
        params=params,
        vector=_vector_from_params(params),
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


def _params_signature(params: dict[str, Any]) -> tuple[Any, ...]:
    items: list[Any] = []
    for key in PARAM_ORDER:
        value = params.get(key)
        if isinstance(value, float):
            items.append((key, round(value, 6)))
        else:
            items.append((key, value))
    return tuple(items)


def main() -> None:
    print("CrossWeaver 参数搜索（交互式）")
    print("- 输入一个 crossweaver 实验 YAML")
    print("- 脚本会基于其 topology/workload 做参数搜索")
    print("- bayes 采用 GP + EI 的贝叶斯优化")
    print("- 最后可将最优参数回写到该实验文件")
    print()

    exp_text = _read_text("请输入 crossweaver 实验文件路径", "configs/experiment/inter_dc_triple_heavy_crossweaver.yaml")
    exp_path = _resolve_path(exp_text)
    if not exp_path.exists():
        raise FileNotFoundError(f"实验文件不存在: {exp_path}")

    base_cfg = _load_yaml(exp_path)
    scheduler_type = str(base_cfg.get("scheduler", {}).get("type", "")).lower()
    if scheduler_type != "crossweaver":
        raise ValueError("输入实验文件的 scheduler.type 必须是 crossweaver")

    base_seed = int(base_cfg.get("simulation", {}).get("random_seed", 42))
    default_seeds = [base_seed, base_seed + 17, base_seed + 29, base_seed + 43]

    trials = _read_int("搜索 trial 数", 30)
    seeds = _read_seeds("评估 seeds（逗号分隔）", default_seeds)
    method = _read_method("搜索方式")
    apply_best = _read_bool("是否把最优参数直接写回原实验文件", True)

    topology_file = base_cfg.get("inputs", {}).get("topology_file", "")
    workload_file = base_cfg.get("inputs", {}).get("workload_file", "")
    print(f"\n拓扑: {topology_file}")
    print(f"工作负载: {workload_file}")
    print(f"trial 数: {trials}, seeds: {seeds}, 方式: {method}")
    print(f"搜索维度: {len(PARAM_ORDER)}\n")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    search_root = ROOT / "results" / "crossweaver_param_search"
    search_root.mkdir(parents=True, exist_ok=True)
    run_dir = search_root / f"{exp_path.stem}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(base_seed)
    history: list[TrialResult] = []
    visited_signatures: set[tuple[Any, ...]] = set()

    for trial in range(1, trials + 1):
        if method == "random":
            params = _sample_random(rng)
        else:
            params = _sample_bayesian(rng, history)

        signature = _params_signature(params)
        retry_guard = 0
        while signature in visited_signatures and retry_guard < 50:
            params = _sample_random(rng)
            signature = _params_signature(params)
            retry_guard += 1
        visited_signatures.add(signature)

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
            f"trial={trial:03d} score={result.score:.3f} "
            f"comm={result.comm_mean_ms:.3f}±{result.comm_std_ms:.3f} "
            f"plan={result.planning_mean_ms:.3f} ratio={result.completion_ratio_mean:.3f}"
        )

    best = min(history, key=lambda item: item.score)

    report = {
        "experiment_file": str(exp_path),
        "topology_file": topology_file,
        "workload_file": workload_file,
        "search_method": method,
        "optimizer": "gaussian_process_expected_improvement" if method == "bayes" else "random_search",
        "trials": trials,
        "seeds": seeds,
        "search_space": PARAM_SPACE,
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
