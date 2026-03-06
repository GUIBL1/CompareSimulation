from __future__ import annotations

from dataclasses import dataclass, field

from simulator.config.models import WorkloadJobConfig


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    size_mb: float
    source_set: list[str]
    destination_set: list[str]
    ready_time_ms: float


@dataclass(slots=True)
class CommunicationDemand:
    collective_type: str
    total_size_mb: float
    chunk_count: int
    dependency_mode: str
    chunks: list[Chunk] = field(default_factory=list)


@dataclass(slots=True)
class UnifiedJob:
    job_id: str
    arrival_time_ms: float
    participants: list[str]
    compute_phase_ms: float
    iteration_count: int
    repeat_interval_ms: float
    communication_demands: list[CommunicationDemand] = field(default_factory=list)


def build_unified_job(config: WorkloadJobConfig) -> UnifiedJob:
    chunk_size_mb = config.total_data_mb / config.chunk_count if config.chunk_count else 0.0
    chunks = [
        Chunk(
            chunk_id=f"{config.job_id}_chunk_{index}",
            size_mb=chunk_size_mb,
            source_set=list(config.participants),
            destination_set=list(config.participants),
            ready_time_ms=config.arrival_time_ms,
        )
        for index in range(config.chunk_count)
    ]
    demand = CommunicationDemand(
        collective_type=config.communication_pattern,
        total_size_mb=config.total_data_mb,
        chunk_count=config.chunk_count,
        dependency_mode=config.dependency_mode,
        chunks=chunks,
    )
    return UnifiedJob(
        job_id=config.job_id,
        arrival_time_ms=config.arrival_time_ms,
        participants=list(config.participants),
        compute_phase_ms=config.compute_phase_ms,
        iteration_count=config.iteration_count,
        repeat_interval_ms=config.repeat_interval_ms,
        communication_demands=[demand],
    )
