# Experiment 配置说明

本目录的实验配置用于运行单个仿真实验。它把 topology、workload、scheduler、simulation 和 metrics 串起来，是 `ExperimentRunner` 的直接输入。

可参考模板：`experiment.template.yaml`，可参考示例：`minimal_crux_e2e.yaml`、`minimal_teccl_e2e.yaml`。`generated/` 目录下的 YAML 也是同一种 schema，只是由批处理入口自动生成。

## 基本结构

```yaml
meta:
  name: example_experiment
  version: 1
  description: experiment entry for unified comparison

inputs:
  topology_file: configs/topology/topology.template.yaml
  workload_file: configs/workload/workload.template.yaml

scheduler:
  type: crux
  crux:
    max_priority_levels: 8
    candidate_path_limit: 8
    intensity_window_iterations: 3
  teccl:
    epoch_size_ms: 1
    solver_backend: highs
    planning_horizon_epochs: 32
    max_solver_time_ms: 5000
    solver_threads: 4
    enforce_integrality: true
    objective_mode: weighted_early_completion
    switch_buffer_policy: zero
    allow_gpu_replication: true
    allow_switch_replication: false
    enable_gpu_buffer: true
    enable_switch_buffer: false

simulation:
  time_unit: ms
  max_time_ms: 100000
  bandwidth_sharing_model: max_min_fair
  random_seed: 42
  repetitions: 3

metrics:
  export_csv: true
  export_json: true
  export_trace: true
  output_dir: results/example_experiment
```

## 顶层字段

### meta

- `name`：实验名，必填。
- `version`：版本号。
- `description`：实验说明。

### inputs

- `topology_file`：拓扑文件路径，必填。
- `workload_file`：工作负载文件路径，必填。

路径可以写相对路径。加载器会按当前 experiment 文件所在位置向上解析，直到找到目标文件。

### scheduler

- `type`：必填，只能是 `crux` 或 `teccl`。
- `crux`：CRUX 专属参数块。
- `teccl`：TECCL 专属参数块。

注意：两个参数块都可以存在，但只有 `scheduler.type` 对应的那一块会被真正消费。

#### CRUX 参数

- `max_priority_levels`：必填。优先级压缩级数。
- `candidate_path_limit`：候选路径上限。
- `intensity_window_iterations`：强度统计窗口。

#### TECCL 参数

- `epoch_size_ms`：必填。一个 epoch 的时长，单位毫秒。
- `solver_backend`：必填。当前正式后端使用 `highs`。
- `planning_horizon_epochs`：建议显式填写。时间展开规划窗口的 epoch 数。
- `max_solver_time_ms`：求解器时间预算。
- `mip_gap`：可选。HiGHS 的 MIP gap 目标。
- `solver_threads`：可选。HiGHS 线程数。
- `enforce_integrality`：是否保持整数建模。
- `objective_mode`：当前默认 `weighted_early_completion`。
- `switch_buffer_policy`：当前默认 `zero`，对应交换机零持久 buffer 语义。
- `allow_gpu_replication`：是否允许 GPU 复制。
- `allow_switch_replication`：是否允许交换机复制。当前公平实验通常设为 `false`。
- `enable_gpu_buffer`：是否启用 GPU buffer 语义。
- `enable_switch_buffer`：是否启用交换机 buffer 语义。当前通常设为 `false`。

### simulation

- `time_unit`：时间单位，当前统一使用 `ms`。
- `max_time_ms`：最大仿真时长，必填且大于 0。
- `bandwidth_sharing_model`：链路带宽共享模型，当前基线为 `max_min_fair`。
- `random_seed`：随机种子。
- `repetitions`：重复运行次数，必填且大于 0。

### metrics

- `export_csv`：是否导出 CSV。
- `export_json`：是否导出 JSON。
- `export_trace`：是否导出 flow trace 和 schedule history。
- `output_dir`：结果目录，必填。

## CRUX 示例

```yaml
scheduler:
  type: crux
  crux:
    max_priority_levels: 4
    candidate_path_limit: 2
    intensity_window_iterations: 1
```

## TECCL 示例

```yaml
scheduler:
  type: teccl
  teccl:
    epoch_size_ms: 1
    solver_backend: highs
    planning_horizon_epochs: 32
    max_solver_time_ms: 5000
    solver_threads: 4
    enforce_integrality: true
    objective_mode: weighted_early_completion
    switch_buffer_policy: zero
    allow_gpu_replication: true
    allow_switch_replication: false
    enable_gpu_buffer: true
    enable_switch_buffer: false
```

## 运行方式

单实验一般通过 Python 调用 `ExperimentRunner`，或被更上层脚本间接调用。对于公平矩阵批处理，不建议手写很多 experiment YAML，优先使用 `scripts/run_fair_matrix.py` 自动物化到 `configs/experiment/generated/`。

## 书写建议

- 公平对比时，CRUX 与 TECCL 两个 experiment 只应在 `scheduler` 参数上不同。
- `metrics.output_dir` 不要复用同一目录，避免结果相互覆盖。
- 当前正式实验建议直接使用 `solver_backend: highs`，并显式控制 `planning_horizon_epochs` 与 `max_solver_time_ms`。
- 如果 TE-CCL 运行时间过长，优先检查 `planning_horizon_epochs`、chunk 数、目的地对数量、拓扑边数和 `mip_gap` 设置，而不是先怀疑 runtime 通信执行。
- 如果只想跑 1 次最小复现，把 `repetitions` 设为 `1`，并把 `max_time_ms` 控制在较小范围。

## 常见错误

- `scheduler.type=teccl` 但漏写 `scheduler.teccl.epoch_size_ms` 或 `solver_backend`。
- `scheduler.type=teccl` 但没有显式限制 `planning_horizon_epochs`，导致时间展开 MILP 模型规模过大。
- `scheduler.type=crux` 但漏写 `scheduler.crux.max_priority_levels`。
- `output_dir` 为空。
- `topology_file`、`workload_file` 路径写错。