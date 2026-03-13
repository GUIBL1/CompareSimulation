from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from typing import Any

from simulator.schedulers.teccl_model_input import TECCLDemandEntry
from simulator.schedulers.teccl_model_input import TECCLModelInput

try:
	import highspy
except ImportError:  # pragma: no cover - handled by runtime validation
	highspy = None

if TYPE_CHECKING:
	from highspy import Highs
	from highspy import highs_var


class TECCLMILPBuildError(RuntimeError):
	"""Raised when the TE-CCL MILP model cannot be constructed."""


@dataclass(slots=True)
class TECCLMILPBuildConfig:
	model_name: str = "teccl_time_expanded_milp"
	enforce_integrality: bool = True
	objective_mode: str = "weighted_early_completion"
	switch_buffer_policy: str = "zero"
	include_buffer_upper_bounds: bool = False


@dataclass(slots=True)
class TECCLVariableBundle:
	flow: dict[tuple[str, str, int], highs_var] = field(default_factory=dict)
	buffer: dict[tuple[str, str, int], highs_var] = field(default_factory=dict)
	gpu_send_rep: dict[tuple[str, str, int], highs_var] = field(default_factory=dict)
	receive: dict[tuple[str, str, str, int], highs_var] = field(default_factory=dict)


@dataclass(slots=True)
class TECCLMILPBuildResult:
	model: Highs
	model_input: TECCLModelInput
	config: TECCLMILPBuildConfig
	variables: TECCLVariableBundle
	summary: dict[str, int | float | str]
	metadata: dict[str, Any] = field(default_factory=dict)


def build_teccl_milp_model(
	model_input: TECCLModelInput,
	config: TECCLMILPBuildConfig | None = None,
) -> TECCLMILPBuildResult:
	config = config or TECCLMILPBuildConfig()
	if highspy is None:
		raise TECCLMILPBuildError("highspy is required to build the TE-CCL MILP model")
	if config.objective_mode != "weighted_early_completion":
		raise TECCLMILPBuildError(f"Unsupported objective_mode: {config.objective_mode}")
	if config.switch_buffer_policy != "zero":
		raise TECCLMILPBuildError(f"Unsupported switch_buffer_policy: {config.switch_buffer_policy}")

	model = highspy.Highs()
	variable_type = highspy.HighsVarType.kInteger if config.enforce_integrality else highspy.HighsVarType.kContinuous
	variables = TECCLVariableBundle()

	_create_flow_variables(model, model_input, variables, variable_type)
	_create_buffer_variables(model, model_input, variables, variable_type, include_upper_bounds=config.include_buffer_upper_bounds)
	_create_gpu_send_rep_variables(model, model_input, variables, variable_type)
	_create_receive_variables(model, model_input, variables, variable_type)

	constraint_counters = {
		"capacity_constraint_count": _add_capacity_constraints(model, model_input, variables),
		"gpu_representation_constraint_count": _add_gpu_send_rep_constraints(model, model_input, variables),
		"relay_availability_constraint_count": _add_relay_availability_constraints(model, model_input, variables),
		"switch_flow_conservation_constraint_count": _add_switch_flow_conservation_constraints(model, model_input, variables),
		"buffer_update_constraint_count": _add_buffer_update_constraints(model, model_input, variables),
		"receive_constraint_count": _add_receive_constraints(model, model_input, variables),
	}
	_set_objective(model, model_input, variables)

	flow_count = len(variables.flow)
	buffer_count = len(variables.buffer)
	gpu_send_rep_count = len(variables.gpu_send_rep)
	receive_count = len(variables.receive)
	summary = {
		"model_name": config.model_name,
		"objective_mode": config.objective_mode,
		"enforce_integrality": int(config.enforce_integrality),
		"variable_count": model.getNumCol(),
		"constraint_count": model.getNumRow(),
		"non_zero_count": model.getNumNz(),
		"flow_variable_count": flow_count,
		"buffer_variable_count": buffer_count,
		"gpu_send_rep_variable_count": gpu_send_rep_count,
		"receive_variable_count": receive_count,
		"binary_variable_count": 0,
		"integer_variable_count": flow_count + buffer_count + gpu_send_rep_count + receive_count if config.enforce_integrality else 0,
		"continuous_variable_count": 0 if config.enforce_integrality else flow_count + buffer_count + gpu_send_rep_count + receive_count,
		**constraint_counters,
	}
	return TECCLMILPBuildResult(
		model=model,
		model_input=model_input,
		config=config,
		variables=variables,
		summary=summary,
		metadata={
			"gpu_nodes": model_input.index_bundle.node_partition.gpu_nodes,
			"switch_nodes": model_input.index_bundle.node_partition.switch_nodes,
			"relay_nodes": model_input.index_bundle.node_partition.relay_nodes,
		},
	)


def _create_flow_variables(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
	variable_type: Any,
) -> None:
	for commodity in model_input.index_bundle.commodities:
		for edge in model_input.index_bundle.directed_edges:
			for epoch in model_input.index_bundle.epochs:
				key = (commodity.commodity_id, edge.edge_id, epoch.epoch_index)
				variables.flow[key] = model.addVariable(
					lb=0.0,
					ub=commodity.size_mb,
					type=variable_type,
					name=_sanitize_name(f"F__{commodity.commodity_id}__{edge.edge_id}__k{epoch.epoch_index}"),
				)


def _create_buffer_variables(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
	variable_type: Any,
	include_upper_bounds: bool,
) -> None:
	switch_nodes = set(model_input.index_bundle.node_partition.switch_nodes)
	for commodity in model_input.index_bundle.commodities:
		buffer_upper_bound = commodity.size_mb if include_upper_bounds else highspy.kHighsInf
		for node_id in model_input.index_bundle.node_partition.all_nodes:
			for epoch in model_input.index_bundle.epochs:
				key = (commodity.commodity_id, node_id, epoch.epoch_index)
				if node_id in switch_nodes:
					variables.buffer[key] = model.addVariable(
						lb=0.0,
						ub=0.0,
						type=variable_type,
						name=_sanitize_name(f"B__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
					)
					continue
				variables.buffer[key] = model.addVariable(
					lb=0.0,
					ub=buffer_upper_bound,
					type=variable_type,
					name=_sanitize_name(f"B__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
				)


def _create_receive_variables(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
	variable_type: Any,
) -> None:
	for demand in model_input.demand_entries:
		for epoch in model_input.index_bundle.epochs:
			key = (demand.source_node, demand.destination_node, demand.commodity_id, epoch.epoch_index)
			variables.receive[key] = model.addVariable(
				lb=0.0,
				ub=demand.required_amount_mb,
				type=variable_type,
				name=_sanitize_name(
					f"R__{demand.source_node}__{demand.destination_node}__{demand.commodity_id}__k{epoch.epoch_index}"
				),
			)


def _create_gpu_send_rep_variables(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
	variable_type: Any,
) -> None:
	for commodity in model_input.index_bundle.commodities:
		for node_id in model_input.index_bundle.node_partition.gpu_nodes:
			for epoch in model_input.index_bundle.epochs:
				key = (commodity.commodity_id, node_id, epoch.epoch_index)
				variables.gpu_send_rep[key] = model.addVariable(
					lb=0.0,
					ub=commodity.size_mb,
					type=variable_type,
					name=_sanitize_name(f"U__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
				)


def _add_capacity_constraints(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
) -> int:
	count = 0
	for edge in model_input.index_bundle.directed_edges:
		for epoch in model_input.index_bundle.epochs:
			lhs = sum(
				variables.flow[(commodity.commodity_id, edge.edge_id, epoch.epoch_index)]
				for commodity in model_input.index_bundle.commodities
			)
			capacity = model_input.capacity_by_edge_and_epoch[(edge.edge_id, epoch.epoch_index)]
			model.addConstr(lhs <= capacity, name=_sanitize_name(f"cap__{edge.edge_id}__k{epoch.epoch_index}"))
			count += 1
	return count


def _add_gpu_send_rep_constraints(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
) -> int:
	count = 0
	for commodity in model_input.index_bundle.commodities:
		for node_id in model_input.index_bundle.node_partition.gpu_nodes:
			outgoing_edges = model_input.index_bundle.edges_by_src.get(node_id, ())
			for epoch in model_input.index_bundle.epochs:
				send_rep = variables.gpu_send_rep[(commodity.commodity_id, node_id, epoch.epoch_index)]
				buffer_var = variables.buffer[(commodity.commodity_id, node_id, epoch.epoch_index)]
				model.addConstr(
					send_rep <= buffer_var,
					name=_sanitize_name(f"gpu_rep_avail__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
				)
				count += 1
				if not outgoing_edges:
					model.addConstr(
						send_rep == 0,
						name=_sanitize_name(f"gpu_rep_zero__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
					)
					count += 1
					continue
				for edge in outgoing_edges:
					flow_var = variables.flow[(commodity.commodity_id, edge.edge_id, epoch.epoch_index)]
					model.addConstr(
						flow_var <= send_rep,
						name=_sanitize_name(f"gpu_rep__{commodity.commodity_id}__{node_id}__{edge.edge_id}__k{epoch.epoch_index}"),
					)
					count += 1
	return count


def _add_relay_availability_constraints(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
) -> int:
	count = 0
	for commodity in model_input.index_bundle.commodities:
		for node_id in model_input.index_bundle.node_partition.relay_nodes:
			outgoing_edges = model_input.index_bundle.edges_by_src.get(node_id, ())
			if not outgoing_edges:
				continue
			for epoch in model_input.index_bundle.epochs:
				lhs = sum(
					variables.flow[(commodity.commodity_id, edge.edge_id, epoch.epoch_index)]
					for edge in outgoing_edges
				)
				previous_buffer = 0 if epoch.epoch_index == 0 else variables.buffer[(commodity.commodity_id, node_id, epoch.epoch_index - 1)]
				initial_amount = model_input.initial_buffer_matrix.get((commodity.commodity_id, node_id, epoch.epoch_index), 0.0)
				arrivals = _sum_arrivals_for_node(
					model_input=model_input,
					variables=variables,
					commodity_id=commodity.commodity_id,
					node_id=node_id,
					epoch_index=epoch.epoch_index,
				)
				available = previous_buffer + initial_amount + arrivals
				model.addConstr(
					lhs <= available,
					name=_sanitize_name(f"relay_avail__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
				)
				count += 1
	return count


def _add_switch_flow_conservation_constraints(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
) -> int:
	count = 0
	for commodity in model_input.index_bundle.commodities:
		for node_id in model_input.index_bundle.node_partition.switch_nodes:
			outgoing_edges = model_input.index_bundle.edges_by_src.get(node_id, ())
			incoming_edges = model_input.index_bundle.edges_by_dst.get(node_id, ())
			for epoch in model_input.index_bundle.epochs:
				incoming_expr = _sum_arrivals_for_node(
					model_input=model_input,
					variables=variables,
					commodity_id=commodity.commodity_id,
					node_id=node_id,
					epoch_index=epoch.epoch_index,
					override_edges=incoming_edges,
				)
				outgoing_expr = sum(
					variables.flow[(commodity.commodity_id, edge.edge_id, epoch.epoch_index)]
					for edge in outgoing_edges
				)
				model.addConstr(
					incoming_expr == outgoing_expr,
					name=_sanitize_name(f"switch_cons__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
				)
				count += 1
	return count


def _add_buffer_update_constraints(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
) -> int:
	count = 0
	switch_nodes = set(model_input.index_bundle.node_partition.switch_nodes)
	for commodity in model_input.index_bundle.commodities:
		for node_id in model_input.index_bundle.node_partition.all_nodes:
			for epoch in model_input.index_bundle.epochs:
				buffer_var = variables.buffer[(commodity.commodity_id, node_id, epoch.epoch_index)]
				if node_id in switch_nodes:
					model.addConstr(
						buffer_var == 0,
						name=_sanitize_name(f"switch_buffer_zero__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
					)
					count += 1
					continue

				previous_buffer = 0 if epoch.epoch_index == 0 else variables.buffer[(commodity.commodity_id, node_id, epoch.epoch_index - 1)]
				initial_amount = model_input.initial_buffer_matrix.get((commodity.commodity_id, node_id, epoch.epoch_index), 0.0)
				arrivals = _sum_arrivals_for_node(
					model_input=model_input,
					variables=variables,
					commodity_id=commodity.commodity_id,
					node_id=node_id,
					epoch_index=epoch.epoch_index,
				)
				send_amount = 0
				if node_id not in model_input.index_bundle.node_partition.gpu_nodes:
					outgoing_edges = model_input.index_bundle.edges_by_src.get(node_id, ())
					if outgoing_edges:
						send_amount = sum(
							variables.flow[(commodity.commodity_id, edge.edge_id, epoch.epoch_index)]
							for edge in outgoing_edges
						)
				model.addConstr(
					buffer_var == previous_buffer + initial_amount + arrivals - send_amount,
					name=_sanitize_name(f"buffer_update__{commodity.commodity_id}__{node_id}__k{epoch.epoch_index}"),
				)
				count += 1
	return count


def _add_receive_constraints(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
) -> int:
	count = 0
	last_epoch_index = model_input.index_bundle.epochs[-1].epoch_index
	for demand in model_input.demand_entries:
		for epoch in model_input.index_bundle.epochs:
			receive_key = (demand.source_node, demand.destination_node, demand.commodity_id, epoch.epoch_index)
			receive_var = variables.receive[receive_key]
			destination_buffer = variables.buffer[(demand.commodity_id, demand.destination_node, epoch.epoch_index)]
			model.addConstr(
				receive_var <= demand.required_amount_mb,
				name=_sanitize_name(f"recv_demand_ub__{demand.source_node}__{demand.destination_node}__{demand.commodity_id}__k{epoch.epoch_index}"),
			)
			count += 1
			model.addConstr(
				receive_var <= destination_buffer,
				name=_sanitize_name(f"recv_buffer_ub__{demand.source_node}__{demand.destination_node}__{demand.commodity_id}__k{epoch.epoch_index}"),
			)
			count += 1
			if epoch.epoch_index > 0:
				previous_receive = variables.receive[(
					demand.source_node,
					demand.destination_node,
					demand.commodity_id,
					epoch.epoch_index - 1,
				)]
				model.addConstr(
					receive_var >= previous_receive,
					name=_sanitize_name(f"recv_monotonic__{demand.source_node}__{demand.destination_node}__{demand.commodity_id}__k{epoch.epoch_index}"),
				)
				count += 1

		final_receive = variables.receive[(demand.source_node, demand.destination_node, demand.commodity_id, last_epoch_index)]
		model.addConstr(
			final_receive == demand.required_amount_mb,
			name=_sanitize_name(f"recv_terminal__{demand.source_node}__{demand.destination_node}__{demand.commodity_id}"),
		)
		count += 1
	return count


def _set_objective(
	model: Highs,
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
) -> None:
	objective = 0
	for demand in model_input.demand_entries:
		for epoch in model_input.index_bundle.epochs:
			weight = 1.0 / (epoch.epoch_index + 1)
			objective += weight * variables.receive[(demand.source_node, demand.destination_node, demand.commodity_id, epoch.epoch_index)]
	model.setObjective(objective, sense=highspy.ObjSense.kMaximize)


def _sum_arrivals_for_node(
	model_input: TECCLModelInput,
	variables: TECCLVariableBundle,
	commodity_id: str,
	node_id: str,
	epoch_index: int,
	override_edges: tuple | None = None,
):
	incoming_edges = override_edges if override_edges is not None else model_input.index_bundle.edges_by_dst.get(node_id, ())
	expression = 0
	for edge in incoming_edges:
		send_epoch = epoch_index - model_input.delay_epochs_by_edge[edge.edge_id] - 1
		if send_epoch < 0:
			continue
		expression += variables.flow[(commodity_id, edge.edge_id, send_epoch)]
	return expression


def _sanitize_name(value: str) -> str:
	return value.replace(" ", "_").replace("-", "_").replace(":", "_")