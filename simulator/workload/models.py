from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simulator.config.models import WorkloadJobConfig


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    chunk_index: int
    size_mb: float
    source_set: list[str]
    destination_set: list[str]
    ready_time_ms: float
    dependency_parent_ids: list[str] = field(default_factory=list)
    collective_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CommunicationDemand:
    demand_id: str
    collective_type: str
    participants: list[str]
    source_set: list[str]
    destination_set: list[str]
    total_size_mb: float
    chunk_count: int
    chunk_size_mb: float
    dependency_mode: str
    chunks: list[Chunk] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UnifiedJob:
    job_id: str
    arrival_time_ms: float
    participants: list[str]
    compute_phase_ms: float
    iteration_count: int
    repeat_interval_ms: float
    communication_demands: list[CommunicationDemand] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_unified_job(config: WorkloadJobConfig) -> UnifiedJob:
    collective_type = _normalize_token(config.communication_pattern)
    dependency_mode = _normalize_token(config.dependency_mode)
    participants = list(config.participants)
    source_set, destination_set, demand_metadata = _resolve_collective_sets(collective_type, participants)
    chunk_size_mb = config.total_data_mb / config.chunk_count if config.chunk_count else 0.0
    chunk_ids = [f"{config.job_id}_chunk_{index}" for index in range(config.chunk_count)]
    parent_map = _build_dependency_parent_map(chunk_ids, dependency_mode)
    chunks = [
        Chunk(
            chunk_id=chunk_ids[index],
            chunk_index=index,
            size_mb=chunk_size_mb,
            source_set=list(source_set),
            destination_set=list(destination_set),
            ready_time_ms=config.arrival_time_ms,
            dependency_parent_ids=parent_map[chunk_ids[index]],
            collective_type=collective_type,
            metadata={
                "dependency_mode": dependency_mode,
                "participant_count": len(participants),
            },
        )
        for index in range(config.chunk_count)
    ]
    demand = CommunicationDemand(
        demand_id=f"{config.job_id}_demand_0",
        collective_type=collective_type,
        participants=participants,
        source_set=source_set,
        destination_set=destination_set,
        total_size_mb=config.total_data_mb,
        chunk_count=config.chunk_count,
        chunk_size_mb=chunk_size_mb,
        dependency_mode=dependency_mode,
        chunks=chunks,
        metadata=demand_metadata,
    )
    return UnifiedJob(
        job_id=config.job_id,
        arrival_time_ms=config.arrival_time_ms,
        participants=participants,
        compute_phase_ms=config.compute_phase_ms,
        iteration_count=config.iteration_count,
        repeat_interval_ms=config.repeat_interval_ms,
        communication_demands=[demand],
        metadata={
            "communication_pattern": collective_type,
            "dependency_mode": dependency_mode,
            "chunk_count": config.chunk_count,
            "participant_count": len(participants),
        },
    )


def _normalize_token(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _resolve_collective_sets(
    collective_type: str,
    participants: list[str],
) -> tuple[list[str], list[str], dict[str, Any]]:
    if collective_type in {"all_reduce", "all_gather", "all_to_all", "reduce_scatter"}:
        return list(participants), list(participants), {"fanout": "many_to_many"}

    if collective_type in {"broadcast", "multicast", "scatter"}:
        source_set = participants[:1]
        destination_set = participants[1:] or participants[:1]
        return source_set, destination_set, {"fanout": "one_to_many"}

    if collective_type in {"reduce", "gather"}:
        source_set = participants[1:] or participants[:1]
        destination_set = participants[:1]
        return source_set, destination_set, {"fanout": "many_to_one"}

    if collective_type in {"point_to_point", "unicast"}:
        source_set = participants[:1]
        destination_set = participants[1:2] or participants[:1]
        return source_set, destination_set, {"fanout": "one_to_one"}

    return list(participants), list(participants), {"fanout": "custom"}


def _build_dependency_parent_map(chunk_ids: list[str], dependency_mode: str) -> dict[str, list[str]]:
    parent_map: dict[str, list[str]] = {chunk_id: [] for chunk_id in chunk_ids}
    if dependency_mode in {"independent", "parallel", "none"}:
        return parent_map

    if dependency_mode in {"strict", "serial", "chain", "chained", "sequential"}:
        for index in range(1, len(chunk_ids)):
            parent_map[chunk_ids[index]] = [chunk_ids[index - 1]]
        return parent_map

    if dependency_mode in {"barrier", "all_previous"}:
        for index in range(1, len(chunk_ids)):
            parent_map[chunk_ids[index]] = list(chunk_ids[:index])
        return parent_map

    return parent_map
