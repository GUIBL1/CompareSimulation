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

构建完成后，系统会预生成 candidate_paths。CRUX 主要直接消费这些路径候选；TE-CCL 则把 topology 转成时间展开 MILP 所需的节点、链路、epoch 与 commodity 索引，再生成完整 epoch 计划。

### 2.4 离散事件执行器

真正推进仿真的是 RuntimeEngine。它的职责不是做调度，而是执行调度器给出的决策。

执行器当前的核心机制是：

1. 初始化链路状态和事件队列。
2. 在 `schedule` 事件上调用调度器。
3. 将调度结果物化为 flow。
4. 按 max-min fair 模型重新分配链路带宽。
5. 推进到下一个“调度时刻、流完成时刻或 max_time_ms”。
6. 更新 flow、link、job 的完成状态。

补充：当前 runtime 仍以 `max_min_fair` 为底层共享模型；当调度器是 CRUX 且开启 `enable_priority_aware_bandwidth` 时，会先按优先级分组，再在组内执行 max-min fair 并逐级消耗链路剩余带宽。TE-CCL 路径回放不走该优先级分组逻辑。

这意味着系统里真正被传输的是 flow，但这些 flow 的来源有两种：

1. CRUX：直接把 job/chunk 的端到端传输物化成 flow。
2. TE-CCL：先一次性求出全局 epoch 计划，再由执行器按 epoch 回放计划中的 hop 传输。

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

当前 CRUX 仍然是 job-level 调度器，但它已经不再是早期的“强度排序 + 等宽分桶”近似版，而是按 paper/crux.md 重构后的主路径实现。

它当前的核心对象是：

1. 以 UnifiedJob.compute_phase_ms 映射 $W_j$。
2. 以已选路径上的最大传输时间映射 $t_j$。
3. 计算 $I_j = W_j / t_j$。
4. 计算 $P_j = k_j I_j$。
5. 基于共享链路关系构建 contention DAG。
6. 通过多拓扑序采样 + 连续分段 DP 进行硬件优先级压缩。

### 3.2 当前 CRUX 的主要实现步骤

每次调度时，CRUX 做的是：

1. 构造 CRUX 输入模型，显式导出 job、flow、path、intensity 和 priority 对象。
2. 按初始 intensity 顺序做 path selection，让高 intensity 作业优先占据低拥塞路径。
3. 基于最终选中路径重新计算 $t_j$、$I_j$ 和 $P_j$。
4. 用最终优先级与共享链路关系构建 contention DAG。
5. 在给定 hardware priority 数下执行拓扑序采样和 DP 压缩。
6. 将压缩后的硬件优先级交给 runtime，使高优先级流先消费链路残余带宽。

当前导出也会把这条主路径拆开统计：

1. path selection time
2. priority assignment time
3. priority compression time
4. communication execution time
5. end-to-end time

### 3.3 当前 CRUX 与 TE-CCL 的边界差异

当前实现中，CRUX 与 TE-CCL 的差异不再是“一个完整、一个基线”，而是两种不同建模粒度：

1. CRUX 是 job/path/priority 主导的作业级调度。
2. TE-CCL 是 chunk/epoch/MILP 主导的时间展开调度。

CRUX 当前仍然没有引入以下 TE-CCL 式语义：

1. GPU replica 持久化状态。
2. 交换机零缓冲非复制约束的显式 MILP 变量。
3. 逐 epoch 的全局离线计划求解。

因此，当前系统比较的是两类不同调度思想在同一 runtime 底座上的表现，而不是两份同构算法实现。

## 4. TE-CCL 实现了什么

### 4.1 当前 TE-CCL 的语义边界

当前 TE-CCL 主路径已经切换为按 paper/teccl.md 落地的时间展开多商品流 MILP。它保留的关键语义是：

1. 调度粒度是 chunk commodity。
2. 规划时间轴是离散 epoch。
3. GPU 可以复制并保留已接收 chunk。
4. 交换机遵循非复制、零持久 buffer 语义。
5. 目标函数优先奖励更早完成的接收量。
6. GPU 发送不消耗副本占有：GPU buffer 递推不减发送量，发送由代表变量 U 门控。

### 4.2 当前 TE-CCL 的建模对象

当前主实现会把统一 workload 与 topology 映射到以下数学对象：

1. 节点集合、链路集合、commodity 集合与 epoch 集合。
2. 需求矩阵 $D_{s,d,c}$。
3. 流量变量 $F_{s,i,j,k,c}$。
4. 缓冲变量 $B_{s,n,k,c}$。
5. GPU 代表发送变量 $U_{s,n,k,c}$（用于近似论文中的 GPU 端 max 发送约束）。
6. 接收变量 $R_{s,d,k,c}$。

其中当前实现对应的关键关系可概括为：

1. GPU 发送门控：$F \le U$，且 $U \le B$。
2. GPU buffer 递推：$B_k = B_{k-1} + Init_k + Arrive_k$（不减 U）。
3. 交换机零缓冲与流守恒：$B=0$ 且入流=出流。

求解器不再按当前 runtime 状态逐 epoch 重新做局部候选动作选择，而是先构建完整 MILP，再一次性求出全局计划。

### 4.3 当前 TE-CCL 的正式求解后端

当前系统正式支持的 TE-CCL 主后端是 `highs`，通过 `highspy` 调用 HiGHS。

当前实验入口默认会把以下参数交给新主路径：

1. `epoch_size_ms`
2. `planning_horizon_epochs`
3. `max_solver_time_ms`
4. `mip_gap`
5. `solver_threads`
6. `enforce_integrality`
7. `objective_mode`
8. `switch_buffer_policy`

补充：当前推荐优先使用 `max_epoch_count` 作为规划 epoch 上限；`planning_horizon_epochs` 仍保留兼容，二者择一生效（优先 `max_epoch_count`）。

旧的 `small_scale_debug_solver`、`heuristic_solver` 和 `exact_milp_solver` 不再作为正式对比实验的主后端口径。

### 4.4 当前 TE-CCL 的执行方式

当前 TE-CCL 的主执行路径分为三个阶段：

1. 建模：把 topology 和 workload 转成时间展开 MILP。
2. 求解：用 HiGHS 求出完整 epoch 计划。
3. 回放：把求得的计划解码后交给 runtime 执行。

因此，当前 runtime 对 TE-CCL 的角色是“执行计划”，而不是“驱动求解”。

### 4.5 当前 TE-CCL 的时间口径

为了避免把“算计划慢”和“通信慢”混在一起，当前结果中会分开导出：

1. `teccl_model_build_time_ms`
2. `teccl_solve_only_time_ms`
3. `teccl_solver_wall_time_ms`
4. `teccl_communication_execution_time_ms`
5. `teccl_end_to_end_time_ms`

阶段 6 的回归结果已经验证了这组时间口径是有区分度的：最小案例由通信执行主导，而 dual mild 与 triple heavy 案例都明显由求解阶段主导。

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

1. 读取至少四份 experiment YAML（由参数指定）。
2. 分别运行所有实验。
3. 将原始结果写入同一输出根目录下的 run_1、run_2、...、run_n。
4. 基于多边结果生成 comparison_summary.json（participants 多侧）和一指标一图输出。
5. 写出 comparison_manifest.json，记录输入 experiment、显示标签与输出目录。

它的命令格式是：

```bash
./run_experiment_compare.sh --output-dir <output-dir> --experiment <exp1.yaml> --experiment <exp2.yaml> --experiment <exp3.yaml> --experiment <exp4.yaml> [--experiment <expN.yaml> ...] [--label <label1> --label <label2> ...] [extra compare_experiments.py args...]
```

其中最常用的额外参数是：

1. --title
2. --label-a
3. --label-b

因此，当前推荐的对比流程是优先使用这个脚本，而不是先手工分别运行两个实验，再单独调用绘图逻辑。

### 5.2 当前 compare 输出结构

标准化 compare 结果目录通常包含：

1. comparison_manifest.json
2. run_1/
3. run_2/
4. run_3/
5. run_4/
6. ...
7. comparison/

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

当前 compare 可视化层已经收敛到一组更适合直接比较调度优劣的指标。它们基本都来自三类原始数据：

1. `summary.json` 中的 repetition 聚合字段。
2. `link_load_trace.csv` 中每条链路的时间序列利用率与队列积压。
3. `flow_trace.csv` 中 flow 或逻辑传输片段的开始与完成时间。

下面按当前代码的真实输出顺序说明。

### 6.1 Completion Time（完成时间）

#### completion_time_ms

实验结束时的完成时间，单位毫秒。越小越好。

这是最核心的端到端性能指标，最适合做 CRUX 与 TE-CCL 的主表比较。

### 6.2 Job Completion Ratio（作业完成率）

#### job_completion_ratio

当前实现不是直接画 `completed_job_count`，而是用：

$$
job\_completion\_ratio = \frac{completed\_job\_count}{total\_job\_count}
$$

越大越好。

当两边在给定 `max_time_ms` 内未必都能完成全部作业时，这个比绝对完成数更稳健，也更适合直接放到对比图里。

### 6.3 Bottleneck Link 指标

当前链路类指标不是对全网简单求平均，而是先从 `link_load_trace.csv` 中找出瓶颈链路，再围绕这条链路提取峰值、均值、忙时和拥塞时长。

#### bottleneck_link_peak_utilization

瓶颈链路的峰值利用率。越接近 1，说明该链路越接近满载。

它适合回答“是否出现明显热点”。

#### bottleneck_link_average_utilization

瓶颈链路在整个时间轴上的时间加权平均利用率。

它适合回答“热点不是偶发尖峰，而是长期忙碌，还是只有瞬时冲高”。

#### bottleneck_busy_time_ms

瓶颈链路利用率大于 0 的累计时长，单位毫秒。

它反映这条最忙链路到底忙了多久，而不只是是否出现了一个峰值。

#### congestion_duration_ms

瓶颈链路利用率大于等于 0.90 的累计时长，单位毫秒。

这里的 0.90 是当前实现里的拥塞阈值。这个指标越大，说明持续拥塞越严重。

### 6.4 Queue Backlog P95 / P99（队列积压 P95 / P99）

#### queue_backlog_percentiles_mb

该指标从瓶颈链路的时间序列中提取 `queue_backlog_mb`，再按时间权重计算 P95 和 P99。

它不是“全网平均排队”，而是“最关键瓶颈链路在大部分时间里积压到了什么程度”。

这比简单看某一个时刻的最大队列更稳健，也更适合比较调度是否把拥塞长期堆积在瓶颈上。

### 6.5 Flow Completion Time P50 / P95 / P99（流完成时延 P50 / P95 / P99）

#### flow_completion_time_percentiles_ms

当前实现并不直接把每一条 hop 级 flow 都拿来比较，而是先从 `flow_trace.csv` 中按逻辑传输片段聚合完成时长，再计算 P50、P95、P99，并配 ECDF 图。

这样做的目的是尽量降低 TE-CCL hop 级物化粒度对比较的干扰，让图更接近“逻辑传输完成体验”。

### 6.6 Job Completion Time P50 / P95 / P99（作业完成时延 P50 / P95 / P99）

#### job_completion_time_percentiles_ms

该指标从 `flow_trace.csv` 中按作业聚合，取某个作业最早开始和最终完成之间的跨度，再计算 P50、P95、P99，并配 ECDF 图。

它适合看：

1. 多个作业之间是否普遍完成更快。
2. 尾部慢作业是否被显著拖长。

### 6.7 Completion Time Spread（完成时间离散度）

#### completion_time_spread_ms

当前实现用作业完成时长样本的总体标准差来定义离散度。越大说明不同作业之间完成时间更不均匀。

它适合辅助判断某个调度器是否存在“少数作业很慢，拉长尾部”的问题。

## 7. 当前不作为默认横向对比图的指标

当前 compare 代码已经明确把下面这些指标从默认图集中移除：

1. `epoch_action_count`
2. `schedule_invocation_count`
3. `total_flow_count`
4. `completed_flow_count`
5. `total_transmitted_mb`
6. `path_assignment_count`
7. `priority_assignment_count`
8. `active_link_count`
9. `total_job_count`

原因不是这些字段毫无价值，而是它们更容易受到算法内部粒度差异影响，或者更适合诊断实现本身，而不是直接判断谁调度得更好。

### 7.1 为什么 flow 和 action 类指标不默认横比

原因是 CRUX 和 TE-CCL 对“一个动作”与“一条 flow”的物化方式不同：

1. CRUX 更接近端到端业务流。
2. TE-CCL 更接近按 epoch 输出 hop 级动作后再物化成执行流。

所以 `total_flow_count`、`completed_flow_count`、`epoch_action_count` 天然就会带着实现粒度偏差。

### 7.2 为什么 total_transmitted_mb 也不再作为默认主图

这个值在带复制、转发和不同中间路径长度的场景里很容易混入“语义差异”与“实现差异”。

它仍然可以作为补充诊断字段，但不再作为默认判断优劣的首屏指标。

### 7.3 CRUX 专属字段和 TE-CCL 专属字段怎么用

像 `crux_avg_intensity_score`、`crux_priority_level_count`、`teccl_total_epoch_action_count`、`teccl_completed_replica_count` 这类字段仍然值得看，但定位应当是：

1. 解释某个算法为什么会得到当前结果。
2. 调试实现是否按预期工作。
3. 分析某类参数为什么导致性能变化。

它们不适合直接和另一类算法的私有字段做一一胜负比较。

## 8. 对比指标的使用方法

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

1. `job_completion_ratio`
2. `completion_time_ms`
3. `job_completion_time_percentiles_ms`
4. `flow_completion_time_percentiles_ms`
5. `bottleneck_link_peak_utilization` 与 `bottleneck_link_average_utilization`
6. `queue_backlog_percentiles_mb`
7. `congestion_duration_ms`

如果这些指标出现差异，再去看：

1. `scheduler_debug.json`
2. `schedule_history.json`
3. `crux_*` 或 `teccl_*` 专属字段

### 8.3 两种典型比较场景

#### 场景 A：双方都完成

这是最理想的比较场景。此时可直接比较：

1. completion_time_ms
2. job_completion_time_percentiles_ms
3. flow_completion_time_percentiles_ms
4. bottleneck_link_peak_utilization
5. bottleneck_link_average_utilization
6. completion_time_spread_ms

此时结论可以写成“在相同完成条件下，谁更快、尾部更短、热点更轻、作业间离散度更低”。

#### 场景 B：一方完成，另一方未完成

此时不应再拿 completion_time_ms 直接下性能结论，而应写成：

1. 谁在给定时间窗内完成了作业。
2. 未完成的一方推进到了什么程度。
3. 其调度行为是否存在明显卡点，例如热点链路、调度粒度过细、复制扩张过多。

在这种场景里，应重点结合：

1. job_completion_ratio
2. scheduler_debug.json
3. schedule_history.json
4. link_load_trace.csv

## 9. 一句话总结

当前系统的实现方法可以概括为：

1. 用统一输入层把 topology 和 workload 标准化。
2. 用离散事件执行器推进链路与 flow。
3. 用 CRUX 实现 job/path 级调度基线。
4. 用 TE-CCL 实现 chunk/epoch/replica 级调度。
5. 用统一指标导出层和可视化层做公平对比。

如果只记一条比较原则，那么就是：

优先比较作业完成率、完成时间、时延分位数和瓶颈链路指标，再用调度器私有字段解释原因，不要把 flow 数量或 epoch action 数直接当成 CRUX 与 TE-CCL 的主胜负指标。

## 10. CrossWeaver：性能与参数相关性说明

在当前实现里，CrossWeaver 的性能与参数选取高度相关，不能简单归因于“算法逻辑不行”。

核心原因有三点：
1. Stage I 的 `headroom_ratio/epsilon/cross_path_ecmp_k` 直接决定跨域负载是否会过早集中到少数 DCI；
2. Stage II 的 `gamma/stage2_path_split_k/path expansion` 直接决定域内拥塞是否能被摊平；
3. `queue_wait_estimation_mode` 会改变 \(Q_f\) 估计，重负载下对尾部表现影响显著。

因此，CrossWeaver 的正确使用方式不是固定一组默认参数，而是在“给定 topology + workload”下做参数搜索。

为此，当前仓库新增了交互式搜索脚本：

```bash
/home/code/miniconda3/envs/networkSimulation/bin/python configs/experiment/search_crossweaver_params.py
```

脚本会：
1. 读取你输入的 CrossWeaver experiment 文件；
2. 基于该 experiment 指向的 topology/workload 进行 trial 搜索（`bayes` 或 `random`）；
3. 在多个 seed 上评估通信时间、规划时间和完成率；
4. 输出 `search_report.json`，并可自动回写最优参数到原 experiment 文件。