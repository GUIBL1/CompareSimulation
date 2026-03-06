from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simulator.topology.models import TopologyGraph
from simulator.workload.models import UnifiedJob


@dataclass(slots=True, order=True)
class RuntimeEvent:
    time_ms: float
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(slots=True)
class FlowState:
    flow_id: str
    owner_job_id: str
    total_size_mb: float
    remaining_size_mb: float
    demand_id: str | None = None
    chunk_id: str | None = None
    source_node: str | None = None
    destination_node: str | None = None
    current_node: str | None = None
    priority: int | None = None
    status: str = "pending"
    start_time_ms: float | None = None
    end_time_ms: float | None = None
    assigned_bandwidth_gbps: float = 0.0
    path: list[str] = field(default_factory=list)
    traversed_link_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LinkState:
    link_id: str
    bandwidth_gbps: float = 0.0
    latency_us: float = 0.0
    active_flows: list[str] = field(default_factory=list)
    utilization: float = 0.0
    queue_backlog_mb: float = 0.0
    transmitted_mb: float = 0.0
    last_update_ms: float = 0.0
    utilization_history: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class RuntimeState:
    now_ms: float
    topology: TopologyGraph
    active_jobs: list[UnifiedJob] = field(default_factory=list)
    link_states: dict[str, LinkState] = field(default_factory=dict)
    flow_states: dict[str, FlowState] = field(default_factory=dict)
    pending_events: list[RuntimeEvent] = field(default_factory=list)
    completed_flow_ids: list[str] = field(default_factory=list)
    completed_job_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
