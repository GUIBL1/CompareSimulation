from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

from simulator.config.loaders import load_experiment_config
from simulator.config.loaders import load_topology_config
from simulator.config.loaders import load_workload_config
from simulator.schedulers import TECCLHighsSolveConfig
from simulator.schedulers import TECCLMILPBuildConfig
from simulator.schedulers import build_teccl_model_input
from simulator.schedulers import build_teccl_milp_model
from simulator.schedulers import infer_planning_horizon_epochs
from simulator.schedulers import solve_teccl_milp
from simulator.schedulers.teccl_metrics import TECCLSolverStats
from simulator.schedulers.teccl_metrics import export_teccl_solver_artifacts
from simulator.schedulers.teccl_metrics import build_teccl_solver_stats
from simulator.topology.builder import build_topology
from simulator.workload.models import build_unified_job


@dataclass(slots=True)
class TECCLPlanningRunResult:
	experiment_name: str
	output_dir: Path
	solver_stats: TECCLSolverStats
	exported_files: dict[str, str] = field(default_factory=dict)


def run_teccl_planning_export(
	experiment_file: str | Path,
	output_dir: str | Path | None = None,
) -> TECCLPlanningRunResult:
	experiment_path = Path(experiment_file).resolve()
	experiment = load_experiment_config(experiment_path)
	if experiment.scheduler.type != "teccl":
		raise ValueError(f"TE-CCL planning export only supports teccl scheduler configs: {experiment_path}")

	topology = build_topology(load_topology_config(experiment.inputs.topology_file))
	workload = load_workload_config(experiment.inputs.workload_file)
	jobs = [build_unified_job(job) for job in workload.jobs]
	strategy = dict(experiment.scheduler.teccl)
	epoch_size_ms = float(strategy.get("epoch_size_ms", 0.0) or 0.0)
	if epoch_size_ms <= 0:
		raise ValueError("scheduler.teccl.epoch_size_ms must be positive")
	planning_horizon_epochs = int(
		strategy.get("planning_horizon_epochs")
		or infer_planning_horizon_epochs(
			jobs=jobs,
			topology=topology,
			epoch_size_ms=epoch_size_ms,
			max_time_ms=float(experiment.simulation.max_time_ms),
		)
	)
	build_config = TECCLMILPBuildConfig(
		enforce_integrality=bool(strategy.get("enforce_integrality", True)),
		objective_mode=str(strategy.get("objective_mode", "weighted_early_completion")),
		switch_buffer_policy=str(strategy.get("switch_buffer_policy", "zero")),
	)
	solve_config = TECCLHighsSolveConfig(
		max_solver_time_ms=int(strategy.get("max_solver_time_ms", 0) or 0) or None,
		mip_gap=float(strategy.get("mip_gap")) if strategy.get("mip_gap") is not None else None,
		solver_threads=int(strategy.get("solver_threads", 0) or 0) or None,
		log_to_console=bool(strategy.get("solver_log_to_console", False)),
		extract_all_variable_values=bool(strategy.get("extract_all_variable_values", False)),
	)

	wall_start = perf_counter()
	build_start = perf_counter()
	model_input = build_teccl_model_input(
		topology=topology,
		jobs=jobs,
		epoch_size_ms=epoch_size_ms,
		planning_horizon_epochs=planning_horizon_epochs,
	)
	build_result = build_teccl_milp_model(model_input=model_input, config=build_config)
	model_build_time_ms = (perf_counter() - build_start) * 1000.0
	solve_result = solve_teccl_milp(build_result=build_result, config=solve_config)
	total_wall_time_ms = (perf_counter() - wall_start) * 1000.0

	solver_stats = build_teccl_solver_stats(
		experiment_name=experiment.meta.name,
		solver_backend="highs",
		topology=topology,
		jobs=jobs,
		model_input=model_input,
		build_result=build_result,
		solve_result=solve_result,
		model_build_time_ms=model_build_time_ms,
		total_wall_time_ms=total_wall_time_ms,
	)
	result_output_dir = Path(output_dir).resolve() if output_dir is not None else Path(experiment.metrics.output_dir).resolve()
	exported_files = export_teccl_solver_artifacts(
		output_dir=result_output_dir,
		experiment_name=experiment.meta.name,
		solver_stats=solver_stats,
	)
	return TECCLPlanningRunResult(
		experiment_name=experiment.meta.name,
		output_dir=result_output_dir,
		solver_stats=solver_stats,
		exported_files=exported_files,
	)