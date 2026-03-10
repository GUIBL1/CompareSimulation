from __future__ import annotations

from dataclasses import dataclass, field

from simulator.schedulers.teccl_highs_backend import TECCLHighsSolveResult
from simulator.schedulers.teccl_milp_builder import TECCLMILPBuildResult


@dataclass(slots=True, frozen=True)
class TECCLPlannedTransfer:
	flow_id: str
	job_id: str
	demand_id: str
	commodity_id: str
	chunk_id: str
	source_gpu: str
	current_node: str
	next_node: str
	ultimate_destination: str
	transfer_amount_mb: float
	start_epoch_index: int
	expected_arrival_epoch: int
	route_fragment: tuple[str, ...]
	metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TECCLExecutionPlan:
	transfers_by_epoch: dict[int, tuple[TECCLPlannedTransfer, ...]]
	all_transfers: tuple[TECCLPlannedTransfer, ...]
	flow_ids_by_job: dict[str, tuple[str, ...]]
	summary: dict[str, int | float | str]


def decode_teccl_solution(
	build_result: TECCLMILPBuildResult,
	solve_result: TECCLHighsSolveResult,
	positive_flow_tolerance: float = 1e-9,
) -> TECCLExecutionPlan:
	model_input = build_result.model_input
	transfers: list[TECCLPlannedTransfer] = []
	for (commodity_id, edge_id, epoch_index), amount in solve_result.flow_values.items():
		if amount <= positive_flow_tolerance:
			continue
		commodity = model_input.commodity_by_id[commodity_id]
		edge = model_input.edge_by_id[edge_id]
		ultimate_destination = _infer_ultimate_destination(commodity, edge.dst)
		flow_id = (
			f"teccl-plan::{epoch_index}::{commodity.chunk_id}::{commodity_id}::{edge.src}->{edge.dst}"
			f"::{ultimate_destination}"
		)
		transfers.append(
			TECCLPlannedTransfer(
				flow_id=flow_id,
				job_id=commodity.job_id,
				demand_id=commodity.demand_id,
				commodity_id=commodity_id,
				chunk_id=commodity.chunk_id,
				source_gpu=commodity.source_node,
				current_node=edge.src,
				next_node=edge.dst,
				ultimate_destination=ultimate_destination,
				transfer_amount_mb=float(amount),
				start_epoch_index=epoch_index,
				expected_arrival_epoch=epoch_index + edge.delay_epochs,
				route_fragment=(edge.src, edge.dst),
				metadata={
					"physical_link_id": edge.physical_link_id,
					"delay_epochs": edge.delay_epochs,
				},
			)
		)

	transfers.sort(
		key=lambda item: (
			item.start_epoch_index,
			item.job_id,
			item.chunk_id,
			item.current_node,
			item.next_node,
			item.ultimate_destination,
		)
	)
	transfers_by_epoch: dict[int, list[TECCLPlannedTransfer]] = {}
	flow_ids_by_job: dict[str, list[str]] = {}
	for transfer in transfers:
		transfers_by_epoch.setdefault(transfer.start_epoch_index, []).append(transfer)
		flow_ids_by_job.setdefault(transfer.job_id, []).append(transfer.flow_id)

	plan_summary = {
		"planned_epoch_count": len(transfers_by_epoch),
		"planned_transfer_count": len(transfers),
		"planned_job_count": len(flow_ids_by_job),
	}
	return TECCLExecutionPlan(
		transfers_by_epoch={key: tuple(value) for key, value in transfers_by_epoch.items()},
		all_transfers=tuple(transfers),
		flow_ids_by_job={key: tuple(value) for key, value in flow_ids_by_job.items()},
		summary=plan_summary,
	)


def _infer_ultimate_destination(commodity, next_node: str) -> str:
	if next_node in commodity.destination_nodes:
		return next_node
	if len(commodity.destination_nodes) == 1:
		return commodity.destination_nodes[0]
	return next_node