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
- 支持 TE-CCL 语义、小规模求解后端和启发式后端
- 支持最小端到端实验
- 支持公平对比矩阵配置与枚举
- 支持结果归因报告与交接报告生成

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

- plan.md：系统设计与公平对比约束
- progress.md：阶段实现记录与验证结果
- handoff.md：当前交接摘要与下一步建议
- feature_list.json：阶段验收列表
- configs/experiment/minimal_crux_e2e.yaml：CRUX 最小实验入口
- configs/experiment/minimal_teccl_e2e.yaml：TE-CCL 最小实验入口
- configs/experiment/fair_comparison_matrix.yaml：公平对比矩阵

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

1. plan.md
2. progress.md
3. handoff.md
4. feature_list.json
5. configs 中与你要运行的实验直接相关的配置文件

## 如何运行实验

### 1. 运行 CRUX 最小实验

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python - <<'PY'
from pathlib import Path
from simulator.experiment.runner import ExperimentRunner

runner = ExperimentRunner(Path('configs/experiment/minimal_crux_e2e.yaml'))
result = runner.export_results()
print(result.output_dir)
print(result.aggregate_metrics)
PY
```

输出目录默认是：

- results/minimal_crux_e2e

### 2. 运行 TE-CCL 最小实验

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python - <<'PY'
from pathlib import Path
from simulator.experiment.runner import ExperimentRunner

runner = ExperimentRunner(Path('configs/experiment/minimal_teccl_e2e.yaml'))
result = runner.export_results()
print(result.output_dir)
print(result.aggregate_metrics)
PY
```

输出目录默认是：

- results/minimal_teccl_e2e

### 3. 批量运行公平对比矩阵

当前仓库已经提供可执行的公平矩阵批处理入口。最常用的方式是先跑公共案例，再按需跑参数扫频。

只跑公共案例：

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python scripts/run_fair_matrix.py \
    --include-public \
    --case-id baseline_minimal
```

同时跑公共案例和参数扫频：

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python scripts/run_fair_matrix.py \
    --include-public \
    --include-sweeps \
    --case-id baseline_minimal \
    --sweep-id crux_priority_levels
```

批处理入口会：

- 读取公平矩阵配置
- 在 configs/experiment/generated 下物化可运行 experiment YAML
- 调用 ExperimentRunner.export_results 批量执行实验
- 在 results/fair_comparison_matrix 下写入结果
- 生成批处理清单 results/fair_comparison_matrix/batch_manifest.json

如果你只想查看矩阵内容，也可以直接枚举：

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python - <<'PY'
from pathlib import Path
from simulator.experiment import load_fair_comparison_matrix
from simulator.experiment import enumerate_public_run_pairs
from simulator.experiment import enumerate_parameter_sweep_runs

matrix = load_fair_comparison_matrix(Path('configs/experiment/fair_comparison_matrix.yaml'))
public_runs = enumerate_public_run_pairs(matrix)
sweep_runs = enumerate_parameter_sweep_runs(matrix)

print('public cases:', len(matrix.public_cases))
print('public runs:', len(public_runs))
print('parameter sweeps:', len(matrix.parameter_sweeps))
print('sweep runs:', len(sweep_runs))
PY
```

矩阵配置文件：

- configs/experiment/fair_comparison_matrix.yaml

### 4. 使用标准化对比脚本 run_experiment_compare.sh

主目录下的 run_experiment_compare.sh 是当前推荐的对比入口。

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
    --title "Triple Parallel CRUX vs TE-CCL"
```

第 4 个及之后的参数会透传给 scripts/compare_experiments.py。当前最常用的是：

- --title
- --label-a
- --label-b

例如：

```bash
conda activate networkSimulation
./run_experiment_compare.sh \
    configs/experiment/inter_dc_dual_parallel_crux.yaml \
    configs/experiment/inter_dc_dual_parallel_teccl.yaml \
    results/inter_dc_dual_parallel_crux_vs_teccl \
    --title "Dual Parallel CRUX vs TE-CCL" \
    --label-a "CRUX" \
    --label-b "TE-CCL"
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

### 5. 仅基于已有结果目录生成对比可视化

下面的命令会读取两个结果目录，并输出对比图和摘要 JSON：

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python scripts/visualize_crux_vs_teccl.py \
    --crux-result results/minimal_crux_e2e \
    --teccl-result results/minimal_teccl_e2e \
    --output-dir results/visualizations/minimal_crux_vs_teccl \
    --title "Minimal CRUX vs TE-CCL"
```

当前会生成：

- comparison_summary.json
- metric_plots/completion_time_ms.png
- metric_plots/job_completion_ratio.png
- metric_plots/bottleneck_link_peak_utilization.png
- metric_plots/bottleneck_link_average_utilization.png
- metric_plots/bottleneck_busy_time_ms.png
- metric_plots/queue_backlog_percentiles_mb.png
- metric_plots/flow_completion_time_percentiles_ms.png
- metric_plots/job_completion_time_percentiles_ms.png
- metric_plots/completion_time_spread_ms.png
- metric_plots/congestion_duration_ms.png

### 6. 生成项目交接与归因报告

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python - <<'PY'
from pathlib import Path
from simulator.metrics import write_project_handoff_report

write_project_handoff_report(
    output_dir=Path('results/project_handoff'),
    result_dirs=[
        Path('results/minimal_crux_e2e'),
        Path('results/minimal_teccl_e2e'),
    ],
    matrix_path=Path('configs/experiment/fair_comparison_matrix.yaml'),
)
PY
```

生成结果：

- results/project_handoff/project_handoff_report.json
- results/project_handoff/project_handoff_report.md

## 结果文件说明

每次通过 ExperimentRunner.export_results 运行实验后，结果目录中通常会包含以下文件：

- summary.json：聚合指标与每次 repetition 摘要
- summary.csv：便于表格处理的摘要指标
- link_load_trace.csv：链路负载时间序列
- link_load_trace.json：链路负载时间序列的 JSON 版本
- flow_trace.csv：flow 级执行轨迹
- schedule_history.json：每轮调度历史
- scheduler_debug.json：调度器内部调试状态
- comparison_manifest.json：标准化 compare 入口的运行清单

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
- TE-CCL：epoch_size_ms、solver_backend、max_solver_time_ms

## 进一步开发

如果你准备继续扩展项目，优先参考以下入口：

- handoff.md
- results/project_handoff/project_handoff_report.md
- scripts/run_fair_matrix.py
- scripts/visualize_crux_vs_teccl.py
- simulator/experiment/matrix.py
- simulator/experiment/batch.py
- simulator/metrics/reporting.py
- simulator/metrics/visualization.py

当前最自然的下一步是：

1. 执行部分 scale_extension 和 load_sensitivity 案例
2. 对已运行案例生成 CRUX/TE-CCL 对比图
3. 在绘图或 notebook 层继续消费 project_handoff_report.json 和 comparison_summary.json