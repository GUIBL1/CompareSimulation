from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from time import perf_counter
from typing import Any

from simulator.schedulers.teccl_milp_builder import TECCLMILPBuildResult

try:
	import highspy
except ImportError:  # pragma: no cover - handled by runtime validation
	highspy = None


class TECCLHighsBackendError(RuntimeError):
	"""Raised when the HiGHS backend cannot solve the TE-CCL model."""


@dataclass(slots=True)
class TECCLHighsSolveConfig:
	max_solver_time_ms: int | None = None
	mip_gap: float | None = None
	solver_threads: int | None = None
	log_to_console: bool = False
	extract_all_variable_values: bool = True


@dataclass(slots=True)
class TECCLHighsSolveResult:
	model_status: str
	has_usable_solution: bool
	objective_value: float | None
	best_bound: float | None
	mip_gap: float | None
	solve_time_ms: float
	variable_count: int
	constraint_count: int
	non_zero_count: int
	flow_values: dict[tuple[str, str, int], float] = field(default_factory=dict)
	buffer_values: dict[tuple[str, str, int], float] = field(default_factory=dict)
	receive_values: dict[tuple[str, str, str, int], float] = field(default_factory=dict)
	summary: dict[str, Any] = field(default_factory=dict)


def solve_teccl_milp(
	build_result: TECCLMILPBuildResult,
	config: TECCLHighsSolveConfig | None = None,
) -> TECCLHighsSolveResult:
	config = config or TECCLHighsSolveConfig()
	if highspy is None:
		raise TECCLHighsBackendError("highspy is required to solve the TE-CCL MILP model")

	model = build_result.model
	_apply_solver_options(model, config)
	start = perf_counter()
	model.optimize()
	solve_time_ms = (perf_counter() - start) * 1000.0

	info = model.getInfo()
	status = model.modelStatusToString(model.getModelStatus())
	objective_value = _safe_float(model.getObjectiveValue())
	best_bound = _safe_float(getattr(info, "mip_dual_bound", None))
	mip_gap = _safe_float(getattr(info, "mip_gap", None))
	has_usable_solution = _has_usable_solution(status=status, objective_value=objective_value)

	flow_values: dict[tuple[str, str, int], float] = {}
	buffer_values: dict[tuple[str, str, int], float] = {}
	receive_values: dict[tuple[str, str, str, int], float] = {}
	if config.extract_all_variable_values:
		flow_values = {
			key: float(model.variableValue(var))
			for key, var in build_result.variables.flow.items()
		}
		buffer_values = {
			key: float(model.variableValue(var))
			for key, var in build_result.variables.buffer.items()
		}
		receive_values = {
			key: float(model.variableValue(var))
			for key, var in build_result.variables.receive.items()
		}

	summary = {
		"model_status": status,
		"has_usable_solution": has_usable_solution,
		"objective_value": objective_value,
		"best_bound": best_bound,
		"mip_gap": mip_gap,
		"solve_time_ms": solve_time_ms,
		"variable_count": build_result.summary["variable_count"],
		"constraint_count": build_result.summary["constraint_count"],
		"non_zero_count": build_result.summary["non_zero_count"],
		"simplex_iteration_count": int(getattr(info, "simplex_iteration_count", 0) or 0),
		"ipm_iteration_count": int(getattr(info, "ipm_iteration_count", 0) or 0),
		"mip_node_count": int(getattr(info, "mip_node_count", 0) or 0),
	}
	return TECCLHighsSolveResult(
		model_status=status,
		has_usable_solution=has_usable_solution,
		objective_value=objective_value,
		best_bound=best_bound,
		mip_gap=mip_gap,
		solve_time_ms=solve_time_ms,
		variable_count=int(build_result.summary["variable_count"]),
		constraint_count=int(build_result.summary["constraint_count"]),
		non_zero_count=int(build_result.summary["non_zero_count"]),
		flow_values=flow_values,
		buffer_values=buffer_values,
		receive_values=receive_values,
		summary=summary,
	)


def _apply_solver_options(model, config: TECCLHighsSolveConfig) -> None:
	model.setOptionValue("output_flag", config.log_to_console)
	if config.max_solver_time_ms is not None and config.max_solver_time_ms > 0:
		model.setOptionValue("time_limit", config.max_solver_time_ms / 1000.0)
	if config.mip_gap is not None and config.mip_gap >= 0:
		model.setOptionValue("mip_rel_gap", config.mip_gap)
	if config.solver_threads is not None and config.solver_threads > 0:
		model.setOptionValue("threads", int(config.solver_threads))


def _safe_float(value: object) -> float | None:
	if value is None:
		return None
	if isinstance(value, int | float):
		return float(value) if isfinite(float(value)) else None
	return None


def _has_usable_solution(status: str, objective_value: float | None) -> bool:
	if status in {"Optimal", "Feasible"}:
		return True
	if objective_value is None:
		return False
	status_lower = status.lower()
	return "time limit" in status_lower or "solution limit" in status_lower