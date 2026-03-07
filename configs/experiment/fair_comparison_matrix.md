# Fair Comparison Matrix 配置说明

`fair_comparison_matrix.yaml` 不是单实验配置，而是批处理编排配置。它描述哪些公共案例要同时跑 CRUX 和 TECCL，哪些私有参数可以做 sweep，以及结果目录如何组织。

这个文件会被以下入口直接消费：

- `scripts/run_fair_matrix.py`
- `simulator.experiment.run_fair_comparison_matrix`

## 适用场景

- 想批量生成并运行一组公平对比实验。
- 想保证 CRUX 和 TECCL 使用相同的 topology、workload、random_seed、公共 simulation 参数。
- 想系统地扫 CRUX 或 TECCL 私有参数。

## 基本结构

```yaml
meta:
  name: fair_comparison_matrix
  version: 1
  description: machine-readable fair comparison matrix for CRUX and TE-CCL

defaults:
  results_root: results/fair_comparison_matrix
  repetitions: 3
  simulation: {}
  metrics: {}
  repeatability: {}

private_parameter_ranges:
  crux: {}
  teccl: {}

public_cases:
  - case_id: baseline_minimal
    family: scale_extension
    topology_file: configs/topology/minimal_e2e_topology.yaml
    workload_file: configs/workload/minimal_e2e_workload.yaml
    random_seed: 7
    public_baseline: {}
    scheduler_overrides:
      crux: {}
      teccl: {}

parameter_sweeps:
  - sweep_id: teccl_epoch_size
    family: parameter_sensitivity
    base_case_id: load_medium
    scheduler_type: teccl
    parameter_name: epoch_size_ms
    values: [0.5, 1.0, 2.0]
```

## 顶层字段

### meta

- `name`：矩阵名。
- `version`：版本号。
- `description`：说明。

### defaults

批处理生成的 experiment YAML 会默认继承这里的公共字段。

- `results_root`：所有批处理结果的根目录。
- `repetitions`：默认重复次数，必须大于 0。
- `simulation`：公共 simulation 参数。
- `metrics`：公共导出参数。
- `repeatability`：复现实验说明，不直接驱动仿真，但用于审计和交接。

### private_parameter_ranges

声明允许做 sweep 的私有参数集合。校验器会用它检查 `parameter_sweeps` 是否合法。

例如：

```yaml
private_parameter_ranges:
  crux:
    max_priority_levels: [2, 4, 8]
  teccl:
    epoch_size_ms: [0.5, 1.0, 2.0]
    solver_backend: [small_scale_debug_solver, heuristic_solver]
```

### public_cases

每个 `public_case` 都会扩展成两条运行规格：

- 一条 CRUX
- 一条 TECCL

必须保证它们共享相同的：

- `topology_file`
- `workload_file`
- `random_seed`
- `defaults.simulation`
- `defaults.metrics` 中的公共导出设置

每个 case 的关键字段：

- `case_id`：唯一标识，必填。
- `family`：实验族名，用于组织结果目录。
- `topology_file`：公共拓扑文件。
- `workload_file`：公共工作负载文件。
- `random_seed`：公共随机种子。
- `public_baseline`：仅用于审计描述，不直接影响仿真。
- `scheduler_overrides.crux`：CRUX 私有参数。
- `scheduler_overrides.teccl`：TECCL 私有参数。
- `notes`：补充说明。

### parameter_sweeps

每个 sweep 都基于一个 `base_case_id` 展开。关键字段：

- `sweep_id`：唯一标识，必填。
- `family`：结果目录分类名。
- `base_case_id`：引用一个已有 public case。
- `scheduler_type`：只能是 `crux` 或 `teccl`。
- `parameter_name`：必须先在 `private_parameter_ranges.<scheduler_type>` 中声明。
- `values`：扫描值列表，不能为空。
- `notes`：补充说明。

## 公平性约束

这个矩阵文件的核心不是“方便批处理”，而是“强制对比公平”。建议严格遵守：

- 公共 case 下只能修改 `scheduler_overrides`。
- 不要为 CRUX 和 TECCL 分别指定不同的 topology 或 workload。
- `public_baseline` 只是元数据，不要把真正生效参数只写在这里。

## 结果目录组织

公共案例默认会输出到：

```text
results/fair_comparison_matrix/<family>/<case_id>/<scheduler_type>/
```

参数扫频默认会输出到：

```text
results/fair_comparison_matrix/<family>/<sweep_id>/<scheduler_type>/<parameter_name>_<value>/
```

## 运行方式

只跑公共案例：

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python scripts/run_fair_matrix.py --include-public
```

跑公共案例和 sweep：

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python scripts/run_fair_matrix.py --include-public --include-sweeps
```

只跑指定 case：

```bash
conda activate networkSimulation && /home/code/miniconda3/envs/networkSimulation/bin/python scripts/run_fair_matrix.py --include-public --case-id baseline_minimal
```

## 常见错误

- `public_cases` 中缺少 `scheduler_overrides.crux` 或 `scheduler_overrides.teccl`。
- `parameter_sweeps.base_case_id` 指向不存在的 case。
- `parameter_name` 没有在 `private_parameter_ranges` 中声明。
- 把真正的仿真参数写进 `public_baseline`，却没写进 `scheduler_overrides` 或 defaults。