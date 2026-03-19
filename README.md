# 联合仿真系统

本项目在同一套 Python 离散事件仿真底座中实现并对比两类调度策略：

- CRUX：job-level 的路径与优先级调度
- TE-CCL：chunk/epoch 驱动的集合通信调度

项目目标不是直接复现论文中的所有数值，而是构建一个可运行、可对比、可归因的统一实验平台，使两类算法在相同拓扑、相同链路参数、相同工作负载和相同指标口径下进行公平比较。

## 当前能力

- 支持 topology、workload、experiment 三类 YAML 配置输入
- 支持 generated 和 explicit 两种拓扑构建方式
- 支持统一工作负载模型，供 CRUX 和 TE-CCL 共用
- 支持最小离散事件执行器、链路共享和结果导出
- 支持按 paper/crux.md 重构后的 CRUX：GPU intensity、priority assignment、争用 DAG、优先级压缩与 priority-aware runtime
- 支持按 paper/teccl.md 重构后的 TE-CCL 时间展开 MILP 主路径，正式求解后端为 HiGHS
- 支持 TE-CCL 的 GPU 副本持久转发语义（GPU 发送不扣减副本占有，U 仅用于可发送性门控）
- 支持最小端到端实验
- 支持公平对比矩阵配置与枚举
- 支持结果归因报告与交接报告生成
- 支持分离导出 TE-CCL 的模型构建时间、求解时间、通信执行时间和端到端时间
- 支持分离导出 CRUX 的路径/优先级构建时间、通信执行时间和端到端时间

## 目录结构

```text
simulation/
├── README.md
├── plan.md
├── progress.md
├── prompt.md
├── handoff.md
├── feature_list.json
├── configs/
│   ├── topology/
│   ├── workload/
│   └── experiment/
├── simulator/
│   ├── config/
│   ├── topology/
│   ├── workload/
│   ├── core/
│   ├── schedulers/
│   ├── metrics/
│   └── experiment/
└── results/
```

关键文件：

- README.md、explan.md：项目说明与解释
- configs/workload/XXX.yaml：流量文件
- configs/topology/XXX.yaml：拓扑文件
- configs/experiment/XXX.yaml：实验文件

## 环境要求

所有 Python 相关操作都必须在 conda 的 networkSimulation 环境下执行。

进入项目目录后，先激活环境：

```bash
cd /home/code/simulation
conda activate networkSimulation
```

如果你需要显式使用该环境中的 Python，可使用：

```bash
/home/code/miniconda3/envs/networkSimulation/bin/python
```

## Python 依赖版本清单

项目根目录新增了依赖版本文件：

- `requirement.txt`

该文件记录了当前项目代码实际使用到的第三方 Python 包及其版本（基于 `networkSimulation` 环境扫描生成）。

如需按该清单安装依赖，可执行：

```bash
/home/code/miniconda3/envs/networkSimulation/bin/python -m pip install -r requirement.txt
```

## 快速开始

建议第一次接手时按以下顺序阅读：

1. explan.md 与 README.md
2. configs 中与你要运行的实验直接相关的配置文件

## 如何运行实验

###  使用标准化对比脚本 run_experiment_compare.sh

主目录下的 run_experiment_compare.sh 是当前推荐的对比入口。

这个脚本内部固定使用 networkSimulation 环境中的 Python 解释器，因此即使不手工写出完整 Python 路径，也会在同一解释器口径下运行 compare 流程。为了避免环境变量或依赖歧义，仍然建议先执行 conda activate networkSimulation。

它会自动完成以下步骤：

- 读取三份 experiment 配置
- 分别运行 experiment-a、experiment-b 和 experiment-c（通常分别对应 CRUX / TE-CCL / ECMP）
- 将三边原始结果写到同一个输出根目录下的 run_a、run_b 和 run_c
- 自动生成 comparison_summary.json（三方同屏）和一指标一图的 comparison/metric_plots
- 写出 comparison_manifest.json，记录输入配置、显示标签和输出位置

基本命令格式：

```bash
conda activate networkSimulation
./run_experiment_compare.sh <experiment-a.yaml> <experiment-b.yaml> <experiment-c.yaml> <output-dir>
```

例如，对比 triple 拓扑下的 CRUX、TE-CCL 与 ECMP baseline：

```bash
conda activate networkSimulation
./run_experiment_compare.sh \
    configs/experiment/inter_dc_triple_parallel_crux.yaml \
    configs/experiment/inter_dc_triple_parallel_teccl.yaml \
    configs/experiment/inter_dc_triple_parallel_ecmp.yaml \
    results/inter_dc_triple_parallel_crux_vs_teccl_vs_ecmp \
```
输出目录结构通常如下：

```text
results/<your-compare-dir>/
├── comparison_manifest.json
├── run_a/
├── run_b/
├── run_c/
└── comparison/
    ├── comparison_summary.json
    └── metric_plots/
        ├── completion_time_ms.png
        ├── job_completion_ratio.png
        ├── bottleneck_link_peak_utilization.png
        ├── bottleneck_link_average_utilization.png
        ├── bottleneck_busy_time_ms.png
        ├── queue_backlog_percentiles_mb.png
        ├── flow_completion_time_percentiles_ms.png
        ├── job_completion_time_percentiles_ms.png
        ├── completion_time_spread_ms.png
        └── congestion_duration_ms.png
```

其中最值得优先查看的是：

- comparison/comparison_summary.json
- comparison/metric_plots/*.png
- run_a/summary.json
- run_b/summary.json
- run_c/summary.json

comparison_summary.json 中会写出每个指标的 display_name、chart_type，以及 participants 三侧实验的汇总值，适合后续继续做自动报告或 notebook 分析。


## 结果文件说明

每次通过 ExperimentRunner.export_results 运行实验后，结果目录中通常会包含以下文件：

- summary.json：聚合指标与每次 repetition 摘要
- summary.csv：便于表格处理的摘要指标
- link_load_trace.csv：链路负载时间序列
- link_load_trace.json：链路负载时间序列的 JSON 版本
- flow_trace.csv：flow 级执行轨迹
- schedule_history.json：每轮调度历史
- scheduler_debug.json：调度器内部调试状态
- crux_scheduler_stats.json：当 scheduler.type=crux 时，额外导出完整 CRUX profiling、图规模、cut 统计与执行增益
- teccl_solver_stats.json：当 scheduler.type=teccl 时，额外导出完整求解统计与模型规模

如果是通过 run_experiment_compare.sh 运行标准化对比，则在对比输出根目录下还会额外出现：

- comparison_manifest.json：标准化 compare 入口的运行清单
- comparison/comparison_summary.json：对比指标汇总
- comparison/metric_plots/*.png：一指标一图的对比结果

对于当前的 CRUX 实现，summary.json 与 crux_scheduler_stats.json 中最需要优先区分的是以下几个时间字段：

- crux_scheduler_wall_time_ms：CRUX 从 intensity/path selection 到 DAG 压缩结束的总构建耗时
- crux_path_selection_time_ms：路径选择耗时
- crux_priority_assignment_time_ms：priority assignment 耗时
- crux_priority_compression_time_ms：争用 DAG 构建、拓扑序采样与 DP 压缩耗时
- crux_communication_execution_time_ms：按压缩后硬件优先级执行通信的耗时
- crux_end_to_end_time_ms：构建耗时与执行耗时之和

这组字段用于直接回答：CRUX 的收益来自建模/压缩，还是来自 priority-aware execution。

对于当前的 TE-CCL 实现，summary.json 与 teccl_solver_stats.json 中最需要优先区分的是以下几个时间字段：

- completion_time_ms：runtime 执行结束时的完成时间，可视为通信执行阶段的完成时间基线
- teccl_model_build_time_ms：时间展开 MILP 的建模耗时
- teccl_solve_only_time_ms：HiGHS optimize 本身的耗时
- teccl_solver_wall_time_ms：TE-CCL 从建模开始到求解结束的总墙钟耗时
- teccl_communication_execution_time_ms：已求得计划在 runtime 中执行通信的耗时
- teccl_end_to_end_time_ms：求解总耗时与通信执行耗时之和

这组字段用于直接回答：TE-CCL 慢，是慢在求解还是慢在通信。

当前 comparison/metric_plots/completion_time_ms.png 会把 CRUX 和 TE-CCL 都画成“通信执行时间 + 规划/求解时间”的堆叠柱，便于直接对齐端到端口径。

## 公平对比规则

CRUX 与 TE-CCL 做比较时，下面这些公共字段必须保持一致：

- topology_file
- workload_file
- random_seed
- simulation.max_time_ms
- simulation.bandwidth_sharing_model
- metrics.export_json
- metrics.export_csv
- metrics.export_trace

允许变化的仅是算法私有参数，例如：

- CRUX：hardware_priority_count、candidate_path_limit、topological_order_sample_count、intensity_definition_mode、priority_factor_mode、enable_priority_aware_bandwidth
- TE-CCL：epoch_size_ms、solver_backend、max_epoch_count（兼容 planning_horizon_epochs）、max_solver_time_ms、mip_gap、solver_threads、objective_mode、switch_buffer_policy