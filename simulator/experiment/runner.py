from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from simulator.config.loaders import load_experiment_config
from simulator.config.loaders import load_topology_config
from simulator.config.loaders import load_workload_config
from simulator.core.models import RuntimeState
from simulator.schedulers.base import Scheduler
from simulator.schedulers.crux import CruxScheduler
from simulator.schedulers.teccl import TECCLScheduler
from simulator.schedulers.teccl import TECCLStrategy
from simulator.topology.builder import build_topology
from simulator.workload.models import build_unified_job


@dataclass(slots=True)
class ExperimentRunner:
    experiment_file: Path

    def load_inputs(self) -> tuple[RuntimeState, Scheduler]:
        experiment = load_experiment_config(self.experiment_file)
        topology_config = load_topology_config(experiment.inputs.topology_file)
        workload_config = load_workload_config(experiment.inputs.workload_file)
        topology = build_topology(topology_config)
        jobs = [build_unified_job(job) for job in workload_config.jobs]
        runtime = RuntimeState(now_ms=0.0, topology=topology, active_jobs=jobs)
        scheduler = self._create_scheduler(experiment.scheduler)
        for job in jobs:
            scheduler.on_workload_arrival(job, runtime)
        return runtime, scheduler

    def _create_scheduler(self, scheduler_config) -> Scheduler:
        if scheduler_config.type == "crux":
            return CruxScheduler(**scheduler_config.crux)
        if scheduler_config.type == "teccl":
            strategy = TECCLStrategy(**scheduler_config.teccl)
            return TECCLScheduler(strategy=strategy)
        raise ValueError(f"Unsupported scheduler type: {scheduler_config.type}")
