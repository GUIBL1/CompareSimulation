from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
import json
from statistics import mean
from typing import Any

from simulator.schedulers.teccl_highs_backend import TECCLHighsSolveResult
from simulator.schedulers.teccl_milp_builder import TECCLMILPBuildResult
from simulator.schedulers.teccl_model_input import TECCLModelInput
from simulator.topology.models import TopologyGraph
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class TECCLSolverStats:
	experiment_name: str
	topology_name: str
	solver_backend: str
	epoch_size_ms: float
	max_epoch_count: int
	planning_horizon_epochs: int
	planning_horizon_ms: float
	teccl_model_build_time_ms: float
	teccl_solve_only_time_ms: float
	teccl_solver_wall_time_ms: float
	job_count: int
	demand_count: int
	chunk_count: int
	commodity_count: int
	source_gpu_count: int
	destination_pair_count: int
	epoch_count: int
	node_count: int
	edge_count: int
	total_demand_mb: float
	average_chunk_mb: float
	max_chunk_mb: float
	inter_dc_edge_count: int
	variable_count: int
	binary_variable_count: int
	integer_variable_count: int
	continuous_variable_count: int
	constraint_count: int
	non_zero_count: int
	solver_status: str
	objective_value: float | None
	best_bound: float | None
	mip_gap: float | None
	node_explored_count: int
	metadata: dict[str, Any]

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


def build_teccl_solver_stats(
	experiment_name: str,
	solver_backend: str,
	topology: TopologyGraph,
	jobs: list[UnifiedJob],
	model_input: TECCLModelInput,
	build_result: TECCLMILPBuildResult,
	solve_result: TECCLHighsSolveResult,
	model_build_time_ms: float,
	total_wall_time_ms: float,
) -> TECCLSolverStats:
	chunk_sizes = [chunk.size_mb for job in jobs for demand in job.communication_demands for chunk in demand.chunks]
	unique_sources = {commodity.source_node for commodity in model_input.index_bundle.commodities}
	inter_dc_edge_count = sum(1 for edge in model_input.index_bundle.directed_edges if _is_inter_dc_edge(topology, edge.src, edge.dst))
	return TECCLSolverStats(
		experiment_name=experiment_name,
		topology_name=model_input.topology_name,
		solver_backend=solver_backend,
		epoch_size_ms=model_input.epoch_size_ms,
		max_epoch_count=model_input.planning_horizon_epochs,
		planning_horizon_epochs=model_input.planning_horizon_epochs,
		planning_horizon_ms=model_input.planning_horizon_ms,
		teccl_model_build_time_ms=model_build_time_ms,
		teccl_solve_only_time_ms=solve_result.solve_time_ms,
		teccl_solver_wall_time_ms=total_wall_time_ms,
		job_count=len(jobs),
		demand_count=sum(len(job.communication_demands) for job in jobs),
		chunk_count=len(chunk_sizes),
		commodity_count=len(model_input.index_bundle.commodities),
		source_gpu_count=len(unique_sources),
		destination_pair_count=len(model_input.demand_entries),
		epoch_count=len(model_input.index_bundle.epochs),
		node_count=len(model_input.index_bundle.node_partition.all_nodes),
		edge_count=len(model_input.index_bundle.directed_edges),
		total_demand_mb=float(model_input.summary.get("total_demand_mb", 0.0) or 0.0),
		average_chunk_mb=mean(chunk_sizes) if chunk_sizes else 0.0,
		max_chunk_mb=max(chunk_sizes, default=0.0),
		inter_dc_edge_count=inter_dc_edge_count,
		variable_count=int(build_result.summary.get("variable_count", 0) or 0),
		binary_variable_count=int(build_result.summary.get("binary_variable_count", 0) or 0),
		integer_variable_count=int(build_result.summary.get("integer_variable_count", 0) or 0),
		continuous_variable_count=int(build_result.summary.get("continuous_variable_count", 0) or 0),
		constraint_count=int(build_result.summary.get("constraint_count", 0) or 0),
		non_zero_count=int(build_result.summary.get("non_zero_count", 0) or 0),
		solver_status=solve_result.model_status,
		objective_value=solve_result.objective_value,
		best_bound=solve_result.best_bound,
		mip_gap=solve_result.mip_gap,
		node_explored_count=int(solve_result.summary.get("mip_node_count", 0) or 0),
		metadata={
			"has_usable_solution": solve_result.has_usable_solution,
			"gpu_node_count": len(model_input.index_bundle.node_partition.gpu_nodes),
			"switch_node_count": len(model_input.index_bundle.node_partition.switch_nodes),
			"relay_node_count": len(model_input.index_bundle.node_partition.relay_nodes),
			"builder_summary": dict(build_result.summary),
			"solver_summary": dict(solve_result.summary),
		},
	)


def export_teccl_solver_artifacts(
	output_dir: str | Path,
	experiment_name: str,
	solver_stats: TECCLSolverStats,
) -> dict[str, str]:
	output_path = Path(output_dir).resolve()
	output_path.mkdir(parents=True, exist_ok=True)
	stats_payload = solver_stats.to_dict()
	summary_payload = {
		"experiment_name": experiment_name,
		"scheduler_type": "teccl",
		"aggregate_metrics": {
			key: value
			for key, value in stats_payload.items()
			if isinstance(value, int | float | str) and key != "metadata"
		},
		"repetitions": [
			{
				"repetition_index": 0,
				**stats_payload,
			}
		],
	}
	debug_payload = {
		"experiment_name": experiment_name,
		"scheduler_type": "teccl",
		"repetitions": [
			{
				"repetition_index": 0,
				"scheduler_debug_state": {
					"teccl_solver_stats": stats_payload,
				},
			}
		],
	}

	summary_path = output_path / "summary.json"
	stats_path = output_path / "teccl_solver_stats.json"
	scheduler_debug_path = output_path / "scheduler_debug.json"
	summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")
	stats_path.write_text(json.dumps(stats_payload, indent=2, ensure_ascii=False), encoding="utf-8")
	scheduler_debug_path.write_text(json.dumps(debug_payload, indent=2, ensure_ascii=False), encoding="utf-8")
	return {
		"summary_json": str(summary_path),
		"teccl_solver_stats_json": str(stats_path),
		"scheduler_debug_json": str(scheduler_debug_path),
	}


def _is_inter_dc_edge(topology: TopologyGraph, src: str, dst: str) -> bool:
	src_node = topology.nodes.get(src)
	dst_node = topology.nodes.get(dst)
	if src_node is None or dst_node is None:
		return False
	src_dc = src_node.attributes.get("dc")
	dst_dc = dst_node.attributes.get("dc")
	if src_dc is None or dst_dc is None:
		return False
	return str(src_dc) != str(dst_dc)