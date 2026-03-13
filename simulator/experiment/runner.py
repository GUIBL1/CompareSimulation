from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simulator.config.loaders import load_experiment_config
from simulator.config.loaders import load_topology_config
from simulator.config.loaders import load_workload_config
from simulator.core.engine import RuntimeEngine
from simulator.core.models import RuntimeState
from simulator.metrics import export_experiment_results
from simulator.schedulers.base import Scheduler
from simulator.schedulers.crux import CruxScheduler
from simulator.schedulers.teccl import TECCLScheduler
from simulator.schedulers.teccl import TECCLStrategy
from simulator.topology.builder import build_topology
from simulator.workload.models import build_unified_job


@dataclass(slots=True)
class ExperimentRunRecord:
    repetition_index: int
    runtime: RuntimeState
    scheduler_debug_state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExperimentRunResult:
    experiment_name: str
    scheduler_type: str
    output_dir: Path
    repetitions: list[ExperimentRunRecord] = field(default_factory=list)
    aggregate_metrics: dict[str, Any] = field(default_factory=dict)
    exported_files: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ExperimentRunner:
    experiment_file: Path

    def load_inputs(self, experiment=None) -> tuple[RuntimeState, Scheduler]:
        experiment = experiment or self._load_experiment_config()
        topology_config = load_topology_config(experiment.inputs.topology_file)
        workload_config = load_workload_config(experiment.inputs.workload_file)
        topology = build_topology(topology_config)
        jobs = [build_unified_job(job) for job in workload_config.jobs]
        runtime = RuntimeState(now_ms=0.0, topology=topology, active_jobs=jobs)
        runtime.metadata["experiment_name"] = experiment.meta.name
        runtime.metadata["scheduler_type"] = experiment.scheduler.type
        runtime.metadata["simulation_max_time_ms"] = experiment.simulation.max_time_ms
        scheduler = self._create_scheduler(experiment.scheduler)
        for job in jobs:
            scheduler.on_workload_arrival(job, runtime)
        return runtime, scheduler

    def run(self) -> ExperimentRunResult:
        experiment = self._load_experiment_config()
        run_result = ExperimentRunResult(
            experiment_name=experiment.meta.name,
            scheduler_type=experiment.scheduler.type,
            output_dir=self._resolve_output_dir(experiment.metrics.output_dir),
        )
        for repetition_index in range(experiment.simulation.repetitions):
            runtime, scheduler = self.load_inputs(experiment)
            runtime.metadata["repetition_index"] = repetition_index
            runtime.metadata["random_seed"] = experiment.simulation.random_seed + repetition_index
            engine = RuntimeEngine(
                max_time_ms=experiment.simulation.max_time_ms,
                bandwidth_sharing_model=experiment.simulation.bandwidth_sharing_model,
            )
            final_runtime = engine.run(runtime, scheduler, experiment)
            run_result.repetitions.append(
                ExperimentRunRecord(
                    repetition_index=repetition_index,
                    runtime=final_runtime,
                    scheduler_debug_state=scheduler.export_debug_state(),
                )
            )

        run_result.aggregate_metrics = self._build_aggregate_metrics(run_result)
        return run_result

    def export_results(self, run_result: ExperimentRunResult | None = None) -> ExperimentRunResult:
        experiment = self._load_experiment_config()
        run_result = run_result or self.run()
        exported_files = export_experiment_results(
            experiment=experiment,
            output_dir=run_result.output_dir,
            run_records=[
                {
                    "repetition_index": record.repetition_index,
                    "runtime": record.runtime,
                    "scheduler_debug_state": record.scheduler_debug_state,
                }
                for record in run_result.repetitions
            ],
        )
        run_result.exported_files = exported_files
        return run_result

    def _load_experiment_config(self):
        return load_experiment_config(self.experiment_file)

    def _resolve_output_dir(self, raw_output_dir: str) -> Path:
        output_path = Path(raw_output_dir)
        if output_path.is_absolute():
            return output_path
        return (self._workspace_root() / output_path).resolve()

    def _workspace_root(self) -> Path:
        experiment_path = self.experiment_file.resolve()
        for parent in [experiment_path.parent, *experiment_path.parents]:
            if (parent / "plan.md").exists() and (parent / "feature_list.json").exists() and (parent / "configs").exists():
                return parent
        return experiment_path.parent.parent.parent

    def _build_aggregate_metrics(self, run_result: ExperimentRunResult) -> dict[str, Any]:
        completion_times = [record.runtime.now_ms for record in run_result.repetitions]
        completed_flows = [len(record.runtime.completed_flow_ids) for record in run_result.repetitions]
        completed_jobs = [len(record.runtime.completed_job_ids) for record in run_result.repetitions]
        return {
            "repetition_count": len(run_result.repetitions),
            "avg_completion_time_ms": sum(completion_times) / len(completion_times) if completion_times else 0.0,
            "max_completion_time_ms": max(completion_times, default=0.0),
            "avg_completed_flow_count": sum(completed_flows) / len(completed_flows) if completed_flows else 0.0,
            "avg_completed_job_count": sum(completed_jobs) / len(completed_jobs) if completed_jobs else 0.0,
        }

    def _create_scheduler(self, scheduler_config) -> Scheduler:
        if scheduler_config.type == "crux":
            return CruxScheduler(**scheduler_config.crux)
        if scheduler_config.type == "teccl":
            strategy = TECCLStrategy(**scheduler_config.teccl)
            return TECCLScheduler(strategy=strategy)
        raise ValueError(f"Unsupported scheduler type: {scheduler_config.type}")
