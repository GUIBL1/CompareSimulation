# 联合仿真系统

本项目在同一套 Python 离散事件仿真底座中实现并对比多种调度策略，核心对比对象为：
- CRUX
- TE-CC
- ECMP 
- CrossWeaver

## 当前能力

- 支持 topology、workload、experiment 三类 YAML 配置输入
- 支持 generated 与 explicit 两种拓扑构建方式
- 支持统一工作负载语义（CRUX / TE-CCL / ECMP / CrossWeaver 共用）
- 支持离散事件执行器、链路共享和统一结果导出
- 支持 CRUX（intensity、priority assignment、contention DAG、priority compression、priority-aware runtime）
- 支持 TE-CCL 时间展开 MILP 主路径（HiGHS 后端）
- 支持 TE-CCL 的 GPU 副本持久化转发语义（GPU 发送不扣减副本占有）
- 支持 ECMP 基线调度（stable_per_flow 或 round_robin）
- 支持 CrossWeaver 两阶段调度及参数搜索
- 支持多实验标准化对比：2 方 / 3 方 / N 方

## 目录结构

```text
CompareSimulation/
├── README.md
├── EXPLAN.md
├── requirement.txt
├── run_experiment_compare.sh
├── configs/
│   ├── topology/
│   ├── workload/
│   └── experiment/
├── simulator/
│   ├── config/
│   ├── core/
│   ├── topology/
│   ├── workload/
│   ├── schedulers/
│   ├── metrics/
│   └── experiment/
├── scripts/
│   └── compare_experiments.py
├── paper/
├── reference_topology/
└── results/
```

关键文件：

- `README.md`、`EXPLAN.md`：项目说明与实现解释
- `configs/topology/*.yaml`：拓扑配置
- `configs/workload/*.yaml`：工作负载配置
- `configs/experiment/*.yaml`：实验配置
- `run_experiment_compare.sh`：标准化多实验对比入口
- `scripts/compare_experiments.py`：对比执行与可视化生成主入口

## 环境要求

建议所有 Python 操作都在 conda 环境 `networkSimulation` 下执行。

```bash
cd /home/inspur-02/CompareSimulation
conda activate networkSimulation
```

如需显式使用该环境 Python：

```bash
/home/inspur-02/.conda/envs/networkSimulation/bin/python
```

补充：`run_experiment_compare.sh` 内部已固定使用上述 Python 解释器。

## Python 依赖版本清单

项目根目录依赖文件：

- `requirement.txt`
安装方式：

```bash
python -m pip install -r requirement.txt
```

## 快速开始

建议首次接手顺序：

1. 阅读 `README.md` 与 `EXPLAN.md`
2. 阅读目标实验对应的 `configs/topology/*.yaml`、`configs/workload/*.yaml`、`configs/experiment/*.yaml`
3. 使用 `run_experiment_compare.sh` 跑最小对比

## 如何运行实验

### 使用标准化对比脚本 run_experiment_compare.sh（推荐）

该脚本会自动：

- 接收至少两个 experiment 配置
- 依次执行并输出到 `run_1 ... run_n`
- 生成 `comparison/comparison_summary.json`
- 生成 `comparison/metric_plots/*.png`


命令格式（推荐）：

```bash
conda activate networkSimulation
./run_experiment_compare.sh \
  --output-dir <output-dir> \
  --experiment <exp1.yaml> \
  --experiment <exp2.yaml> \
  [--experiment <expN.yaml> ...] \
  [--label <label1> --label <label2> ...] \
  [--title "<compare-title>"]
```

位置参数模式（最后一个参数为输出目录）：

```bash
./run_experiment_compare.sh <exp1.yaml> <exp2.yaml> [<expN.yaml> ...] <output-dir>  [--label <label1> --label <label2> ...] [--title "<compare-title>"]
```

标签规则：

- `--label` 按顺序与 experiment 对齐
- 标签不足时回退为 experiment 的 `meta.name`
- 标签多于 experiment 数量时，多余标签会被忽略

示例（四方对比）：

```bash
conda activate networkSimulation
./run_experiment_compare.sh \
  --output-dir results/inter_dc_triple_fourway \
  --experiment configs/experiment/inter_dc_triple_heavy_ecmp.yaml \
  --experiment configs/experiment/inter_dc_triple_heavy_crux.yaml \
  --experiment configs/experiment/inter_dc_triple_heavy_teccl.yaml \
  --experiment configs/experiment/inter_dc_triple_heavy_crossweaver.yaml \
  --label ECMP --label CRUX --label TECCL --label CrossWeaver \
  --title "Inter-DC Triple Heavy"
```
或者：

```bash
conda activate networkSimulation
./run_experiment_compare.sh \
  configs/experiment/inter_dc_triple_heavy_ecmp.yaml \
  configs/experiment/inter_dc_triple_heavy_crux.yaml \
  configs/experiment/inter_dc_triple_heavy_teccl.yaml \
  configs/experiment/inter_dc_triple_heavy_crossweaver.yaml \
  results/inter_dc_triple_fourway \
  --label ECMP --label CRUX --label TECCL --label CrossWeaver \
  --title "Inter-DC Triple Heavy"
```

## 脚本入口说明

当前直接入口脚本：

- `run_experiment_compare.sh`：标准化 compare 包装脚本（推荐）
- `scripts/compare_experiments.py`：核心对比入口
  - 方式 1（推荐）：重复 `--experiment` / `--label`
  - 方式 2（legacy）：`--experiment-a --experiment-b ...`，并支持 `--experiment-<suffix>`、`--label-<suffix>`
  - 最少需要 2 个实验

配置目录下的辅助脚本（可选）：

- `configs/experiment/scan_teccl_feasibility.py`：TE-CCL 可行性扫描
- `configs/experiment/search_crossweaver_params.py`：CrossWeaver 参数搜索

## 结果文件说明

每次实验通过 `export_experiment_results` 导出，文件受 `metrics` 开关控制：

- `export_json=true` 时：
  - `summary.json`
  - `scheduler_debug.json`
  - `link_load_trace.json`
  - `crux_scheduler_stats.json`（仅 CRUX）
  - `teccl_solver_stats.json`（仅 TE-CCL）
- `export_csv=true` 时：
  - `summary.csv`
  - `link_load_trace.csv`
- `export_trace=true` 时：
  - `flow_trace.csv`
  - `schedule_history.json`

通过 `run_experiment_compare.sh` 执行对比时，还会在输出根目录额外生成：

- `comparison_manifest.json`
- `comparison/comparison_summary.json`
- `comparison/metric_plots/*.png`

当前默认主图指标包括：

- `completion_time_ms`
- `planning_time_ms`
- `communication_execution_time_ms`
- `job_completion_ratio`
- `bottleneck_link_peak_utilization`
- `bottleneck_link_average_utilization`
- `bottleneck_busy_time_ms`
- `queue_backlog_percentiles_mb`
- `flow_completion_time_percentiles_ms`
- `job_completion_time_percentiles_ms`
- `completion_time_spread_ms`
- `congestion_duration_ms`

CRUX 时间口径（`summary.json` / `crux_scheduler_stats.json`）：

- `crux_scheduler_wall_time_ms`
- `crux_path_selection_time_ms`
- `crux_priority_assignment_time_ms`
- `crux_priority_compression_time_ms`
- `crux_communication_execution_time_ms`
- `crux_end_to_end_time_ms`

TE-CCL 时间口径（`summary.json` / `teccl_solver_stats.json`）：

- `teccl_model_build_time_ms`
- `teccl_solve_only_time_ms`
- `teccl_solver_wall_time_ms`
- `teccl_communication_execution_time_ms`
- `teccl_end_to_end_time_ms`

CrossWeaver 时间口径（`summary.json`）：

- `crossweaver_stage1a_time_ms`
- `crossweaver_stage1b_time_ms`
- `crossweaver_stage2_time_ms`
- `crossweaver_scheduler_wall_time_ms`
- `crossweaver_communication_execution_time_ms`
- `crossweaver_end_to_end_time_ms`

## 公平对比规则（核心）

比较不同调度器时，建议保持以下字段一致：

- `inputs.topology_file`
- `inputs.workload_file`
- `simulation.random_seed`
- `simulation.max_time_ms`
- `simulation.bandwidth_sharing_model`
- `metrics.export_json / export_csv / export_trace`

仅调整 scheduler 私有参数（如 CRUX 的 priority 参数、TE-CCL 的 epoch/solver 参数、CrossWeaver 的 stage 参数、ECMP 的 `stable_per_flow`）。