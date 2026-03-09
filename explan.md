# 联合仿真系统实现说明

## 1. 系统在做什么

这个系统不是论文原型代码的直接封装，而是一个统一的 Python 离散事件仿真平台。它的目标是让 CRUX 和 TE-CCL 在同一套底座上运行，然后在相同拓扑、相同工作负载、相同带宽共享模型、相同随机种子和相同指标口径下做对比。

系统当前采用的总思路是：

1. 用 YAML 文件描述拓扑、工作负载和实验参数。
2. 把这些文件加载成统一内部对象。
3. 由调度器输出调度结果。
4. 由离散事件执行器推进时间、链路和流状态。
5. 导出统一格式的结果文件和对比图。

因此，它比较的不是“两个独立系统谁更快”，而是“两个调度逻辑在同一仿真环境里的行为差异”。

## 2. 系统如何实现仿真

### 2.1 输入层

系统有三类核心输入文件：

1. topology
   描述节点、链路、链路带宽、链路时延、候选路径生成方式。
2. workload
   描述作业、参与 GPU、通信模式、数据量、chunk 数和依赖模式。
3. experiment
   指定本次实验使用哪份 topology、哪份 workload、哪种调度器以及调度参数、仿真参数和结果输出目录。

它们分别由配置加载器读取，再转换为内部对象。

### 2.2 统一工作负载语义

工作负载不会直接交给 CRUX 或 TE-CCL，而是先被转换成统一语义对象：

1. UnifiedJob
2. CommunicationDemand
3. Chunk

这样做的目的，是让两种算法共享同一份业务输入，而不是各自维护一套解释逻辑。

当前统一工作负载模型的关键点是：

1. `participants` 决定参与通信的 GPU 集合。
2. `communication_pattern` 决定 source_set 和 destination_set 的生成方式。
3. `total_data_mb` 和 `chunk_count` 决定 chunk 粒度。
4. `dependency_mode` 决定 chunk 之间是否串行、独立或 barrier 依赖。

### 2.3 拓扑层

拓扑既支持 generated，也支持 explicit。

内部统一成：

1. Node
2. Link
3. TopologyGraph

构建完成后，系统会预生成 candidate_paths。CRUX 主要直接消费这些路径候选；TE-CCL 则主要按邻接关系和局部 hop 行动来生成 epoch action。

### 2.4 离散事件执行器

真正推进仿真的是 RuntimeEngine。它的职责不是做调度，而是执行调度器给出的决策。

执行器当前的核心机制是：

1. 初始化链路状态和事件队列。
2. 在 `schedule` 事件上调用调度器。
3. 将调度结果物化为 flow。
4. 按 max-min fair 模型重新分配链路带宽。
5. 推进到下一个“调度时刻、流完成时刻或 max_time_ms”。
6. 更新 flow、link、job 的完成状态。

这意味着系统里真正被传输的是 flow，但这些 flow 的来源有两种：

1. CRUX：直接把 job/chunk 的端到端传输物化成 flow。
2. TE-CCL：先输出 epoch action，再由执行器把每个 hop action 物化成 flow。

### 2.5 带宽与时间推进

当前链路共享模型是 `max_min_fair`。

其近似逻辑是：

1. 每条 active flow 会占用其路径上的每一条链路。
2. 每条链路把带宽平均分给经过它的 active flow。
3. 每条 flow 的实际带宽取路径上最小的那一份公平份额。

若一条流的带宽是 $b$ Gbps，则其传输速率换算为 MB/ms 的公式是：

$$
rate_{MB/ms} = 0.125 \times b
$$

流的剩余大小会按这个速率随时间递减，减到 0 就被标记为 completed。

## 3. CRUX 实现了什么

### 3.1 当前 CRUX 的建模粒度

当前 CRUX 是 job-level 调度器，但会把统一工作负载中的 source-destination 需求物化成端到端 flow。

它保留了三个核心思想：

1. 用 compute/communication 比例近似 job intensity。
2. 根据 intensity 对 job 做排序和优先级压缩。
3. 在候选路径中选择相对低拥塞路径。

### 3.2 当前 CRUX 的主要实现步骤

每次调度时，CRUX 做的是：

1. 更新历史观测通信时间 `observed_comm_time_ms`。
2. 计算每个 job 的 intensity score。
3. 按 score 排序。
4. 把排序结果压缩到有限个 priority level。
5. 为每个 flow 选择一条最优候选路径。

路径选择的代价主要由三部分组成：

1. 路径上最大链路利用率。
2. 路径总 contention。
3. 路径长度。

也就是说，当前 CRUX 更像一个“作业级路径与优先级联合启发式”，而不是论文里所有机制的完整复刻。

### 3.3 当前 CRUX 没做什么

当前实现没有做以下事情：

1. 没有实现更复杂的全局最优化路径分配。
2. 没有把 GPU buffer 或 chunk replica 语义纳入调度状态。
3. 没有做 TE-CCL 那种逐 epoch 的时间展开计划。

因此，当前 CRUX 的定位是稳定、易解释的 job/path baseline。

## 4. TE-CCL 实现了什么

### 4.1 当前 TE-CCL 的语义边界

当前 TE-CCL 的实现，不是“普通 shortest path 转发”，而是保留了以下关键语义：

1. 调度粒度是 chunk replica。
2. 决策时间是 epoch。
3. GPU 可以复制，也可以缓存收到的 chunk。
4. 交换机不允许长期缓存，不承担持久副本语义。
5. 输出不是最终路径，而是一组 epoch action。

其中 GPU 和交换机的语义差异是这套实现最重要的约束之一。

### 4.2 当前 TE-CCL 的内部状态

当前 TE-CCL 维护的核心状态包括：

1. `delivered_destinations`
   哪些目的 GPU 已经收到该 replica。
2. `inflight_destinations`
   哪些目的地已经在飞行中，以及预期到达 epoch。
3. `gpu_buffers`
   哪些 GPU 当前持有该 replica，以及从哪个 epoch 开始可继续发送。
4. `switch_arrivals`
   哪些交换机已经在某个 epoch 收到该 replica，等待转发。
5. `completed_replica_ids`
   哪些 replica 已经对其所有目标完成送达。

### 4.3 当前 TE-CCL 的求解后端

当前系统支持三种求解后端：

1. `small_scale_debug_solver`
   小规模枚举搜索，用于校验语义正确性。
2. `heuristic_solver`
   贪心近似后端，适合中大规模候选空间。
3. `exact_milp_solver`
   基于 pulp/CBC 的 0-1 MILP 选择器，用于更精确地从 epoch 候选动作中选一组动作。

这里的 exact MILP 不是“整场实验一次性全局求解”，而是“每个 epoch 对当前候选动作做一次精确选择”。

### 4.4 当前 exact MILP 实现方式

当前 MILP 后端解决的问题是：

在某个 epoch，给定所有候选动作，选择一组动作，使得目标送达收益最大，同时满足约束。

当前约束主要包括：

1. 每个候选组最多选一个动作。
2. 同一 replica 对同一 ultimate destination 不能重复选。
3. 在禁止 switch replication 时，同一交换机本 epoch 至多选一个 switch action。
4. 同一 `(current_node, next_node)` hop 在本 epoch 不能重复选。
5. 已经物化过的同一 epoch-hop-flow 不允许再次被选中。

当前目标函数是一个工程化目标，而不是论文原式逐项复刻。它优先奖励：

1. 产生有效送达推进的动作。
2. 更直接的送达。
3. GPU 侧复制动作。
4. 更早到达的动作。
5. 更短的 route fragment。

因此，它更准确地说是“精确求解当前 epoch 的动作选择问题”。

### 4.5 当前 TE-CCL 没做什么

当前实现还没有做到：

1. 全时域联合 MILP。
2. 严格论文级完整变量和完整约束系统。
3. 更复杂的交换机级多资源约束。
4. 更细粒度的 buffer 容量竞争模型。

所以，当前 TE-CCL 是“语义完整优先、求解后端渐进增强”的实现路线。

## 5. 结果是如何导出的

每次实验结束后，系统会统一导出：

1. `summary.json`
2. `summary.csv`
3. `link_load_trace.csv`
4. `link_load_trace.json`
5. `flow_trace.csv`
6. `schedule_history.json`
7. `scheduler_debug.json`

其中最关键的是：

1. `summary.json`
   用于看聚合指标和每次 repetition 摘要。
2. `schedule_history.json`
   用于看调度行为随时间如何变化。
3. `scheduler_debug.json`
   用于解释调度器内部状态。
4. `link_load_trace.csv`
   用于看链路利用率曲线。

### 5.1 标准化 compare 入口

当前仓库主目录提供了 run_experiment_compare.sh，作为统一的对比入口。

它内部调用 scripts/compare_experiments.py，按如下顺序工作：

1. 读取两份 experiment YAML。
2. 分别运行 experiment-a 和 experiment-b。
3. 将两边原始结果写入同一输出根目录下的 run_a 和 run_b。
4. 基于两边结果生成 comparison_summary.json 和一指标一图输出。
5. 写出 comparison_manifest.json，记录输入 experiment、显示标签与输出目录。

它的命令格式是：

```bash
./run_experiment_compare.sh <experiment-a.yaml> <experiment-b.yaml> <output-dir> [extra compare_experiments.py args...]
```

其中最常用的额外参数是：

1. --title
2. --label-a
3. --label-b

因此，当前推荐的对比流程是优先使用这个脚本，而不是先手工分别运行两个实验，再单独调用绘图逻辑。

### 5.2 当前 compare 输出结构

标准化 compare 结果目录通常包含：

1. comparison_manifest.json
2. run_a/
3. run_b/
4. comparison/

其中 comparison/ 下最关键的是：

1. comparison_summary.json
2. metric_plots/

metric_plots 使用一指标一图的组织方式，当前主图通常包括：

1. completion_time_ms
2. job_completion_ratio
3. bottleneck_link_peak_utilization
4. bottleneck_link_average_utilization
5. bottleneck_busy_time_ms
6. queue_backlog_percentiles_mb
7. flow_completion_time_percentiles_ms
8. job_completion_time_percentiles_ms
9. completion_time_spread_ms
10. congestion_duration_ms

## 6. 各指标的意义

下面把指标分成三类：

1. 可直接横向比较的主指标。
2. 可辅助解释的行为指标。
3. 只适合算法内部分析、不宜直接横向比较的诊断指标。

### 6.1 主指标

#### completion_time_ms

实验结束时的完成时间，单位毫秒。越小越好。

这是最核心的端到端性能指标，最适合做 CRUX 与 TE-CCL 的主表比较。

#### completed_job_count

已经完成的作业数。越大越好。

如果两个实验设置了相同的作业集合，那么这个指标可以直接反映谁更完整地完成了任务。

#### total_transmitted_mb

全网累计发送的数据量。

它反映调度过程中网络一共搬运了多少数据。这个指标本身不是越小越好也不是越大越好，要结合任务完成情况和复制语义解释。

若两个算法都完成了相同任务，而其中一个的 transmitted 更高，通常说明它付出了更多中间转发或复制代价。

#### average_link_utilization

链路时间加权平均利用率。

它反映整体网络资源使用程度。适合辅助解释“为什么完成时间快或慢”。

#### max_link_utilization

最热点链路的最大利用率。

它反映是否存在明显瓶颈链路。如果某算法完成慢且热点更尖锐，通常说明其流量更集中。

#### active_link_count

实际参与传输的链路数量。

它反映流量分散程度和路径铺开程度，但只能辅助解释，不能单独作为优劣结论。

### 6.2 行为指标

#### schedule_invocation_count

调度器被调用的次数。

CRUX 倾向于较少次重调度，TE-CCL 因为按 epoch 工作，通常会更多。这能解释两者控制开销和决策粒度差异。

#### epoch_action_count

所有调度轮次中输出的 epoch action 总数。

对 TE-CCL 尤其重要，因为它直接反映动作计划密度。CRUX 通常为 0，因为它不按 epoch action 运行。

### 6.3 CRUX 专属指标

#### crux_avg_observed_comm_time_ms

CRUX 对作业通信时长的观测均值。用于解释其 intensity 估计依据。

#### crux_avg_intensity_score

CRUX 内部 intensity 分数的均值。值越高，说明 compute 相对 communication 更重。

#### crux_path_assignment_count

CRUX 当前路径分配条目数。反映本轮路径层面的决策规模。

#### crux_priority_level_count

当前实际用到的优先级层数。反映优先级压缩是否真的区分出了多个等级。

### 6.4 TE-CCL 专属指标

#### teccl_solver_backend

当前 TE-CCL 用的是哪个求解后端，例如 `heuristic_solver` 或 `exact_milp_solver`。

#### teccl_epoch_size_ms

每个 epoch 的时长。越小代表调度更细，但控制开销也可能更高。

#### teccl_solver_report_count

solver report 的数量。当前通常接近作业数，主要用于确认求解器确实工作了。

#### teccl_total_epoch_action_count

TE-CCL 在整次实验中输出的总动作数。可用于衡量计划复杂度。

#### teccl_replica_count

当前建模出来的 replica 总数。

#### teccl_completed_replica_count

已经完成的 replica 数。它比 `completed_flow_count` 更贴近 TE-CCL 本身的语义完成度。

## 7. 哪些指标不能直接横向比较

这一点很重要。

### 7.1 completed_flow_count 不能直接当成跨算法主指标

原因是 CRUX 和 TE-CCL 对 flow 的物化方式不同：

1. CRUX 的 flow 更接近端到端业务流。
2. TE-CCL 的 flow 是由 epoch action 物化出来的 hop 级动作流。

因此，TE-CCL 的 flow 数可能天然更多。它反映的是执行粒度，不等价于业务完成量。

这个指标可以看，但不适合单独用来下“谁更好”的结论。

### 7.2 epoch_action_count 也不能直接和 CRUX 横比

CRUX 本来就不是按 epoch action 工作，所以它天然偏低甚至为 0。这个指标应该被看作 TE-CCL 的控制复杂度指标，而不是通用性能指标。

### 7.3 priority_level_count 和 replica_count 不能互相比

这两类指标分别属于不同算法的内部机制。它们用于解释算法行为，不用于直接比较优劣。

## 8. 对比指标的对比方法

### 8.1 先保证公平性

只有在以下字段一致时，对比才成立：

1. `topology_file`
2. `workload_file`
3. `random_seed`
4. `simulation.max_time_ms`
5. `simulation.bandwidth_sharing_model`
6. `metrics.export_json`
7. `metrics.export_csv`
8. `metrics.export_trace`

允许不同的只有算法私有参数。

### 8.2 先比主指标，再看解释指标

推荐的比较顺序是：

1. `completed_job_count`
2. `completion_time_ms`
3. `total_transmitted_mb`
4. `average_link_utilization`
5. `max_link_utilization`

如果主指标出现差异，再去看：

1. `schedule_invocation_count`
2. `epoch_action_count`
3. `crux_*` 或 `teccl_*` 专属指标

### 8.3 两种典型比较场景

#### 场景 A：双方都完成

这是最理想的比较场景。此时可直接比较：

1. completion_time_ms
2. total_transmitted_mb
3. average_link_utilization
4. max_link_utilization

此时结论可以写成“在相同完成条件下，谁更快、谁更省网络搬运、谁的热点更严重”。

#### 场景 B：一方完成，另一方未完成

此时不应再拿 completion_time_ms 直接下性能结论，而应写成：

1. 谁在给定时间窗内完成了作业。
2. 未完成的一方推进到了什么程度。
3. 其调度行为是否存在明显卡点，例如热点链路、调度粒度过细、复制扩张过多。

在这种场景里，应重点结合：

1. completed_job_count
2. scheduler_debug.json
3. schedule_history.json
4. link_load_trace.csv

### 8.4 建议的汇报模板

如果要给出一段标准结论，推荐按下面格式写：

1. 说明公共输入是否一致。
2. 说明双方是否都完成。
3. 给出完成时间对比。
4. 给出网络开销和链路利用率对比。
5. 若有必要，再给出行为解释。

例如：

> 在相同 topology、workload、random_seed 和 max-min fair 带宽共享模型下，CRUX 与 TE-CCL 均完成了该实验。CRUX 的 completion_time_ms 更低，但 TE-CCL 的调度粒度更细，产生了更多 epoch action 和更多 hop 级 flow。若关注端到端完成时间，应以 completion_time_ms 为主结论；若关注动作计划复杂度，则需要额外参考 epoch_action_count 和 teccl_total_epoch_action_count。

## 9. 当前仓库中两类 inter-DC 实验应该怎么读

### 9.1 inter_dc_mild

这一组用于“双方都完成时的纯性能对比”。

推荐重点看：

1. `completed_job_count`
2. `completion_time_ms`
3. `total_transmitted_mb`
4. `average_link_utilization`

### 9.2 inter_dc_broadcast

这一组更像压力场景。它用于看较重负载下两种调度逻辑在跨 DC 链路上的行为差异。

这里除了主指标，还建议重点看：

1. `schedule_invocation_count`
2. `epoch_action_count`
3. `comparison_link_utilization.png`
4. `comparison_scheduler_activity.png`

## 10. 一句话总结

当前系统的实现方法可以概括为：

1. 用统一输入层把 topology 和 workload 标准化。
2. 用离散事件执行器推进链路与 flow。
3. 用 CRUX 实现 job/path 级调度基线。
4. 用 TE-CCL 实现 chunk/epoch/replica 级调度。
5. 用统一指标导出层和可视化层做公平对比。

如果只记一条比较原则，那么就是：

优先比较作业是否完成和完成时间，再用流量、利用率和调度行为指标解释原因，不要把 flow 数量直接当成 CRUX 与 TE-CCL 的主胜负指标。