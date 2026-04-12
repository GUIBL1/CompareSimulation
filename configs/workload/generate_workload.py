from __future__ import annotations

import argparse
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


COMMUNICATION_PATTERNS = [
    "broadcast",
    "all_reduce",
    "all_gather",
    "reduce_scatter",
]

POINT_TO_POINT_PATTERN = "point_to_point"

# 多 DC 拓扑下模式 2 的默认跨域流量占比（跨域流量仅允许点到点）
MULTI_DC_CROSS_DC_TRAFFIC_RATIO = 0.25

PROFILE_DEFINITIONS = [
    {
        "name": "distributed_training_sync",
        "weight": 0.40,
        "communication_pattern": "all_reduce",
        "participant_count_range": (8, 64),
        "total_data_mb_range": (256.0, 4096.0),
        "chunk_count_choices": [4, 8, 16, 32],
        "compute_phase_ms_range": (20.0, 200.0),
        "arrival_gap_ms_range": (0.0, 15.0),
        "iteration_count_range": (10, 500),
        "repeat_interval_ms_range": (5.0, 40.0),
        "dependency_mode": "strict",
    },
    {
        "name": "fanout_parameter_distribution",
        "weight": 0.20,
        "communication_pattern": "broadcast",
        "participant_count_range": (4, 32),
        "total_data_mb_range": (64.0, 1024.0),
        "chunk_count_choices": [2, 4, 8, 16],
        "compute_phase_ms_range": (5.0, 80.0),
        "arrival_gap_ms_range": (0.0, 10.0),
        "iteration_count_range": (2, 100),
        "repeat_interval_ms_range": (10.0, 80.0),
        "dependency_mode": "independent",
    },
    {
        "name": "state_collection",
        "weight": 0.20,
        "communication_pattern": "all_gather",
        "participant_count_range": (4, 32),
        "total_data_mb_range": (128.0, 2048.0),
        "chunk_count_choices": [4, 8, 16],
        "compute_phase_ms_range": (10.0, 120.0),
        "arrival_gap_ms_range": (0.0, 20.0),
        "iteration_count_range": (4, 200),
        "repeat_interval_ms_range": (8.0, 50.0),
        "dependency_mode": "strict",
    },
    {
        "name": "sharded_exchange",
        "weight": 0.20,
        "communication_pattern": "reduce_scatter",
        "participant_count_range": (4, 32),
        "total_data_mb_range": (64.0, 2048.0),
        "chunk_count_choices": [4, 8, 16, 32],
        "compute_phase_ms_range": (5.0, 100.0),
        "arrival_gap_ms_range": (0.0, 12.0),
        "iteration_count_range": (4, 256),
        "repeat_interval_ms_range": (6.0, 36.0),
        "dependency_mode": "independent",
    },
]


@dataclass(slots=True)
class WorkloadMeta:
    name: str = "generated_workload"
    version: int = 1
    description: str = "workload generated from topology GPU node ids"


@dataclass(slots=True)
class GpuTopologyInventory:
    gpu_node_ids: list[str]
    gpu_to_dc: dict[str, str]
    dc_to_gpu_ids: dict[str, list[str]]


@dataclass(slots=True)
class StructuredFlowConfig:
    collective_gpu_count: int
    collective_total_data_mb: float
    collective_chunk_count: int
    collective_job_count_per_dc: int
    cross_dc_job_count: int
    cross_dc_total_data_mb: float
    cross_dc_chunk_count: int
    round_count: int


DEFAULT_WORKLOAD_META = WorkloadMeta()


def main() -> None:
    args = _build_argument_parser().parse_args()
    topology_path = _resolve_existing_path(args.topology_file or _prompt_required_string("拓扑 YAML 路径"))
    output_path = _resolve_output_path(args.output_file or _prompt_string("输出 workload 路径", "configs/workload/generated_workload.yaml"))
    topology_inventory = load_gpu_topology_inventory(topology_path)
    gpu_node_ids = topology_inventory.gpu_node_ids
    if not gpu_node_ids:
        raise SystemExit(f"未在拓扑文件中找到 node_type=gpu 的节点: {topology_path}")

    print(f"已从 {topology_path} 提取 {len(gpu_node_ids)} 个 GPU 节点。")
    print(f"前 10 个 GPU: {', '.join(gpu_node_ids[:10])}")
    print(f"检测到 DC: {', '.join(sorted(topology_inventory.dc_to_gpu_ids.keys(), key=_natural_sort_key))}")
    dc_gpu_counts = _detect_dc_gpu_counts(topology_inventory)
    print("各 DC GPU 数量检测结果:")
    for dc in sorted(dc_gpu_counts.keys(), key=_natural_sort_key):
        print(f"  - {dc}: {dc_gpu_counts[dc]} 个 GPU")

    meta = WorkloadMeta(
        name=args.name if args.name is not None else _prompt_string("meta.name", DEFAULT_WORKLOAD_META.name),
        version=args.version if args.version is not None else _prompt_int("meta.version", DEFAULT_WORKLOAD_META.version, minimum=1),
        description=args.description if args.description is not None else _prompt_string("meta.description", DEFAULT_WORKLOAD_META.description),
    )

    selected_mode = args.mode or _prompt_choice(
        "生成模式",
        {
            "1": "逐 job 交互生成",
            "2": "按生产流量画像随机生成",
        },
        default_key="1",
    )

    random_seed = args.random_seed if args.random_seed is not None else _prompt_int("随机种子", 42, minimum=0)
    rng = random.Random(random_seed)

    if str(selected_mode) == "1":
        job_count = args.job_count if args.job_count is not None else _prompt_int("job 个数", 1, minimum=1)
        jobs = build_manual_jobs(job_count=job_count, topology_inventory=topology_inventory, rng=rng)
    else:
        mode2_flow_mode = args.mode2_flow_mode or _prompt_choice(
            "模式 2 流量构造方式",
            {
                "1": "标准生产画像随机生成",
                "2": "结构化分层流量（各 DC 域内集合通信 + 跨 DC 点到点）",
            },
            default_key="1",
        )
        if str(mode2_flow_mode) == "2":
            structured_config = _build_structured_flow_config(args=args, topology_inventory=topology_inventory)
            jobs = build_structured_flow_jobs(topology_inventory=topology_inventory, rng=rng, config=structured_config)
        else:
            simulation_round_mode = args.simulation_round_mode or _prompt_choice(
                "模式 2 的 job 模拟轮次",
                {
                    "1": "单轮 job 模拟",
                    "2": "多轮 job 模拟",
                },
                default_key="1",
            )
            job_count = args.job_count if args.job_count is not None else _prompt_int("job 个数", 1, minimum=1)
            jobs = build_random_profile_jobs(
                job_count=job_count,
                topology_inventory=topology_inventory,
                rng=rng,
                multi_iteration_enabled=str(simulation_round_mode) == "2",
            )

    payload = {
        "meta": {
            "name": meta.name,
            "version": meta.version,
            "description": meta.description,
        },
        "jobs": jobs,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"已生成 workload: {output_path}")


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate workload YAML from GPU node ids extracted from a topology YAML file.")
    parser.add_argument("--topology-file", help="Path to the topology YAML file.")
    parser.add_argument("--output-file", help="Path to the output workload YAML file.")
    parser.add_argument("--name", help="meta.name for the output workload.")
    parser.add_argument("--version", type=int, help="meta.version for the output workload.")
    parser.add_argument("--description", help="meta.description for the output workload.")
    parser.add_argument("--mode", choices=["1", "2"], help="1=manual per-job generation, 2=random production profile generation.")
    parser.add_argument(
        "--simulation-round-mode",
        choices=["1", "2"],
        help="Only used in mode 2. 1=single-iteration jobs, 2=multi-iteration jobs with randomized iteration fields.",
    )
    parser.add_argument(
        "--mode2-flow-mode",
        choices=["1", "2"],
        help="Only used in mode 2. 1=standard profile random generation, 2=structured layered flow generation.",
    )
    parser.add_argument("--job-count", type=int, help="How many jobs to generate.")
    parser.add_argument("--random-seed", type=int, help="Random seed used for random choices.")
    parser.add_argument(
        "--structured-collective-gpu-count",
        type=int,
        help="Only used in mode2-flow-mode=2. GPU count per intra-DC collective job.",
    )
    parser.add_argument(
        "--structured-collective-total-data-mb",
        type=float,
        help="Only used in mode2-flow-mode=2. total_data_mb for intra-DC collective jobs.",
    )
    parser.add_argument(
        "--structured-collective-chunk-count",
        type=int,
        help="Only used in mode2-flow-mode=2. chunk_count for intra-DC collective jobs.",
    )
    parser.add_argument(
        "--structured-collective-job-count-per-dc",
        type=int,
        help="Only used in mode2-flow-mode=2. how many intra-DC collective jobs to build per DC in each round.",
    )
    parser.add_argument(
        "--structured-cross-dc-job-count",
        type=int,
        help="Only used in mode2-flow-mode=2. cross-DC point-to-point job count per round.",
    )
    parser.add_argument(
        "--structured-cross-dc-total-data-mb",
        type=float,
        help="Only used in mode2-flow-mode=2. total_data_mb for cross-DC point-to-point jobs.",
    )
    parser.add_argument(
        "--structured-cross-dc-chunk-count",
        type=int,
        help="Only used in mode2-flow-mode=2. chunk_count for cross-DC point-to-point jobs.",
    )
    parser.add_argument(
        "--structured-round-count",
        type=int,
        help="Only used in mode2-flow-mode=2. how many rounds to repeat the structured generation process.",
    )
    return parser


def load_gpu_topology_inventory(topology_path: Path) -> GpuTopologyInventory:
    raw = yaml.safe_load(topology_path.read_text(encoding="utf-8")) or {}
    explicit_nodes = ((raw.get("nodes") or {}).get("explicit_nodes") or []) if isinstance(raw, dict) else []
    gpu_to_dc: dict[str, str] = {}
    for node in explicit_nodes:
        node_type = str(node.get("node_type", "")).strip().lower()
        node_id = str(node.get("node_id", "")).strip()
        if node_type != "gpu" or not node_id:
            continue
        dc = str(node.get("dc", "unknown_dc")).strip() or "unknown_dc"
        gpu_to_dc[node_id] = dc
    dc_to_gpu_ids: dict[str, list[str]] = {}
    for gpu_node_id, dc in gpu_to_dc.items():
        dc_to_gpu_ids.setdefault(dc, []).append(gpu_node_id)
    for dc in dc_to_gpu_ids:
        dc_to_gpu_ids[dc] = sorted(dc_to_gpu_ids[dc], key=_natural_sort_key)
    gpu_node_ids = sorted(gpu_to_dc.keys(), key=_natural_sort_key)
    return GpuTopologyInventory(gpu_node_ids=gpu_node_ids, gpu_to_dc=gpu_to_dc, dc_to_gpu_ids=dc_to_gpu_ids)


def build_manual_jobs(job_count: int, topology_inventory: GpuTopologyInventory, rng: random.Random) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    print("进入模式 1：逐 job 交互生成。每个字段可直接回车使用默认值。")
    for index in range(job_count):
        job_number = index + 1
        print(f"\n配置 job_{job_number:03d}")
        job_id = _prompt_string("job_id", f"job_{job_number:03d}")
        is_cross_dc = _prompt_cross_dc_choice(topology_inventory)
        if is_cross_dc:
            communication_pattern = POINT_TO_POINT_PATTERN
        else:
            communication_pattern = _prompt_pattern(default_pattern="broadcast")
        arrival_time_ms = _prompt_float("arrival_time_ms", 0.0, minimum=0.0)
        total_data_mb = _prompt_float("total_data_mb", 1024.0, minimum=0.000001)
        chunk_count = _prompt_int("chunk_count", 16, minimum=1)
        compute_phase_ms = _prompt_float("compute_phase_ms", 20.0, minimum=0.0)
        iteration_count = _prompt_int("iteration_count", 1, minimum=1)
        repeat_interval_ms = _prompt_float("repeat_interval_ms", 0.0, minimum=0.0)
        dependency_mode = _prompt_string("dependency_mode", "independent" if is_cross_dc else "strict")
        if is_cross_dc:
            participants = _prompt_cross_dc_participants(topology_inventory=topology_inventory, rng=rng)
        else:
            participants = _prompt_single_dc_participants(
                topology_inventory=topology_inventory,
                communication_pattern=communication_pattern,
                rng=rng,
            )
        jobs.append(
            build_job_record(
                job_id=job_id,
                arrival_time_ms=arrival_time_ms,
                participants=participants,
                communication_pattern=communication_pattern,
                total_data_mb=total_data_mb,
                chunk_count=chunk_count,
                compute_phase_ms=compute_phase_ms,
                iteration_count=iteration_count,
                repeat_interval_ms=repeat_interval_ms,
                dependency_mode=dependency_mode,
            )
        )
    return jobs


def build_random_profile_jobs(
    job_count: int,
    topology_inventory: GpuTopologyInventory,
    rng: random.Random,
    multi_iteration_enabled: bool,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    if _has_multi_dc(topology_inventory):
        if multi_iteration_enabled:
            print("进入模式 2：多 DC 拓扑下按生产画像模拟混合流量（单 DC 集合通信 + 跨 DC 点到点），多轮 job 模拟。")
        else:
            print("进入模式 2：多 DC 拓扑下按生产画像模拟混合流量（单 DC 集合通信 + 跨 DC 点到点），单轮 job 模拟。")
        current_arrival_time_ms = 0.0
        cross_dc_job_indexes = _sample_cross_dc_job_indexes(job_count=job_count, rng=rng)
        for index in range(job_count):
            if index in cross_dc_job_indexes:
                profile = {
                    "total_data_mb_range": (64.0, 1024.0),
                    "chunk_count_choices": [4, 8, 16],
                    "arrival_gap_ms_range": (0.0, 10.0),
                    "iteration_count_range": (10, 500),
                    "repeat_interval_ms_range": (5.0, 40.0),
                }
                communication_pattern = POINT_TO_POINT_PATTERN
                participants = _sample_cross_dc_participants(topology_inventory=topology_inventory, rng=rng)
                total_data_mb = round(rng.uniform(*profile["total_data_mb_range"]), 3)
                chunk_count = int(rng.choice(profile["chunk_count_choices"]))
                compute_phase_ms = 0.0
                dependency_mode = "independent"
            else:
                profile = _sample_profile(rng)
                communication_pattern = str(profile["communication_pattern"])
                participants = _sample_single_dc_collective_participants(
                    topology_inventory=topology_inventory,
                    communication_pattern=communication_pattern,
                    lower_bound=int(profile["participant_count_range"][0]),
                    upper_bound=int(profile["participant_count_range"][1]),
                    rng=rng,
                )
                total_data_mb = round(rng.uniform(*profile["total_data_mb_range"]), 3)
                chunk_count = int(rng.choice(profile["chunk_count_choices"]))
                compute_phase_ms = round(rng.uniform(*profile["compute_phase_ms_range"]), 3)
                dependency_mode = str(profile["dependency_mode"])

            arrival_gap_ms = round(rng.uniform(*profile["arrival_gap_ms_range"]), 3)
            iteration_count, repeat_interval_ms = _sample_iteration_fields(profile=profile, rng=rng, multi_iteration_enabled=multi_iteration_enabled)
            if index == 0:
                current_arrival_time_ms = 0.0
            else:
                current_arrival_time_ms = round(current_arrival_time_ms + arrival_gap_ms, 3)
            jobs.append(
                build_job_record(
                    job_id=f"job_{index + 1:03d}",
                    arrival_time_ms=current_arrival_time_ms,
                    participants=participants,
                    communication_pattern=communication_pattern,
                    total_data_mb=total_data_mb,
                    chunk_count=chunk_count,
                    compute_phase_ms=compute_phase_ms,
                    iteration_count=iteration_count,
                    repeat_interval_ms=repeat_interval_ms,
                    dependency_mode=dependency_mode,
                )
            )
        return jobs
    if multi_iteration_enabled:
        print("进入模式 2：按生产流量画像随机生成（单 DC 集合通信），多轮 job 模拟。")
    else:
        print("进入模式 2：按生产流量画像随机生成（单 DC 集合通信），单轮 job 模拟。")
    current_arrival_time_ms = 0.0
    all_gpu_node_ids = topology_inventory.gpu_node_ids
    for index in range(job_count):
        profile = _sample_profile(rng)
        communication_pattern = str(profile["communication_pattern"])
        participant_count = _sample_participant_count(
            gpu_count=len(all_gpu_node_ids),
            communication_pattern=communication_pattern,
            lower_bound=int(profile["participant_count_range"][0]),
            upper_bound=int(profile["participant_count_range"][1]),
            rng=rng,
        )
        participants = _sample_single_dc_collective_participants(
            topology_inventory=topology_inventory,
            communication_pattern=communication_pattern,
            lower_bound=participant_count,
            upper_bound=participant_count,
            rng=rng,
        )
        total_data_mb = round(rng.uniform(*profile["total_data_mb_range"]), 3)
        chunk_count = int(rng.choice(profile["chunk_count_choices"]))
        compute_phase_ms = round(rng.uniform(*profile["compute_phase_ms_range"]), 3)
        arrival_gap_ms = round(rng.uniform(*profile["arrival_gap_ms_range"]), 3)
        iteration_count, repeat_interval_ms = _sample_iteration_fields(profile=profile, rng=rng, multi_iteration_enabled=multi_iteration_enabled)
        if index == 0:
            current_arrival_time_ms = 0.0
        else:
            current_arrival_time_ms = round(current_arrival_time_ms + arrival_gap_ms, 3)
        jobs.append(
            build_job_record(
                job_id=f"job_{index + 1:03d}",
                arrival_time_ms=current_arrival_time_ms,
                participants=participants,
                communication_pattern=communication_pattern,
                total_data_mb=total_data_mb,
                chunk_count=chunk_count,
                compute_phase_ms=compute_phase_ms,
                iteration_count=iteration_count,
                repeat_interval_ms=repeat_interval_ms,
                dependency_mode=str(profile["dependency_mode"]),
            )
        )
    return jobs


def _build_structured_flow_config(args: argparse.Namespace, topology_inventory: GpuTopologyInventory) -> StructuredFlowConfig:
    print("进入模式 2：结构化分层流量生成。")
    collective_gpu_count = (
        args.structured_collective_gpu_count
        if args.structured_collective_gpu_count is not None
        else _prompt_int("每个 DC 每个域内集合通信 job 的 GPU 数", 4, minimum=2)
    )
    collective_total_data_mb = (
        args.structured_collective_total_data_mb
        if args.structured_collective_total_data_mb is not None
        else _prompt_float("域内集合通信 total_data_mb", 4096.0, minimum=0.000001)
    )
    collective_chunk_count = (
        args.structured_collective_chunk_count
        if args.structured_collective_chunk_count is not None
        else _prompt_int("域内集合通信 chunk_count", 16, minimum=1)
    )
    collective_job_count_per_dc = (
        args.structured_collective_job_count_per_dc
        if args.structured_collective_job_count_per_dc is not None
        else _prompt_int("每个 DC 每轮域内集合通信 job 数", 1, minimum=1)
    )

    cross_dc_default_job_count = 1 if _has_multi_dc(topology_inventory) else 0
    cross_dc_job_count = (
        args.structured_cross_dc_job_count
        if args.structured_cross_dc_job_count is not None
        else _prompt_int("每轮跨 DC 点到点通信 job 数", cross_dc_default_job_count, minimum=0)
    )
    if cross_dc_job_count > 0 and not _has_multi_dc(topology_inventory):
        raise SystemExit("当前拓扑仅包含单个 DC，无法生成跨 DC 点到点通信 job。")
    cross_dc_total_data_mb = (
        args.structured_cross_dc_total_data_mb
        if args.structured_cross_dc_total_data_mb is not None
        else _prompt_float("跨 DC 点到点通信 total_data_mb", 4096.0, minimum=0.000001)
    )
    cross_dc_chunk_count = (
        args.structured_cross_dc_chunk_count
        if args.structured_cross_dc_chunk_count is not None
        else _prompt_int("跨 DC 点到点通信 chunk_count", 16, minimum=1)
    )
    round_count = (
        args.structured_round_count
        if args.structured_round_count is not None
        else _prompt_int("结构化流量执行轮次", 1, minimum=1)
    )

    _validate_each_dc_gpu_capacity(topology_inventory=topology_inventory, required_gpu_count=collective_gpu_count)

    return StructuredFlowConfig(
        collective_gpu_count=collective_gpu_count,
        collective_total_data_mb=float(collective_total_data_mb),
        collective_chunk_count=collective_chunk_count,
        collective_job_count_per_dc=collective_job_count_per_dc,
        cross_dc_job_count=cross_dc_job_count,
        cross_dc_total_data_mb=float(cross_dc_total_data_mb),
        cross_dc_chunk_count=cross_dc_chunk_count,
        round_count=round_count,
    )


def build_structured_flow_jobs(
    topology_inventory: GpuTopologyInventory,
    rng: random.Random,
    config: StructuredFlowConfig,
) -> list[dict[str, Any]]:
    _validate_each_dc_gpu_capacity(topology_inventory=topology_inventory, required_gpu_count=config.collective_gpu_count)

    jobs: list[dict[str, Any]] = []
    job_sequence = 1
    dc_names = sorted(topology_inventory.dc_to_gpu_ids.keys(), key=_natural_sort_key)
    print(
        "结构化流量参数："
        f"每轮每个 DC 生成 {config.collective_job_count_per_dc} 个域内集合通信 job，"
        f"每个 job 使用 {config.collective_gpu_count} 个 GPU；"
        f"每轮跨 DC 点到点 job 数 {config.cross_dc_job_count}；"
        f"总轮次 {config.round_count}。"
    )

    for round_index in range(config.round_count):
        round_collective_participants: dict[str, set[str]] = {dc: set() for dc in dc_names}
        for dc in dc_names:
            dc_gpu_ids = topology_inventory.dc_to_gpu_ids[dc]
            for _ in range(config.collective_job_count_per_dc):
                communication_pattern = str(rng.choice(COMMUNICATION_PATTERNS))
                participants = sorted(rng.sample(dc_gpu_ids, config.collective_gpu_count), key=_natural_sort_key)
                round_collective_participants[dc].update(participants)
                jobs.append(
                    build_job_record(
                        job_id=f"job_{job_sequence:03d}",
                        arrival_time_ms=0.0,
                        participants=participants,
                        communication_pattern=communication_pattern,
                        total_data_mb=config.collective_total_data_mb,
                        chunk_count=config.collective_chunk_count,
                        compute_phase_ms=0.0,
                        iteration_count=1,
                        repeat_interval_ms=0.0,
                        dependency_mode="strict",
                    )
                )
                job_sequence += 1

        if config.cross_dc_job_count <= 0:
            continue

        active_dcs = [dc for dc, participants in round_collective_participants.items() if participants]
        if len(active_dcs) < 2:
            raise SystemExit(
                f"第 {round_index + 1} 轮中可用于跨 DC 点到点通信的 DC 数不足 2 个，无法生成跨域 job。"
            )

        for _ in range(config.cross_dc_job_count):
            src_dc, dst_dc = rng.sample(active_dcs, 2)
            src_gpu = str(rng.choice(sorted(round_collective_participants[src_dc], key=_natural_sort_key)))
            dst_gpu = str(rng.choice(sorted(round_collective_participants[dst_dc], key=_natural_sort_key)))
            jobs.append(
                build_job_record(
                    job_id=f"job_{job_sequence:03d}",
                    arrival_time_ms=0.0,
                    participants=sorted([src_gpu, dst_gpu], key=_natural_sort_key),
                    communication_pattern=POINT_TO_POINT_PATTERN,
                    total_data_mb=config.cross_dc_total_data_mb,
                    chunk_count=config.cross_dc_chunk_count,
                    compute_phase_ms=0.0,
                    iteration_count=1,
                    repeat_interval_ms=0.0,
                    dependency_mode="independent",
                )
            )
            job_sequence += 1
    return jobs


def _build_random_cross_dc_point_to_point_jobs(
    job_count: int,
    topology_inventory: GpuTopologyInventory,
    rng: random.Random,
    multi_iteration_enabled: bool,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    current_arrival_time_ms = 0.0
    dc_names = sorted(topology_inventory.dc_to_gpu_ids.keys(), key=_natural_sort_key)
    if len(dc_names) < 2:
        raise SystemExit("跨 DC 点到点生成至少需要 2 个 DC。")
    for index in range(job_count):
        src_dc, dst_dc = rng.sample(dc_names, 2)
        src_gpu = str(rng.choice(topology_inventory.dc_to_gpu_ids[src_dc]))
        dst_gpu = str(rng.choice(topology_inventory.dc_to_gpu_ids[dst_dc]))
        participants = sorted([src_gpu, dst_gpu], key=_natural_sort_key)
        total_data_mb = round(rng.uniform(64.0, 1024.0), 3)
        chunk_count = int(rng.choice([4, 8, 16]))
        compute_phase_ms = 0.0
        arrival_gap_ms = round(rng.uniform(0.0, 10.0), 3)
        iteration_count, repeat_interval_ms = _sample_iteration_fields(
            profile={
                "iteration_count_range": (10, 500),
                "repeat_interval_ms_range": (5.0, 40.0),
            },
            rng=rng,
            multi_iteration_enabled=multi_iteration_enabled,
        )
        if index == 0:
            current_arrival_time_ms = 0.0
        else:
            current_arrival_time_ms = round(current_arrival_time_ms + arrival_gap_ms, 3)
        jobs.append(
            build_job_record(
                job_id=f"job_{index + 1:03d}",
                arrival_time_ms=current_arrival_time_ms,
                participants=participants,
                communication_pattern=POINT_TO_POINT_PATTERN,
                total_data_mb=total_data_mb,
                chunk_count=chunk_count,
                compute_phase_ms=compute_phase_ms,
                iteration_count=iteration_count,
                repeat_interval_ms=repeat_interval_ms,
                dependency_mode="independent",
            )
        )
    return jobs


def _sample_cross_dc_job_indexes(job_count: int, rng: random.Random) -> set[int]:
    desired_count = int(round(job_count * MULTI_DC_CROSS_DC_TRAFFIC_RATIO))
    desired_count = max(1, min(job_count, desired_count))
    return set(rng.sample(range(job_count), desired_count))


def _sample_cross_dc_participants(topology_inventory: GpuTopologyInventory, rng: random.Random) -> list[str]:
    dc_names = sorted(topology_inventory.dc_to_gpu_ids.keys(), key=_natural_sort_key)
    if len(dc_names) < 2:
        raise SystemExit("跨 DC 点到点生成至少需要 2 个 DC。")
    src_dc, dst_dc = rng.sample(dc_names, 2)
    src_gpu = str(rng.choice(topology_inventory.dc_to_gpu_ids[src_dc]))
    dst_gpu = str(rng.choice(topology_inventory.dc_to_gpu_ids[dst_dc]))
    return sorted([src_gpu, dst_gpu], key=_natural_sort_key)


def _sample_single_dc_collective_participants(
    topology_inventory: GpuTopologyInventory,
    communication_pattern: str,
    lower_bound: int,
    upper_bound: int,
    rng: random.Random,
) -> list[str]:
    minimum_count = _minimum_participants_for_pattern(communication_pattern)
    candidate_dcs = [
        dc
        for dc, gpu_ids in topology_inventory.dc_to_gpu_ids.items()
        if len(gpu_ids) >= minimum_count
    ]
    if not candidate_dcs:
        raise SystemExit(f"没有 DC 能满足模式 {communication_pattern} 的最小 participants 要求。")
    selected_dc = str(rng.choice(candidate_dcs))
    dc_gpu_ids = topology_inventory.dc_to_gpu_ids[selected_dc]
    participant_count = _sample_participant_count(
        gpu_count=len(dc_gpu_ids),
        communication_pattern=communication_pattern,
        lower_bound=lower_bound,
        upper_bound=min(upper_bound, len(dc_gpu_ids)),
        rng=rng,
    )
    return sorted(rng.sample(dc_gpu_ids, participant_count), key=_natural_sort_key)


def _sample_iteration_fields(
    profile: dict[str, Any],
    rng: random.Random,
    multi_iteration_enabled: bool,
) -> tuple[int, float]:
    if not multi_iteration_enabled:
        return 1, 0.0
    iteration_count_range = profile.get("iteration_count_range", (2, 100))
    repeat_interval_ms_range = profile.get("repeat_interval_ms_range", (1.0, 50.0))
    iteration_count = rng.randint(int(iteration_count_range[0]), int(iteration_count_range[1]))
    repeat_interval_ms = round(rng.uniform(float(repeat_interval_ms_range[0]), float(repeat_interval_ms_range[1])), 3)
    return iteration_count, repeat_interval_ms


def build_job_record(
    job_id: str,
    arrival_time_ms: float,
    participants: list[str],
    communication_pattern: str,
    total_data_mb: float,
    chunk_count: int,
    compute_phase_ms: float,
    iteration_count: int,
    repeat_interval_ms: float,
    dependency_mode: str,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "arrival_time_ms": arrival_time_ms,
        "participants": participants,
        "communication_pattern": communication_pattern,
        "total_data_mb": total_data_mb,
        "chunk_count": chunk_count,
        "compute_phase_ms": compute_phase_ms,
        "iteration_count": iteration_count,
        "repeat_interval_ms": repeat_interval_ms,
        "dependency_mode": dependency_mode,
    }


def _prompt_pattern(default_pattern: str) -> str:
    options = {str(index + 1): pattern for index, pattern in enumerate(COMMUNICATION_PATTERNS)}
    selected_key = _prompt_choice(
        f"communication_pattern {options}",
        options,
        default_key=str(COMMUNICATION_PATTERNS.index(default_pattern) + 1),
    )
    return options[selected_key]


def _prompt_cross_dc_choice(topology_inventory: GpuTopologyInventory) -> bool:
    if not _has_multi_dc(topology_inventory):
        print("当前拓扑仅包含单个 DC，job 默认非跨域。")
        return False
    selected = _prompt_choice(
        "job 是否跨域",
        {
            "1": "否（单 DC 集合通信）",
            "2": "是（跨 DC 点到点）",
        },
        default_key="1",
    )
    return selected == "2"


def _prompt_cross_dc_participants(topology_inventory: GpuTopologyInventory, rng: random.Random) -> list[str]:
    raw = input("participants（跨域必须两个 GPU，来自不同 DC；逗号分隔，直接回车则随机选择）: ").strip()
    if raw:
        participants = [item.strip() for item in raw.split(",") if item.strip()]
        _validate_cross_dc_participants(
            participants=participants,
            gpu_node_ids=topology_inventory.gpu_node_ids,
            gpu_to_dc=topology_inventory.gpu_to_dc,
        )
        return participants
    dc_names = sorted(topology_inventory.dc_to_gpu_ids.keys(), key=_natural_sort_key)
    if len(dc_names) < 2:
        raise SystemExit("当前拓扑不足以生成跨域 participants（至少需要 2 个 DC）。")
    src_dc, dst_dc = rng.sample(dc_names, 2)
    participants = [
        str(rng.choice(topology_inventory.dc_to_gpu_ids[src_dc])),
        str(rng.choice(topology_inventory.dc_to_gpu_ids[dst_dc])),
    ]
    return sorted(participants, key=_natural_sort_key)


def _prompt_single_dc_participants(
    topology_inventory: GpuTopologyInventory,
    communication_pattern: str,
    rng: random.Random,
) -> list[str]:
    raw = input("participants（单 DC 内逗号分隔，直接回车则随机选择）: ").strip()
    if raw:
        participants = [item.strip() for item in raw.split(",") if item.strip()]
        _validate_single_dc_collective_participants(
            participants=participants,
            gpu_node_ids=topology_inventory.gpu_node_ids,
            gpu_to_dc=topology_inventory.gpu_to_dc,
            communication_pattern=communication_pattern,
        )
        return participants
    participant_count = _sample_participant_count(
        gpu_count=len(topology_inventory.gpu_node_ids),
        communication_pattern=communication_pattern,
        lower_bound=_minimum_participants_for_pattern(communication_pattern),
        upper_bound=min(len(topology_inventory.gpu_node_ids), 16),
        rng=rng,
    )
    candidate_dcs = [
        dc
        for dc, gpu_ids in topology_inventory.dc_to_gpu_ids.items()
        if len(gpu_ids) >= _minimum_participants_for_pattern(communication_pattern)
    ]
    if not candidate_dcs:
        raise SystemExit(f"没有 DC 能满足模式 {communication_pattern} 的最小 participants 要求。")
    selected_dc = str(rng.choice(candidate_dcs))
    dc_gpu_ids = topology_inventory.dc_to_gpu_ids[selected_dc]
    participant_count = min(participant_count, len(dc_gpu_ids))
    return sorted(rng.sample(dc_gpu_ids, participant_count), key=_natural_sort_key)


def _validate_participants_exist(participants: list[str], gpu_node_ids: list[str]) -> None:
    gpu_node_id_set = set(gpu_node_ids)
    missing = [participant for participant in participants if participant not in gpu_node_id_set]
    if missing:
        raise SystemExit(f"participants 中包含拓扑中不存在的 GPU 节点: {', '.join(missing)}")


def _detect_dc_gpu_counts(topology_inventory: GpuTopologyInventory) -> dict[str, int]:
    dc_gpu_counts: dict[str, int] = {}
    for dc, gpu_ids in topology_inventory.dc_to_gpu_ids.items():
        unique_gpu_ids = set(gpu_ids)
        if not unique_gpu_ids:
            raise SystemExit(f"检测失败：数据中心 {dc} 内没有 GPU 节点。")
        if len(unique_gpu_ids) != len(gpu_ids):
            raise SystemExit(f"检测失败：数据中心 {dc} 存在重复 GPU 节点定义。")
        dc_gpu_counts[dc] = len(unique_gpu_ids)
    return dc_gpu_counts


def _validate_each_dc_gpu_capacity(topology_inventory: GpuTopologyInventory, required_gpu_count: int) -> None:
    insufficient: list[tuple[str, int]] = []
    for dc, gpu_ids in topology_inventory.dc_to_gpu_ids.items():
        if len(gpu_ids) < required_gpu_count:
            insufficient.append((dc, len(gpu_ids)))
    if insufficient:
        details = "; ".join(f"{dc}: {count}" for dc, count in sorted(insufficient, key=lambda item: _natural_sort_key(item[0])))
        raise SystemExit(
            f"以下数据中心 GPU 数量不足，无法在每个域内集合通信 job 中使用 {required_gpu_count} 个 GPU: {details}"
        )


def _validate_single_dc_collective_participants(
    participants: list[str],
    gpu_node_ids: list[str],
    gpu_to_dc: dict[str, str],
    communication_pattern: str,
) -> None:
    _validate_participants_exist(participants, gpu_node_ids)
    if communication_pattern not in COMMUNICATION_PATTERNS:
        raise SystemExit(f"单 DC 集合通信只支持模式: {', '.join(COMMUNICATION_PATTERNS)}")
    minimum_count = _minimum_participants_for_pattern(communication_pattern)
    if len(participants) < minimum_count:
        raise SystemExit(f"模式 {communication_pattern} 至少需要 {minimum_count} 个 participants，当前只有 {len(participants)} 个")
    participant_dcs = {gpu_to_dc[participant] for participant in participants}
    if len(participant_dcs) != 1:
        raise SystemExit("集合通信 participants 必须全部位于同一个 DC。")


def _validate_cross_dc_participants(
    participants: list[str],
    gpu_node_ids: list[str],
    gpu_to_dc: dict[str, str],
) -> None:
    _validate_participants_exist(participants, gpu_node_ids)
    if len(participants) != 2:
        raise SystemExit("跨 DC 点到点通信 participants 必须恰好包含两个 GPU。")
    participant_dcs = {gpu_to_dc[participant] for participant in participants}
    if len(participant_dcs) != 2:
        raise SystemExit("跨 DC 点到点通信 participants 必须来自不同 DC。")


def _minimum_participants_for_pattern(communication_pattern: str) -> int:
    if communication_pattern in {"broadcast", "all_reduce", "all_gather", "reduce_scatter", POINT_TO_POINT_PATTERN}:
        return 2
    return 2


def _sample_participant_count(
    gpu_count: int,
    communication_pattern: str,
    lower_bound: int,
    upper_bound: int,
    rng: random.Random,
) -> int:
    minimum_count = max(_minimum_participants_for_pattern(communication_pattern), lower_bound)
    maximum_count = max(minimum_count, min(gpu_count, upper_bound))
    return rng.randint(minimum_count, maximum_count)


def _has_multi_dc(topology_inventory: GpuTopologyInventory) -> bool:
    return len(topology_inventory.dc_to_gpu_ids) > 1


def _sample_profile(rng: random.Random) -> dict[str, Any]:
    weights = [float(profile["weight"]) for profile in PROFILE_DEFINITIONS]
    return rng.choices(PROFILE_DEFINITIONS, weights=weights, k=1)[0]


def _prompt_choice(prompt_text: str, options: dict[str, str], default_key: str) -> str:
    if not sys.stdin.isatty():
        return default_key
    rendered = ", ".join(f"{key}={value}" for key, value in options.items())
    while True:
        raw = input(f"{prompt_text}（{rendered}，默认 {default_key}）: ").strip()
        if not raw:
            return default_key
        if raw in options:
            return raw
        print("输入无效，请重新输入。")


def _prompt_required_string(prompt_text: str) -> str:
    if not sys.stdin.isatty():
        raise SystemExit(f"缺少必填输入: {prompt_text}")
    while True:
        raw = input(f"{prompt_text}: ").strip()
        if raw:
            return raw
        print("该项不能为空，请重新输入。")


def _prompt_string(prompt_text: str, default_value: str) -> str:
    if not sys.stdin.isatty():
        return default_value
    raw = input(f"{prompt_text}（默认 {default_value}）: ").strip()
    return raw or default_value


def _prompt_int(prompt_text: str, default_value: int, minimum: int | None = None) -> int:
    if not sys.stdin.isatty():
        if minimum is not None and default_value < minimum:
            raise SystemExit(f"默认值 {default_value} 小于最小值 {minimum}: {prompt_text}")
        return default_value
    while True:
        raw = input(f"{prompt_text}（默认 {default_value}）: ").strip()
        if not raw:
            value = default_value
        else:
            try:
                value = int(raw)
            except ValueError:
                print("请输入整数。")
                continue
        if minimum is not None and value < minimum:
            print(f"请输入不小于 {minimum} 的整数。")
            continue
        return value


def _prompt_float(prompt_text: str, default_value: float, minimum: float | None = None) -> float:
    if not sys.stdin.isatty():
        if minimum is not None and default_value < minimum:
            raise SystemExit(f"默认值 {default_value} 小于最小值 {minimum}: {prompt_text}")
        return default_value
    while True:
        raw = input(f"{prompt_text}（默认 {default_value}）: ").strip()
        if not raw:
            value = default_value
        else:
            try:
                value = float(raw)
            except ValueError:
                print("请输入数字。")
                continue
        if minimum is not None and value < minimum:
            print(f"请输入不小于 {minimum} 的数值。")
            continue
        return value


def _resolve_existing_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"文件不存在: {path}")
    return path


def _resolve_output_path(raw_path: str) -> Path:
    return Path(raw_path).expanduser().resolve()


def _natural_sort_key(value: str) -> list[int | str]:
    parts = re.split(r"(\d+)", value)
    return [int(part) if part.isdigit() else part for part in parts]


if __name__ == "__main__":
    main()