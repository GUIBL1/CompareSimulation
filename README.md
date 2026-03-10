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
- 支持 CRUX 基线调度
- 支持按 paper/teccl.md 重构后的 TE-CCL 时间展开 MILP 主路径，正式求解后端为 HiGHS
- 支持最小端到端实验
- 支持公平对比矩阵配置与枚举
- 支持结果归因报告与交接报告生成
- 支持分离导出 TE-CCL 的模型构建时间、求解时间、通信执行时间和端到端时间

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

- REDEME.md、explan.md：项目说明与解释
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

## 快速开始

建议第一次接手时按以下顺序阅读：

1. explan.md与REDEME.md
2. configs 中与你要运行的实验直接相关的配置文件

## 如何运行实验

###  使用标准化对比脚本 run_experiment_compare.sh

主目录下的 run_experiment_compare.sh 是当前推荐的对比入口。

这个脚本内部固定使用 networkSimulation 环境中的 Python 解释器，因此即使不手工写出完整 Python 路径，也会在同一解释器口径下运行 compare 流程。为了避免环境变量或依赖歧义，仍然建议先执行 conda activate networkSimulation。

它会自动完成以下步骤：

- 读取两份 experiment 配置
- 分别运行 experiment-a 和 experiment-b
- 将两边原始结果写到同一个输出根目录下的 run_a 和 run_b
- 自动生成 comparison_summary.json 和一指标一图的 comparison/metric_plots
- 写出 comparison_manifest.json，记录输入配置、显示标签和输出位置

基本命令格式：

```bash
conda activate networkSimulation
./run_experiment_compare.sh <experiment-a.yaml> <experiment-b.yaml> <output-dir>
```

例如，对比 triple 拓扑下的 CRUX 与 TE-CCL：

```bash
conda activate networkSimulation
./run_experiment_compare.sh \
    configs/experiment/inter_dc_triple_parallel_crux.yaml \
    configs/experiment/inter_dc_triple_parallel_teccl.yaml \
    results/inter_dc_triple_parallel_crux_vs_teccl \
```
输出目录结构通常如下：

```text
results/<your-compare-dir>/
├── comparison_manifest.json
├── run_a/
├── run_b/
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

comparison_summary.json 中会同时写出每个指标的 display_name、chart_type，以及左右两侧实验的汇总值，适合后续继续做自动报告或 notebook 分析。


## 结果文件说明

每次通过 ExperimentRunner.export_results 运行实验后，结果目录中通常会包含以下文件：

- summary.json：聚合指标与每次 repetition 摘要
- summary.csv：便于表格处理的摘要指标
- link_load_trace.csv：链路负载时间序列
- link_load_trace.json：链路负载时间序列的 JSON 版本
- flow_trace.csv：flow 级执行轨迹
- schedule_history.json：每轮调度历史
- scheduler_debug.json：调度器内部调试状态
- teccl_solver_stats.json：当 scheduler.type=teccl 时，额外导出完整求解统计与模型规模

如果是通过 run_experiment_compare.sh 运行标准化对比，则在对比输出根目录下还会额外出现：

- comparison_manifest.json：标准化 compare 入口的运行清单
- comparison/comparison_summary.json：对比指标汇总
- comparison/metric_plots/*.png：一指标一图的对比结果

对于当前的 TE-CCL 实现，summary.json 与 teccl_solver_stats.json 中最需要优先区分的是以下几个时间字段：

- completion_time_ms：runtime 执行结束时的完成时间，可视为通信执行阶段的完成时间基线
- teccl_model_build_time_ms：时间展开 MILP 的建模耗时
- teccl_solve_only_time_ms：HiGHS optimize 本身的耗时
- teccl_solver_wall_time_ms：TE-CCL 从建模开始到求解结束的总墙钟耗时
- teccl_communication_execution_time_ms：已求得计划在 runtime 中执行通信的耗时
- teccl_end_to_end_time_ms：求解总耗时与通信执行耗时之和

这组字段用于直接回答：TE-CCL 慢，是慢在求解还是慢在通信。

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

- CRUX：max_priority_levels、candidate_path_limit、intensity_window_iterations
- TE-CCL：epoch_size_ms、solver_backend、planning_horizon_epochs、max_solver_time_ms、mip_gap、solver_threads、objective_mode、switch_buffer_policy