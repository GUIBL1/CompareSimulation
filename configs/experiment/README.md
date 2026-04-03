# Experiment 配置说明

本目录的实验配置用于运行单个仿真实验。它把 topology、workload、scheduler、simulation 和 metrics 串起来，是 `ExperimentRunner` 的直接输入。

## 当前示例实验文件

- `single_dc_any_ecmp.yaml`
- `single_dc_any_crux.yaml`
- `single_dc_any_teccl.yaml`
- `single_dc_any_crossweaver.yaml`
- `inter_dc_triple_heavy_ecmp.yaml`
- `inter_dc_triple_heavy_crux.yaml`
- `inter_dc_triple_heavy_teccl.yaml`
- `inter_dc_triple_heavy_crossweaver.yaml`

这些文件可直接作为 `scripts/compare_experiments.py` 的输入进行多算法对比。


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
    max_epoch_count: 32
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

- `type`：必填，只能是 `crux`、`teccl`、`ecmp` 或 `crossweaver`。
- `crux`：CRUX 专属参数块。
- `teccl`：TECCL 专属参数块。
- `ecmp`：ECMP baseline 专属参数块。
- `crossweaver`：CrossWeaver 两阶段调度参数块。

注意：两个参数块都可以存在，但只有 `scheduler.type` 对应的那一块会被真正消费。

#### CRUX 参数

- `max_priority_levels`：兼容字段。若未显式给出 `hardware_priority_count`，则作为硬件优先级数使用。
- `hardware_priority_count`：推荐显式填写。DAG 压缩后的硬件优先级级数。
- `candidate_path_limit`：候选路径上限。
- `topological_order_sample_count`：优先级压缩时采样的拓扑序数量。
- `intensity_window_iterations`：强度统计窗口。
- `intensity_definition_mode`：当前推荐 `selected_path_max_flow_time`。
- `priority_factor_mode`：当前推荐 `dlt_aware`。
- `enable_priority_aware_bandwidth`：是否在 runtime 中启用高优先级先占用残余带宽。

#### TECCL 参数

- `epoch_size_ms`：必填。一个 epoch 的时长，单位毫秒。
- `solver_backend`：必填。当前正式后端使用 `highs`。
- `max_epoch_count`：建议显式填写。MILP 时间展开的最大 epoch 数。
- `max_solver_time_ms`：MILP 求解器时间预算（毫秒）。若未显式提供，调度器默认 120000（2 分钟）。
- `mip_gap`：可选。HiGHS 的 MIP gap 目标。
- `solver_threads`：可选。HiGHS 线程数。
- `enforce_integrality`：是否保持整数建模。
- `objective_mode`：当前默认 `weighted_early_completion`。
- `switch_buffer_policy`：当前默认 `zero`，对应交换机零持久 buffer 语义。
- `allow_gpu_replication`：是否允许 GPU 复制。
- `allow_switch_replication`：是否允许交换机复制。当前公平实验通常设为 `false`。
- `enable_gpu_buffer`：是否启用 GPU buffer 语义。
- `enable_switch_buffer`：是否启用交换机 buffer 语义。当前通常设为 `false`。

#### ECMP 参数

- `stable_per_flow`：是否使用按 flow_id 稳定哈希选路。默认 `true`。
  - `true`：同一 flow 固定映射到候选等价路径中的一条。
  - `false`：对同一 `(src,dst)` 目的对做轮询选路。

#### CrossWeaver 参数

- `slot_ms`：时间片长度（毫秒）。
- `headroom_ratio`：Stage I-B 预留比例 \\(\eta\\)，保留容量 \\(\tilde C_e=(1-\eta)C_e\\)。
- `epsilon`：Stage I-B MWU 更新步长。
- `gamma`：Stage II 价格更新步长。
- `stage1_max_iterations`：Stage I-B 最大迭代次数。
- `stage1_binary_search_rounds`：Stage I-A 的 \(\theta\) 二分轮数。
- `stage1_backoff_ratio`：Stage I-B 不可行时跨域承诺速率缩放系数。
- `stage1_backoff_max_rounds`：Stage I-B 回退重试上限。
- `stage1_safety_margin_ratio`：Stage I-A 对 \(B_f\) 的安全裕量比例。
- `stage1_rate_smoothing_alpha`：跨 slot 速率平滑系数 \(\alpha\)。
- `stage1_rate_change_cap_ratio`：跨 slot 速率变化上限比例。
- `stage2_max_iterations`：Stage II 给定 T 的价格迭代次数。
- `stage2_binary_search_rounds`：Stage II 对 T 的二分轮数。
- `cross_path_ecmp_k`：Stage I-A 跨域路径候选上限（按 DCI 签名去重后取前 K）。
- `stage2_path_split_k`：Stage II 每个 ToR 对按低价格选择的多路径条数。
- `stage2_initial_max_paths`：Stage II 初始域内候选路径上限。
- `stage2_max_path_expansion`：Stage II 不可行时扩展后的路径上限。
- `stage2_path_expansion_step`：Stage II 每轮扩展路径数。
- `stage2_softmin_temperature`：Stage II 软最小分流温度参数。
- `wcmp_update_threshold_l1`：WCMP 权重更新阈值（L1 范数）。
- `stage1_early_stop_patience`：Stage I-B 提前收敛容忍轮次。
- `stage2_early_stop_patience`：Stage II 价格迭代提前收敛容忍轮次。
- `stage2_binary_search_tolerance_ms`：Stage II 二分提前收敛阈值。
- `feasibility_tolerance`：约束可行性容差。
- `queue_wait_estimation_mode`：`zero` 或 `observed`，用于 \\(Q_f\\) 估计。

##### CrossWeaver 参数作用与取值影响

1. Stage I-A（跨域速率/选路）
   - `cross_path_ecmp_k`
     - 作用：控制每条跨域流可选的跨 DC 路径多样性。
     - 影响：
       - 偏小（2~3）：规划更快，但容易集中到少数 DCI，通信尾部风险更高。
       - 中等（4~5）：通常能显著降低拥塞持续时间和 P99。
       - 过大（>6）：收益边际下降，规划与重平衡开销上升。

2. Stage I-B（MWU 域内映射）
   - `headroom_ratio`
     - 作用：给 Stage II 预留容量，避免 Stage I 把域内链路吃满。
     - 影响：
       - 过小（<0.04）：通信可能更快，但在突发负载下鲁棒性下降。
       - 适中（0.05~0.10）：通常是性能与鲁棒性平衡区间。
       - 过大（>0.12）：Stage II 可用容量被压缩，完成时间可能变差。
   - `epsilon`
     - 作用：MWU 更新步长。
     - 影响：
       - 小：更稳定但收敛慢。
       - 大：收敛快但可能抖动，导致负载波动。
   - `stage1_max_iterations` / `stage1_early_stop_patience`
     - 作用：控制 Stage I-B 收敛预算与提前停止。
     - 影响：预算太低易欠收敛，预算过高会抬高 planning time。
   - `stage1_backoff_ratio` / `stage1_backoff_max_rounds`
     - 作用：当 Stage I-B 在预算内不可行时，回退跨域承诺并重试。
     - 影响：回退过小可能仍不可行；回退过大可能保守，通信时间上升。
   - `stage1_rate_smoothing_alpha` / `stage1_rate_change_cap_ratio`
     - 作用：抑制跨 slot 速率抖动，降低路径更新震荡。
     - 影响：过强平滑会降低响应速度；过弱会导致频繁重配置。

3. Stage II（域内完成时间优化）
   - `gamma`
     - 作用：价格更新步长。
     - 影响：
       - 偏小：收敛慢，规划时间上升。
       - 偏大：可能震荡，通信与尾延迟变差。
   - `stage2_path_split_k`
     - 作用：每个 ToR 对的多路径分流强度。
     - 影响：
       - 偏小：热点难摊平；
       - 中等（3~5）：常见最优区间；
       - 过大：路径切分收益趋缓，计算开销增加。
   - `stage2_initial_max_paths` / `stage2_max_path_expansion` / `stage2_path_expansion_step`
     - 作用：控制候选路径空间与不可行时扩展速度。
     - 影响：
       - 初始太小：容易走到不可行，通信退化；
       - 扩展太激进：规划时间上升；
       - 建议先保证 `max_path_expansion >= initial + step`。
   - `stage2_max_iterations` / `stage2_binary_search_rounds` / `stage2_binary_search_tolerance_ms` / `stage2_early_stop_patience`
     - 作用：控制 Stage II 的“可行性内循环 + T 二分”预算。
     - 影响：预算不足会导致欠优化；预算过高会拉长 planning。
   - `stage2_softmin_temperature`
     - 作用：控制低价路径偏好强度。
     - 影响：温度过低可能过度集中到少数路径，过高会稀释负载引导。
   - `wcmp_update_threshold_l1`
     - 作用：控制 WCMP 权重的更新门限，减少抖动。
     - 影响：阈值过高可能错失优化机会，阈值过低可能带来过多更新。

4. 队列等待估计
   - `queue_wait_estimation_mode`
     - `zero`：忽略队列等待，规划更快，可能低估拥塞。
     - `observed`：使用在线观测 backlog 估计 \(Q_f\)，对重负载和尾部指标通常更稳。

5. 调参建议（重负载默认起点）
   - 可优先从以下组合开始：
     - `headroom_ratio: 0.05`
     - `epsilon: 0.06`
     - `gamma: 0.04`
     - `cross_path_ecmp_k: 5`
     - `stage2_path_split_k: 4`
     - `stage2_initial_max_paths: 12`
     - `stage2_max_path_expansion: 32`
     - `stage2_path_expansion_step: 8`
     - `queue_wait_estimation_mode: observed`

   该区间在 30-job heavy workload 下通常可同时改善 planning 与 communication。

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
    hardware_priority_count: 4
    candidate_path_limit: 4
    topological_order_sample_count: 4
    intensity_window_iterations: 3
    intensity_definition_mode: selected_path_max_flow_time
    priority_factor_mode: dlt_aware
    enable_priority_aware_bandwidth: true
```

## TECCL 示例

```yaml
scheduler:
  type: teccl
  teccl:
    epoch_size_ms: 1
    solver_backend: highs
    max_epoch_count: 32
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

## ECMP 示例

```yaml
scheduler:
  type: ecmp
  ecmp:
    stable_per_flow: true
```

## CrossWeaver 示例

```yaml
scheduler:
  type: crossweaver
  crossweaver:
    slot_ms: 1.0
    headroom_ratio: 0.1
    epsilon: 0.08
    gamma: 0.05
    stage1_max_iterations: 24
    stage1_binary_search_rounds: 42
    stage1_backoff_ratio: 0.9
    stage1_backoff_max_rounds: 4
    stage1_safety_margin_ratio: 0.05
    stage1_rate_smoothing_alpha: 0.7
    stage1_rate_change_cap_ratio: 0.5
    stage2_max_iterations: 32
    stage2_binary_search_rounds: 24
    stage2_softmin_temperature: 0.25
    wcmp_update_threshold_l1: 0.05
    feasibility_tolerance: 1.0e-6
    queue_wait_estimation_mode: zero
```

## 运行方式

单实验一般通过 Python 调用 `ExperimentRunner`，或被更上层脚本间接调用。对于公平矩阵批处理，不建议手写很多 experiment YAML，优先使用 `scripts/run_fair_matrix.py` 自动物化到 `configs/experiment/generated/`。

## TECCL 可行性扫描脚本

本目录提供交互式脚本 `scan_teccl_feasibility.py`，用于扫描 TECCL 参数可行性，并输出“最小可行时域 + 对应规模”。

运行方式：

```bash
python configs/experiment/scan_teccl_feasibility.py
```

脚本会按顺序交互询问：

1. 实验文件路径（必填）。
2. `epoch_size_ms` 候选值（逗号分隔；回车默认 `[15,20,25,50,100,200,500,1000]`）。
3. `max_epoch_count` 候选值（逗号分隔；回车默认 `[10,20,30,40,50,60,70,80,90,100]`）。
4. `max_solver_time_ms`（回车默认实验文件配置）。
5. `mip_gap`（回车默认实验文件配置）。
6. `solver_threads`（回车默认实验文件配置）。
7. 是否输出为文件（`y/n`）：
   - 选 `y`：继续输入输出文件路径，结果写入 JSON 文件；
   - 选 `n`：直接在终端打印完整 JSON 结果。

扫描逻辑：

- 对每个 `epoch_size_ms`，遍历所有 `max_epoch_count` 组合逐一验证。
- 其余参数默认沿用实验配置。
- 输出包含每个组合的可行性状态（`model_status`/`feasible`）和规模指标（变量数、约束数、非零元、commodity 数、destination pair 数等）。
- 汇总结果中包含：
  - 全局最小可行参数组合（按 `planning_horizon_ms` 最小优先）；
  - 每个 `epoch_size_ms` 下最小可行的 `max_epoch_count`。

## CrossWeaver 参数搜索脚本

本目录新增交互式脚本 `search_crossweaver_params.py`，用于针对“指定 topology + workload”的 CrossWeaver 实验自动搜索参数，并可回写最优参数到原实验文件。

运行方式：

```bash
python configs/experiment/search_crossweaver_params.py
```

## 相关脚本关系

- `scripts/compare_experiments.py`：负责运行多个 experiment 并统一生成对比结果；支持弹性实验数量（最少 2 个）。
- `scripts/export_teccl_solver_stats.py`：只导出 TE-CCL 求解器统计，不参与 compare 主流程，不会被 compare 脚本自动调用。

交互输入项：
1. CrossWeaver 实验文件路径（必填，`scheduler.type` 必须为 `crossweaver`）。
2. `trial` 数（默认 30）。
3. 评估 `seeds`（默认 `[base_seed, base_seed+17, base_seed+29, base_seed+43]`）。
4. 搜索方式：`bayes` 或 `random`。
5. 是否将最优参数直接回写原实验文件。

搜索与评分逻辑：
- 每个 trial 在多个 seed 上运行同一组参数，取均值与波动。
- 默认优化目标：
  - 主目标：`communication_execution_time_ms` 最小；
  - 次目标：`planning_time_ms` 较低；
  - 稳定性：通信时间标准差更小；
  - 完成率惩罚：若 `job_completion_ratio < 1.0`，大幅惩罚。
- 输出 `results/crossweaver_param_search/<exp_name>_<timestamp>/search_report.json`。

说明：
- `bayes` 使用“高斯过程 + Expected Improvement”做标准贝叶斯优化，无需额外第三方依赖。
- `random` 为纯随机扫描，适合作为 baseline。

## 书写建议

- 公平对比时，CRUX 与 TECCL 两个 experiment 只应在 `scheduler` 参数上不同。
- `metrics.output_dir` 不要复用同一目录，避免结果相互覆盖。
- 当前正式实验建议直接使用 `solver_backend: highs`，并显式控制 `max_epoch_count` 与 `max_solver_time_ms`。
- 当前正式 CRUX 实验建议显式填写 `hardware_priority_count`、`topological_order_sample_count`、`intensity_definition_mode`、`priority_factor_mode` 与 `enable_priority_aware_bandwidth`，不要只依赖旧版默认值。
- 如果 TE-CCL 运行时间过长，优先检查 `max_epoch_count`、chunk 数、目的地对数量、拓扑边数和 `mip_gap` 设置，而不是先怀疑 runtime 通信执行。
- 如果只想跑 1 次最小复现，把 `repetitions` 设为 `1`，并把 `max_time_ms` 控制在较小范围。

## 常见错误

- `scheduler.type=teccl` 但漏写 `scheduler.teccl.epoch_size_ms` 或 `solver_backend`。
- `scheduler.type=teccl` 但没有显式限制 `max_epoch_count`，导致时间展开 MILP 模型规模过大。
- `scheduler.type=crux` 但同时漏写 `scheduler.crux.max_priority_levels` 和 `scheduler.crux.hardware_priority_count`。
- `scheduler.type=crux` 但没有开启 `enable_priority_aware_bandwidth`，导致 priority 数值导出存在而运行时行为仍接近纯公平共享。
- `scheduler.type=ecmp` 但误以为会启用 CRUX 的 intensity/priority 语义；ECMP baseline 只做等价路径选路。
- `scheduler.type=crossweaver` 但漏配 `headroom_ratio/epsilon/gamma` 等关键参数，导致 MWU 或价格更新不稳定。
- `output_dir` 为空。
- `topology_file`、`workload_file` 路径写错。