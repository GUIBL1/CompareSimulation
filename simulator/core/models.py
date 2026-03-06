from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simulator.topology.models import TopologyGraph
from simulator.workload.models import UnifiedJob


@dataclass(slots=True)
class FlowState:
    flow_id: str
    owner_job_id: str
    remaining_size_mb: float
    current_node: str | None = None
    priority: int | None = None
    path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LinkState:
    link_id: str
    active_flows: list[str] = field(default_factory=list)
    utilization: float = 0.0
    queue_backlog_mb: float = 0.0


@dataclass(slots=True)
class RuntimeState:
    now_ms: float
    topology: TopologyGraph
    active_jobs: list[UnifiedJob] = field(default_factory=list)
    link_states: dict[str, LinkState] = field(default_factory=dict)
    flow_states: dict[str, FlowState] = field(default_factory=dict)
