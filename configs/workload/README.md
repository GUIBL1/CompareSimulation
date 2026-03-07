# Workload 配置说明

本目录存放统一工作负载输入。CRUX 与 TECCL 都读取同一种 workload 结构，不允许分别维护两套数据规模定义。

可参考模板：`workload.template.yaml`，可参考最小示例：`minimal_e2e_workload.yaml`。

## 基本结构

```yaml
meta:
  name: example_workload
  version: 1
  description: unified workload input for crux and te-ccl

jobs:
  - job_id: job_001
    arrival_time_ms: 0
    participants:
      - gpu_0_0
      - gpu_0_1
      - gpu_1_0
      - gpu_1_1
    communication_pattern: all_reduce
    total_data_mb: 1024
    chunk_count: 16
    compute_phase_ms: 20
    iteration_count: 100
    repeat_interval_ms: 25
    dependency_mode: strict
```

## 顶层字段

### meta

- `name`：配置名，必填。
- `version`：版本号，通常填 `1`。
- `description`：用途说明。

### jobs

必须是非空列表。每个 job 至少包含以下字段。

- `job_id`：作业唯一标识，必填。
- `arrival_time_ms`：作业到达时间，单位毫秒，必须大于等于 0。
- `participants`：参与该 collective 的 GPU 列表，必填且不能为空。
- `communication_pattern`：通信模式，必填，例如 `broadcast`、`all_reduce`。
- `total_data_mb`：总数据量，单位 MB，必须大于 0。
- `chunk_count`：切分块数，必须大于 0。
- `compute_phase_ms`：计算阶段耗时，单位毫秒，必须大于等于 0。
- `iteration_count`：迭代次数，必须大于 0。
- `repeat_interval_ms`：迭代间隔，单位毫秒，必须大于等于 0。
- `dependency_mode`：依赖模式，必填，当前示例使用 `strict`。

## 字段含义和建模建议

### participants

这里填的是参与通信的 GPU 节点 ID，必须和拓扑文件中的 GPU ID 完全一致。

### communication_pattern

当前建议使用能明确映射到 collective 语义的名称，例如：

- `broadcast`
- `all_reduce`
- `all_gather`
- `reduce_scatter`

如果新增模式，应先确认统一 workload 解析层和两个调度器都能理解该模式。

### total_data_mb 与 chunk_count

- `total_data_mb` 决定总通信量。
- `chunk_count` 决定切块粒度。

单块近似大小可理解为：

$$
chunk\_size\_mb = \frac{total\_data\_mb}{chunk\_count}
$$

TECCL 对 chunk 粒度更敏感，因为它按 chunk replica 和 epoch action 工作。CRUX 虽然是 job 级调度，但同样必须共享这份输入，保证对比公平。

### iteration_count 与 repeat_interval_ms

- `iteration_count = 1` 表示单次 collective。
- `iteration_count > 1` 时，系统会按重复任务看待该作业。
- `repeat_interval_ms` 表示相邻迭代之间的时间间隔。

### dependency_mode

当前最稳妥的写法是 `strict`。如果后续扩展更复杂的依赖模式，应先确认 workload 语义层已经支持。

## 最小示例

```yaml
meta:
  name: minimal_e2e_workload
  version: 1
  description: minimal shared workload for crux and teccl end-to-end validation

jobs:
  - job_id: job_minimal_broadcast
    arrival_time_ms: 0
    participants: [gpu_0, gpu_1, gpu_2]
    communication_pattern: broadcast
    total_data_mb: 96
    chunk_count: 2
    compute_phase_ms: 8
    iteration_count: 1
    repeat_interval_ms: 0
    dependency_mode: strict
```

## 书写建议

- 先确定拓扑里的 GPU 命名，再写 participants。
- 做公平对比时，CRUX 和 TECCL 必须共用同一个 workload 文件。
- 如果目标是验证 TECCL chunk/epoch 语义，不要把 `chunk_count` 设成 1 后再讨论复制收益。
- 如果目标是只看调度框架是否能跑通，优先从单 job、少 participant、小 chunk_count 开始。

## 常见错误

- `jobs` 写成对象而不是列表。
- `participants` 中有不存在的 GPU。
- `total_data_mb` 或 `chunk_count` 为 0。
- `iteration_count > 1` 但 `repeat_interval_ms` 没有按实验意图设置。