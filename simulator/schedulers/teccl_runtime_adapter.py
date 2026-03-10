from __future__ import annotations

from simulator.schedulers.base import EpochAction
from simulator.schedulers.base import ScheduleDecision
from simulator.schedulers.teccl_solution_decoder import TECCLExecutionPlan


def build_teccl_plan_decision(
	plan: TECCLExecutionPlan,
	current_epoch: int,
	decision_time_ms: float,
	epoch_size_ms: float,
	solver_stats: dict[str, object] | None = None,
	emitted_epochs: set[int] | None = None,
) -> ScheduleDecision:
	emitted_epochs = emitted_epochs or set()
	transfers = () if current_epoch in emitted_epochs else plan.transfers_by_epoch.get(current_epoch, ())
	epoch_actions = [
		EpochAction(
			epoch_index=transfer.start_epoch_index,
			chunk_id=transfer.chunk_id,
			source_gpu=transfer.source_gpu,
			current_node=transfer.current_node,
			next_node=transfer.next_node,
			expected_arrival_epoch=transfer.expected_arrival_epoch,
			route_fragment=list(transfer.route_fragment),
			metadata={
				"scheduler": "teccl",
				"execution_mode": "planned_milp",
				"flow_id": transfer.flow_id,
				"owner_job_id": transfer.job_id,
				"demand_id": transfer.demand_id,
				"commodity_id": transfer.commodity_id,
				"replica_id": transfer.commodity_id,
				"ultimate_destination": transfer.ultimate_destination,
				"transfer_amount_mb": transfer.transfer_amount_mb,
				**dict(transfer.metadata),
			},
		)
		for transfer in transfers
	]
	return ScheduleDecision(
		decision_time_ms=decision_time_ms,
		valid_until_ms=decision_time_ms + epoch_size_ms,
		epoch_actions=epoch_actions,
		metadata={
			"scheduler": "teccl",
			"execution_mode": "planned_milp",
			"current_epoch": current_epoch,
			"teccl_plan_summary": {
				"planned_epoch_count": plan.summary.get("planned_epoch_count", 0),
				"planned_transfer_count": plan.summary.get("planned_transfer_count", 0),
				"planned_job_count": plan.summary.get("planned_job_count", 0),
				"flow_ids_by_job": {key: list(value) for key, value in plan.flow_ids_by_job.items()},
			},
			"teccl_solver_stats": dict(solver_stats or {}),
		},
	)