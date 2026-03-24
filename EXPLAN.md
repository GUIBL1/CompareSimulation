# 联合仿真系统实现说明

## 1. 系统在做什么

这个系统是统一的 Python 离散事件仿真平台。它的目标是让不同调度策略在同一底座上运行，并在相同输入与指标口径下对比行为差异。

当前主要调度器包括：

1. CRUX（job/path/priority）
2. TE-CCL（chunk/epoch/MILP）
3. ECMP（基线选路）
4. CrossWeaver（跨域两阶段优化）

系统的统一流程是：

1. 用 YAML 描述 topology、workload、experiment。
2. 由配置加载器解析为统一内部对象。
3. 调度器输出调度决策。
4. 离散事件执行器推进 flow/link/job 状态。
5. 导出统一结果文件并生成对比图。

因此，这个平台比较的是“调度逻辑在同一执行底座中的差异”。

## 2. 系统如何实现仿真

### 2.1 输入层

系统有三类核心输入：

1. topology：节点、链路、带宽、时延、候选路径生成信息。
2. workload：作业到达、参与 GPU、通信模式、数据量、chunk 粒度与依赖。
3. experiment：指定 topology/workload/scheduler/simulation/metrics 组合。

加载层入口在 `simulator/config/loaders.py`，并对关键字段做校验。

### 2.2 统一工作负载语义

workload 会先转换到统一模型（`UnifiedJob`、`CommunicationDemand`、`Chunk`），再交给调度器。

关键语义点：

1. `participants` 定义参与 GPU 集合。
2. `communication_pattern` 决定 source/destination 生成规则。
3. `total_data_mb` 与 `chunk_count` 决定 chunk 粒度。
4. `dependency_mode` 决定 chunk 依赖方式。

### 2.3 拓扑层

拓扑支持 `generated` 与 `explicit` 两种模式，内部统一为：

1. Node
2. Link
3. TopologyGraph

构建完成后预生成 `candidate_paths`。不同调度器复用这份候选路径集合。

### 2.4 离散事件执行器

RuntimeEngine 负责“执行决策”，不是“计算策略”。

核心机制：

1. 初始化链路状态和事件队列。
2. 在调度事件触发调度器。
3. 把调度决策物化为 flow。
4. 基于带宽共享模型分配链路带宽。
5. 推进到下一关键时刻（调度点 / 流完成 / `max_time_ms`）。
6. 更新 flow/link/job 完成状态。

默认共享模型为 `max_min_fair`。当调度器为 CRUX 且 `enable_priority_aware_bandwidth=true` 时，会先按优先级分组，再在组内做 max-min fair，并逐级消耗链路剩余带宽。

### 2.5 带宽与时间推进

当前基线模型是 `max_min_fair`。若一条流分得带宽 $b$（Gbps），速率换算为：

$$
rate_{MB/ms} = 0.125 \times b
$$

流剩余大小按速率递减，降至 0 即完成。

## 3. CRUX 实现了什么

### 3.1 建模粒度

CRUX 是 job-level 调度器，关键步骤：

1. 估计每个 job 的通信时间并计算 intensity。
2. intensity 引导路径选择。
3. 计算 priority score。
4. 构建 contention DAG。
5. 进行硬件优先级压缩。
6. 输出 path 与 priority 供 runtime 执行。

### 3.2 当前主路径

单次调度中，CRUX 执行：

1. 构建 `CruxModelInput`。
2. 按 intensity 排序进行两轮 path selection。
3. 计算 raw priority。
4. 基于共享链路关系构建 DAG。
5. 用拓扑序采样 + 连续分段 DP 压缩到硬件优先级数。
6. 将压缩后优先级注入调度决策。

### 3.3 CRUX 导出时间口径

CRUX 在 summary/stats 中导出：

1. `crux_path_selection_time_ms`
2. `crux_priority_assignment_time_ms`
3. `crux_priority_compression_time_ms`
4. `crux_scheduler_wall_time_ms`
5. `crux_communication_execution_time_ms`
6. `crux_end_to_end_time_ms`

用于区分“调度构建耗时”与“通信执行耗时”。

## 4. TE-CCL 实现了什么

### 4.1 语义边界

TE-CCL 主路径是时间展开多商品流 MILP，关键语义：

1. 粒度是 chunk commodity。
2. 时间轴为离散 epoch。
3. GPU 可复制并保留已接收 chunk。
4. 交换机默认零持久 buffer（由 `switch_buffer_policy` 控制）。
5. 目标函数默认 `weighted_early_completion`。

### 4.2 建模与求解对象

实现包含：

1. commodity / node / edge / epoch 索引
2. 流量变量与缓冲状态变量
3. 约束构建与目标函数构建
4. 计划解码并回放

### 4.3 求解后端与参数兼容

当前正式对比后端为 `highs`（`highspy`）。

TE-CCL 关键参数：

1. `epoch_size_ms`
2. `max_epoch_count`
3. `max_solver_time_ms`
4. `mip_gap`
5. `solver_threads`
6. `enforce_integrality`
7. `objective_mode`
8. `switch_buffer_policy`

### 4.4 执行方式

`solver_backend=highs` 时，流程分为：

1. 建模
2. 求解
3. 将解码后的 epoch 计划交给 runtime 回放

即 runtime 在 TE-CCL 中主要承担“执行计划”角色。

### 4.5 TE-CCL 导出时间口径

当前结果区分：

1. `teccl_model_build_time_ms`
2. `teccl_solve_only_time_ms`
3. `teccl_solver_wall_time_ms`
4. `teccl_communication_execution_time_ms`
5. `teccl_end_to_end_time_ms`

用于回答“慢在求解”还是“慢在执行”。

## 5. CrossWeaver 实现了什么

CrossWeaver 是两阶段调度器：

1. Stage I：跨域速率承诺与域内映射（含 MWU 风格迭代）
2. Stage II：域内路径权重优化与完成时间驱动收敛

核心参数包括 `headroom_ratio`、`epsilon`、`gamma`、`cross_path_ecmp_k`、`stage2_path_split_k`、`queue_wait_estimation_mode` 等。

当前导出时间口径：

1. `crossweaver_stage1a_time_ms`
2. `crossweaver_stage1b_time_ms`
3. `crossweaver_stage2_time_ms`
4. `crossweaver_scheduler_wall_time_ms`
5. `crossweaver_communication_execution_time_ms`
6. `crossweaver_end_to_end_time_ms`

仓库提供交互式参数搜索脚本：

- `configs/experiment/search_crossweaver_params.py`

## 6. ECMP 基线实现

ECMP 提供轻量基线选路：

1. `stable_per_flow=true`：按 flow_id 稳定哈希选路
2. `stable_per_flow=false`：按 `(src,dst)` 轮询选路

其定位是基准对照，不包含 CRUX/TE-CCL/CrossWeaver 的建模语义。

## 7. 结果如何导出

导出入口为 `export_experiment_results`，结果受 `metrics` 开关控制。

### 7.1 条件导出规则

1. `export_json=true`：`summary.json`、`scheduler_debug.json`、`link_load_trace.json`（及调度器专项 stats）
2. `export_csv=true`：`summary.csv`、`link_load_trace.csv`
3. `export_trace=true`：`flow_trace.csv`、`schedule_history.json`

### 7.2 compare 输出

`run_experiment_compare.sh` / `scripts/compare_experiments.py` 会额外生成：

1. `comparison_manifest.json`
2. `comparison/comparison_summary.json`
3. `comparison/metric_plots/*.png`

补充：当前 compare 过程会读取每个 run 的 `summary.json`、`link_load_trace.csv` 与 `flow_trace.csv`，因此用于 compare 的实验配置建议保持 `export_json=true`、`export_csv=true`、`export_trace=true`。

## 8. 指标体系与口径

当前默认 compare 图指标为：

1. `completion_time_ms`
2. `planning_time_ms`
3. `communication_execution_time_ms`
4. `job_completion_ratio`
5. `bottleneck_link_peak_utilization`
6. `bottleneck_link_average_utilization`
7. `bottleneck_busy_time_ms`
8. `queue_backlog_percentiles_mb`
9. `flow_completion_time_percentiles_ms`
10. `job_completion_time_percentiles_ms`
11. `completion_time_spread_ms`
12. `congestion_duration_ms`

默认移除（非主胜负口径）指标包括：`epoch_action_count`、`schedule_invocation_count`、`total_flow_count`、`completed_flow_count`、`total_transmitted_mb` 等。

## 9. 对比方法与公平性

### 9.1 公平对比前提

做横向对比时应保持以下公共字段一致：

1. `inputs.topology_file`
2. `inputs.workload_file`
3. `simulation.random_seed`
4. `simulation.max_time_ms`
5. `simulation.bandwidth_sharing_model`
6. `metrics.export_json/export_csv/export_trace`

仅调整各调度器私有参数。

### 9.2 推荐分析顺序

1. 先看 `job_completion_ratio`
2. 再看 `completion_time_ms`
3. 再看作业/流时延分位数
4. 再看瓶颈链路与队列指标
5. 最后看 `scheduler_debug.json` 与专项统计解释原因

## 10. 一句话总结

当前系统可概括为：

1. 统一输入层标准化 topology/workload/experiment。
2. 统一 runtime 执行不同调度器输出。
3. 用统一结果契约导出指标与轨迹。
4. 用统一 compare 入口生成多方同口径对比图。

因此，结论应优先基于完成率、完成时间、时延分位数和瓶颈链路指标，再结合调度器私有字段做归因解释。