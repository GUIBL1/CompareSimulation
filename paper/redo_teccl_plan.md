
# TE-CCL 全量重构计划
**所有操作均在conda activate networkSimulation虚拟环境下进行：/home/code/miniconda3/envs/networkSimulation**

## 1. 目标

本计划用于指导当前仓库中的 TE-CCL 实现进行一次彻底重构。重构后的 TE-CCL 不再沿用现有的“候选动作枚举 + epoch 内二进制选择”近似实现，而是严格按照 [paper/teccl.md](paper/teccl.md) 中给出的建模说明，构建真正的时间展开多商品流 MILP。

本次重构的总目标是：

1. 严格按 TE-CCL 建模说明实现变量、约束和目标函数。
2. 使用完全免费开源且性能较好的 MILP 求解器 HiGHS 作为唯一正式求解后端。
3. 将求解时间与流量执行时间分开统计，避免把“求解慢”和“通信慢”混在一起。
4. 记录求解时对应的负载规模与模型规模，便于分析性能瓶颈。
5. 将重构过程拆成明确阶段，便于开发时跟踪进度、定位问题和做阶段验收。

本文件是后续 TE-CCL 重构工作的唯一基准计划。后续实现、验收与文档更新，均以本文件为准。

## 2. 当前实现为什么必须重构

当前仓库中的 TE-CCL 主路径实现，本质上是一个工程化的 epoch 候选动作选择器：

1. 先基于当前 replica 状态枚举可行动作。
2. 再在单个 epoch 内用启发式或 0-1 选择器选出一组动作。
3. 依赖 gpu_buffers、switch_arrivals、inflight_destinations 等状态机推进数据传播。

这套实现存在以下根本偏差：

1. 没有显式建模全时域的链路流量变量。
2. 没有显式建模节点缓冲区变量。
3. 没有显式建模目的节点接收变量。
4. 没有把容量约束、流守恒约束、缓冲区更新约束、目的地满足约束统一放入同一个 MILP。
5. 当前 exact_milp_solver 只是“候选动作选择 MILP”，不是论文式多商品流 MILP。

因此，这次重构不是局部补丁，也不是继续扩展旧 exact_milp_solver，而是替换 TE-CCL 的求解核心与执行对接方式。

## 3. 重构后的目标实现形态

重构完成后，TE-CCL 应具备以下结构：

1. 从 unified workload 中抽取需求矩阵 $D_{s,d,c}$。
2. 从 topology 中抽取有向图 $G=(N,E)$、链路带宽 $T_{ij}$ 和链路时延 $\alpha_{ij}$。
3. 以 epoch 为时间维度构建完整时间展开 MILP。
4. 显式定义并求解以下核心变量：
	- $F_{s,i,j,k,c}$：epoch $k$ 上从 $i$ 到 $j$ 的块流量。
	- $B_{s,n,k,c}$：epoch $k$ 时节点 $n$ 的缓冲区持有量。
	- $R_{s,d,k,c}$：epoch $k$ 时目的节点的接收量。
5. 用 HiGHS 求解完整模型。
6. 将求解结果转成仿真系统可执行的 epoch 计划和 flow 执行计划。
7. 在 metrics 中分开导出：
	- MILP 求解时间
	- 调度计划执行对应的通信时间
	- 求解时的需求规模、变量规模、约束规模、拓扑规模

## 4. 建模原则

### 4.1 忠实于说明，不做语义偷换

以下内容必须严格保留：

1. chunk 是离散商品，不退化为连续“总流量”近似。
2. epoch 是明确的离散时间单位。
3. GPU 支持复制。
4. 交换机不支持复制。
5. 交换机不承担持久缓冲区语义，默认 $B_{s,n,k,c}=0$。
6. 优化目标是优先让数据更早完成，而不是简单最小化某个局部代价函数。

### 4.2 先建模，再执行

重构后的 TE-CCL 主路径应遵循：

1. 先构建完整 MILP。
2. 再求解得到时序调度结果。
3. 再把调度结果交给仿真运行时执行。

不能再让运行时状态机反过来主导求解器的语义。

### 4.3 求解时间与通信时间必须分离

TE-CCL 是“先算计划，再执行计划”的调度器，因此必须把两部分时间拆开：

1. Solver Time
	表示 HiGHS 构建和求解 MILP 的耗时。
2. Communication Execution Time
	表示在仿真系统中执行已求得调度计划所经历的通信时间。

最终报告中不能只给一个 completion_time_ms，而必须能回答：

1. TE-CCL 算计划花了多久。
2. 按计划执行通信花了多久。
3. 两者相加后的端到端代价是多少。

## 5. 求解器选型

### 5.1 正式求解器

本次重构统一采用 HiGHS 作为 TE-CCL 的正式 MILP 求解器，Python 接口使用 highspy。

原因如下：

1. 时间展开多商品流 MILP 的规模会显著高于当前候选动作选择问题。
2. HiGHS 是完全免费开源的高性能优化求解器，在开源 MIP 求解器中实现成熟、性能较好、部署成本低，适合作为本项目默认正式后端。
3. 相比当前 pulp/CBC 路线，直接接入 HiGHS 原生接口更利于控制求解参数、提取求解统计信息，并统一后续 profiling 输出。

### 5.2 不再使用的主后端

下列后端不再作为新的 TE-CCL 主实现：

1. pulp/CBC exact_milp_solver
2. 当前 small_scale_debug_solver
3. 当前 heuristic_solver

这些实现删除。

### 5.3 HiGHS 接入要求

重构时必须同时落实以下能力：

1. 检测 highspy 是否可用。
2. 支持 time limit。
3. 支持 MIP gap 配置。
4. 支持线程数配置。
5. 支持 solver 状态导出。
6. 支持目标值、最优界、gap、求解时间导出。
7. 支持无解时导出 LP 或 MPS 模型、solver log 和关键约束摘要，至少输出可操作的不可行性诊断信息。

## 6. 新实现的模块设计

### 6.1 模块划分

重构后建议 TE-CCL 至少拆成以下模块：

1. teccl_model_input.py
	负责把 unified workload 和 topology 转换成 MILP 输入对象。
2. teccl_indexing.py
	负责生成 commodity、epoch、node、edge 等索引。
3. teccl_milp_builder.py
	负责创建变量、约束和目标函数。
4. teccl_highs_backend.py
	负责调用 HiGHS 求解并返回标准化解对象。
5. teccl_solution_decoder.py
	负责把 MILP 解码成可执行的 epoch 计划。
6. teccl_runtime_adapter.py
	负责把计划适配到当前 runtime engine。
7. teccl_metrics.py
	负责记录求解阶段与执行阶段的独立指标。

### 6.2 主调度器职责

TECCLScheduler 的职责要收敛成：

1. 接收统一 workload。
2. 构造 TE-CCL 输入模型。
3. 调用 HiGHS 后端求解。
4. 把解结果缓存为完整计划。
5. 按 epoch 把计划分发给 runtime。
6. 导出调试状态、求解统计和执行统计。

它不再负责：

1. 基于当前状态枚举候选 hop。
2. 维护近似的 switch_arrivals / inflight_destinations 作为求解核心。
3. 在每个 epoch 重新做局部动作最优化。

## 7. 数学模型落地计划

### 7.1 输入对象映射

这一阶段要把说明中的数学量映射到代码对象：

1. 节点集合 $N$
2. 链路集合 $E$
3. 商品集合 $C$
4. epoch 集合 $K$
5. 需求矩阵 $D_{s,d,c}$
6. 链路带宽 $T_{ij}$
7. 链路时延 $\alpha_{ij}$
8. epoch 时长 $\tau$
9. 传播延迟换算 $\delta_{ij}=\alpha_{ij}/\tau$

要求：

1. 所有这些对象必须从外部输入和内部统一模型中推导出来，不能硬编码。
2. 数学索引必须能稳定映射回原始 job / chunk / node / link。

### 7.2 变量设计

本次重构将正式引入以下变量：

1. $F_{s,i,j,k,c}$
	表示 epoch $k$ 时，商品 $(s,c)$ 在链路 $(i,j)$ 上的发送量。
2. $B_{s,n,k,c}$
	表示 epoch $k$ 时节点 $n$ 对商品 $(s,c)$ 的缓冲区持有量。
3. $R_{s,d,k,c}$
	表示目的节点 $d$ 在 epoch $k$ 已接收的商品量。

如有必要，可引入以下辅助变量：

1. 用于线性化 GPU 复制约束的辅助变量。
2. 用于表达目的地接收上界或最终满足条件的辅助变量。
3. 用于刻画分块不可分时的整数控制变量。

### 7.3 约束设计

本次重构必须完整实现四类核心约束。

#### 7.3.1 容量约束

对每条链路 $(i,j)$、每个 epoch $k$：

$$
\sum_{s}\sum_{c}F_{s,i,j,k,c} \le T_{ij}\tau
$$

要求：

1. 单位换算一致。
2. 链路方向明确。
3. bidirectional 链路若内部拆成两条有向边，必须分别约束。

#### 7.3.2 GPU 节点流守恒约束

按说明中的 GPU 可复制语义实现，不允许退化成交换机式守恒。

说明里的 max 形式进入 MILP 时，需要做等价线性化。线性化必须满足两点：

1. GPU 只有在持有 chunk 时才能向外发送。
2. GPU 可以同时把同一个 chunk 发往多条出边。

实现时必须把线性化推导写进代码注释或配套文档，避免后续维护时失真。

#### 7.3.3 交换机流守恒约束

对交换机节点，必须实现：

$$
\sum_{j:(j,n)\in E}F_{s,j,n,k,c} = \sum_{j:(n,j)\in E}F_{s,n,j,k,c}
$$

同时默认：

$$
B_{s,n,k,c} = 0
$$

不允许再通过工程近似让交换机表现得像“可短暂保留并下个 epoch 再发”的持久缓冲节点。

#### 7.3.4 缓冲区更新约束

必须实现按延迟到达的 buffer 更新关系，而不是仅凭 runtime 中 flow 完成事件回写状态：

$$
B_{s,n,k,c} = B_{s,n,k-1,c} + \sum_{j:(j,n)\in E}F_{s,j,n,k-\lceil\delta_{jn}\rceil-1,c}
$$

实现要点：

1. 处理好负时间索引的边界条件。
2. 初始 buffer 条件必须清晰定义。
3. GPU 源节点初始持有数据必须显式建模。

#### 7.3.5 目的地约束

必须同时保证：

1. 接收量不能超过需求。
2. 最终 epoch 满足需求完成。

说明中的

$$
R_{s,d,k,c}=\min(D_{s,d,c}, B_{s,d,k+1,c})
$$

在 MILP 中不能直接原样写入，需要做线性化或等价改写。实现时必须给出清晰的线性形式。

### 7.4 目标函数

目标函数按说明落实为：

$$
\max \sum_{k\in K}\sum_{s,d\in N:s\ne d}\frac{1}{k+1}R_{s,d,k}
$$

如果在最终实现中 $R$ 保留 chunk 维度，则应保持完全等价的展开形式，而不是替换成其他工程化打分函数。

## 8. 求解结果与运行时对接计划

### 8.1 解码层

MILP 解求出来后，不能直接把变量暴露给 runtime。需要增加专门的解码层，把解转成标准化计划对象。

计划对象至少包括：

1. 每个 epoch 的链路发送计划。
2. 每条发送计划对应的 commodity 信息。
3. 源节点、目的节点、链路、数据量、开始 epoch、到达 epoch。
4. 对应原始 job / chunk / demand 的回溯标识。

### 8.2 执行层对接

当前 runtime engine 仍可复用，但对接方式要调整为：

1. runtime 不再驱动 TE-CCL 求解。
2. runtime 只负责执行已求出的计划。
3. TE-CCL 的调度阶段与执行阶段在时间统计上完全拆开。

### 8.3 执行正确性要求

执行层必须满足：

1. 已解出的计划在 runtime 中不应再次被 scheduler 改写。
2. 执行阶段不再引入与 MILP 不一致的二次局部路由逻辑。
3. 对于计划内每条发送，runtime 只负责根据链路与时延模拟完成时间。

## 9. 指标与统计设计

这是本次重构必须新增的重点内容。

### 9.1 分离求解时间与通信时间

TE-CCL 结果中必须新增以下指标：

1. teccl_solver_wall_time_ms
	HiGHS 求解总耗时。
2. teccl_model_build_time_ms
	模型构建耗时。
3. teccl_solve_only_time_ms
	真正调用 HiGHS optimize 的耗时。
4. teccl_communication_execution_time_ms
	计划交给 runtime 执行后的通信耗时。
5. teccl_end_to_end_time_ms
	求解时间与执行时间之和。

说明：

1. completion_time_ms 继续保留，但要明确它属于执行阶段完成时间还是端到端总时间。
2. 默认报告中至少要同时给出 execution time 和 end-to-end time。

### 9.2 记录求解时的负载信息

每次 TE-CCL 求解时，必须同时记录该次求解对应的负载规模，便于后续分析“为什么求解慢”。

至少记录：

1. job_count
2. demand_count
3. chunk_count
4. commodity_count
5. source_gpu_count
6. destination_pair_count
7. epoch_count
8. node_count
9. edge_count
10. total_demand_mb
11. average_chunk_mb
12. max_chunk_mb
13. inter_dc_edge_count
14. candidate_horizon_ms 或 planning_horizon_epochs

### 9.3 记录模型规模

每次求解还必须导出：

1. variable_count
2. binary_variable_count
3. integer_variable_count
4. continuous_variable_count
5. constraint_count
6. non_zero_count（若可获取）
7. solver_status
8. objective_value
9. best_bound
10. mip_gap
11. node_explored_count

### 9.4 记录输出位置

上述求解统计建议至少进入以下位置：

1. summary.json
2. scheduler_debug.json
3. 单独新增 teccl_solver_stats.json

其中 teccl_solver_stats.json 应尽量完整，以便后续做 profiling。

## 10. 配置重构计划

当前 TE-CCL 配置项不足以支撑新模型，需新增或重定义以下字段：

1. solver_backend
	固定支持 highs。
2. epoch_size_ms
3. planning_horizon_epochs
4. max_solver_time_ms
5. mip_gap
6. solver_threads
7. enforce_integrality
8. objective_mode
9. switch_buffer_policy
10. export_solver_stats
11. export_full_milp_debug

对旧配置字段的处理原则：

1. 旧字段若与新模型冲突，则明确废弃。
2. 旧字段若还能表达同一语义，则做兼容映射。
3. 不允许保留会误导用户的旧字段名或旧行为。

## 11. 分阶段执行计划

以下阶段必须按顺序推进，每一阶段都要完成后再进入下一阶段。

### 阶段 0：基线冻结与接口勘察

目标：冻结当前 TE-CCL 代码路径，明确哪些代码保留、哪些转入 legacy。

工作内容：

1. 标记当前 teccl.py 与 teccl_solver.py 的旧实现边界。
2. 识别必须复用的 runtime / exporter / metrics 接口。
3. 梳理现有 experiment 配置与结果导出契约。

验收标准：

1. 输出一份旧路径保留清单。
2. 输出新旧接口影响面清单。

#### 阶段状态

状态：已完成

执行时间：2026-03-10

#### 阶段 0 产出 A：旧路径保留清单

以下内容确认保留并在后续重构中复用：

1. 通用调度器接口层
	- `simulator/schedulers/base.py` 中的 `Scheduler`、`ScheduleDecision` 仍作为统一调度器契约保留。
2. 通用运行时状态层
	- `simulator/core/models.py` 中的 `RuntimeState`、`FlowState`、`LinkState` 继续保留。
3. 运行时主循环与 max-min fair 执行框架
	- `simulator/core/engine.py` 的事件推进、链路带宽分配、flow 完成推进逻辑保留。
4. 实验装配入口
	- `simulator/experiment/runner.py` 中的 `ExperimentRunner`、scheduler 装配入口与 repetition 运行契约保留。
5. 配置系统主结构
	- `simulator/config/models.py` 与 `simulator/config/loaders.py` 的 experiment/topology/workload 基础结构保留，但 TE-CCL 参数校验将扩展。
6. 结果导出主契约
	- `simulator/metrics/exporters.py` 的 `summary.json`、`scheduler_debug.json`、`link_load_trace.csv/json`、`flow_trace.csv`、`schedule_history.json` 输出主框架保留。
7. 结果归因与可视化读取入口
	- `simulator/metrics/reporting.py` 和 `simulator/metrics/visualization.py` 的结果读取入口保留，但 TE-CCL 字段解析需要更新。

以下内容确认不再作为新 TE-CCL 正式实现的主路径，后续将删除或转入 legacy：

1. `simulator/schedulers/teccl.py` 中基于 replica 状态机的主求解路径。
2. `TECCLChunkReplicaState`、`TECCLJobState` 这套以 `gpu_buffers`、`switch_arrivals`、`inflight_destinations` 为核心的近似求解状态。
3. `_synchronize_job_state`、`_select_switch_destination`、`_select_gpu_destinations`、`_build_epoch_actions` 等旧路径局部动作生成逻辑。
4. `simulator/schedulers/teccl_solver.py` 中的 `SmallScaleDebugSolver`、`HeuristicTECCLSolver`、`ExactMILPTECCLSolver` 全部旧实现。
5. 旧的候选动作选择 MILP，也就是当前基于 `pulp/CBC` 的 `exact_milp_solver`。
6. `simulator/experiment/batch.py` 中默认 `solver_backend=small_scale_debug_solver` 的 TE-CCL 批量实验默认块。

#### 阶段 0 产出 B：新旧接口影响面清单

已确认的关键接口影响如下。

1. 调度器输出契约受影响最大
	- 当前 `ScheduleDecision` 里的 `epoch_actions` 是 TE-CCL 被 runtime 执行的主要载体。
	- 新实现将不再以“单 hop 动作枚举”为核心，因此需要新增计划解码层，并决定是扩展 `ScheduleDecision` 还是新增计划对象后再适配到运行时。

2. RuntimeEngine 中有两处 TE-CCL 专用逻辑必须改造
	- `_apply_schedule_decision()` 当前发现 `decision.epoch_actions` 后会走 `_materialize_epoch_action()`。
	- `_update_completed_jobs_from_decision()` 当前依赖 `decision.metadata.job_states` 内的 `pending_destinations`、`inflight_destinations`、`switch_arrivals` 判断 TE-CCL 作业完成。
	- 这两处都直接绑定了旧 TE-CCL 状态机语义，后续必须改为消费新的 MILP 计划与执行统计。

3. ExperimentRunner 的装配方式可复用，但 TE-CCL strategy 字段会变化
	- `ExperimentRunner._create_scheduler()` 当前直接用 `TECCLStrategy(**scheduler_config.teccl)` 装配旧调度器。
	- 后续可以保留装配入口，但 `TECCLStrategy` 字段定义、默认值和校验规则需要重写。

4. 配置校验目前只覆盖旧 TE-CCL 最小参数
	- `loaders.py` 目前只强制要求 `epoch_size_ms` 和 `solver_backend`。
	- 新模型至少还要扩展到 `planning_horizon_epochs`、`mip_gap`、`solver_threads`、`export_solver_stats` 等字段。

5. 导出层当前强依赖旧 TE-CCL 调试结构
	- `exporters.py` 中 `_build_teccl_metrics()` 直接读取 `strategy.solver_backend`、`solver_reports`、`job_states`、`completed_replica_ids`、`epoch_action_count`。
	- 新实现要改成读取正式 solver stats、模型规模、求解时间、执行时间与计划摘要。

6. 归因报告层当前强依赖旧 epoch action 语义
	- `reporting.py` 中 `_build_phase_timing_summary()`、`_build_epoch_action_summary()` 依赖 `schedule_history` 的 `epoch_action_count` 与 `solver_reports.selected_candidates`。
	- 新 TE-CCL 不应再以旧候选动作选择器为中心，因此这部分后续需要改成“求解阶段 + 执行阶段”的双阶段摘要。

7. 批量实验默认配置会阻塞新实现接入
	- `experiment/batch.py` 当前默认 TE-CCL backend 是 `small_scale_debug_solver`。
	- 阶段 6 回归前必须改成新的 `highs` 正式后端配置块。

#### 阶段 0 结论

阶段 0 已完成并得到两个明确结论：

1. 可复用的稳定边界主要是：调度器抽象、运行时主循环、实验装配入口、配置系统主结构、结果导出主契约。
2. 必须整体替换的核心边界主要是：`simulator/schedulers/teccl.py`、`simulator/schedulers/teccl_solver.py`、runtime 中两处 TE-CCL 专用物化/完成判断逻辑，以及 exporters/reporting 中对旧 TE-CCL 调试结构的解析。

#### 阶段 0 已验证结果

1. 旧 TE-CCL 主路径确实绑定在 `epoch_actions + job_states + solver_reports` 这组三元输出契约上。
2. RuntimeEngine、exporters、reporting 三层都已经直接依赖旧 TE-CCL 状态机语义，后续重构不能只换求解器，必须同步改造执行与导出层。
3. 配置与批量实验入口目前仍默认旧 backend 名称，若不先改造这些入口，新实现无法无缝接入现有实验体系。

### 阶段 1：数学模型到代码索引映射

目标：完成数学对象和代码对象的确定映射。

工作内容：

1. 定义 commodity 索引。
2. 定义时间展开索引。
3. 定义链路容量与时延换算。
4. 定义初始 buffer 与需求矩阵构造方式。

验收标准：

1. 小规模样例能打印完整索引。
2. 输入对象和索引对象之间能双向回溯。

#### 阶段状态

状态：已完成

执行时间：2026-03-10

#### 阶段 1 产出 A：新增代码模块

本阶段已新增以下模块：

1. `simulator/schedulers/teccl_indexing.py`
	- 定义 `TECCLEpoch`、`TECCLDirectedEdge`、`TECCLCommodity`、`TECCLNodePartition`、`TECCLIndexBundle`。
	- 实现 `build_node_partition()`。
	- 实现 `build_directed_edge_index()`。
	- 实现 `build_epoch_index()`。
	- 实现 `build_commodity_index()`。
	- 实现 `build_teccl_index_bundle()`。

2. `simulator/schedulers/teccl_model_input.py`
	- 定义 `TECCLDemandEntry`、`TECCLInitialBufferEntry`、`TECCLModelInput`。
	- 实现 `build_teccl_model_input()`。
	- 实现 `infer_planning_horizon_epochs()`。
	- 将需求矩阵、初始 buffer、链路容量、链路时延 epoch 化映射为正式 MILP 输入对象。

3. `simulator/schedulers/__init__.py`
	- 已导出上述阶段 1 模块的公共入口，便于后续 builder 与验证代码直接复用。

#### 阶段 1 产出 B：数学对象到代码对象的映射结论

本阶段已明确以下数学对象映射。

1. 节点集合 $N$
	- 由 `TopologyGraph.nodes` 映射而来。
	- 当前进一步拆分为：
	  - `gpu_nodes`
	  - `switch_nodes`
	  - `relay_nodes`
	- 其中 `relay_nodes` 用来承接生成式拓扑中的 host 等非 GPU、非 switch 节点，避免在阶段 1 就丢失拓扑信息。

2. 链路集合 $E$
	- 由 `TopologyGraph.links` 展开成有向边集合。
	- 若物理链路 `bidirectional=true`，则在索引层生成两条 `TECCLDirectedEdge`。
	- 每条有向边都显式记录：
	  - `src`
	  - `dst`
	  - `bandwidth_gbps`
	  - `latency_us`
	  - `delay_epochs`
	  - `capacity_mb_per_epoch`

3. 商品集合 $C$
	- 当前定义为“每个 chunk 在每个 source node 上形成一个 commodity”。
	- 即对统一工作负载中的每个 `chunk` 和每个 `source_set` 元素，构造一个 `TECCLCommodity`。
	- 其稳定标识格式为：`{chunk_id}::{source_node}`。

4. 需求矩阵 $D_{s,d,c}$
	- 对每个 `TECCLCommodity`，对其所有 `destination_nodes` 生成 `TECCLDemandEntry`。
	- 当前矩阵键定义为 `(source_node, destination_node, commodity_id)`。
	- 值为该 commodity 对该目的节点的需求量 `required_amount_mb`。

5. epoch 集合 $K$
	- 通过 `build_epoch_index()` 生成。
	- 每个 epoch 显式记录 `epoch_index`、`start_time_ms`、`end_time_ms`。

6. epoch 时长 $\tau$
	- 由 `epoch_size_ms` 提供。

7. 传播延迟换算 $\delta_{ij}$
	- 当前按 `ceil((latency_us / 1000) / epoch_size_ms)` 映射为 `delay_epochs`。
	- 与旧 TE-CCL 中 `max(1, delay)` 的近似不同，阶段 1 已按建模说明保留 0 epoch 延迟的可能性。

8. 初始 buffer
	- 当前定义为：每个 commodity 在其 source node 的 `ready_epoch_index` 时刻拥有 `size_mb` 的初始持有量。
	- 已映射为 `TECCLInitialBufferEntry` 与 `initial_buffer_matrix[(commodity_id, node_id, epoch_index)]`。

#### 阶段 1 产出 C：后续阶段可直接复用的输入对象

`TECCLModelInput` 当前已经统一承载以下后续 builder 所需输入：

1. `index_bundle`
2. `demand_entries`
3. `demand_matrix`
4. `initial_buffer_entries`
5. `initial_buffer_matrix`
6. `capacity_by_edge_and_epoch`
7. `delay_epochs_by_edge`
8. `commodity_by_id`
9. `edge_by_id`
10. `summary`

这意味着阶段 2 构建 HiGHS MILP 时，不再需要直接从 workload/topology 原始对象回读，而是统一从 `TECCLModelInput` 进入。

#### 阶段 1 已验证结果

已使用 `configs/experiment/inter_dc_dual_parallel_teccl.yaml` 对索引层做最小验证，结果如下：

1. `topology_name = inter_dc_dual_fabric_topology`
2. `planning_horizon_epochs = 300`
3. `node_count = 58`
4. `directed_edge_count = 178`
5. `commodity_count = 32`
6. `destination_pair_count = 32`
7. `total_demand_mb = 768.0`
8. 首个 commodity 标识为 `inter_dc_unicast_0_chunk_0::gpu_0`
9. 首个需求矩阵键为 `('gpu_0', 'gpu_8', 'inter_dc_unicast_0_chunk_0::gpu_0')`
10. 首个初始 buffer 键为 `('inter_dc_unicast_0_chunk_0::gpu_0', 'gpu_0', 0)`

#### 阶段 1 结论

阶段 1 已完成，并得到以下明确结论：

1. 数学模型所需的 commodity、epoch、directed edge、需求矩阵、初始 buffer 映射已经独立于旧 TE-CCL 状态机落地。
2. 后续阶段 2 可以直接围绕 `TECCLModelInput` 建立变量、约束和目标函数，无需继续依赖旧 `TECCLChunkReplicaState` 或候选动作枚举逻辑。
3. 生成式与显式拓扑都可以通过同一索引层接入，其中 bidirectional 物理链路已经在阶段 1 正式展开为 MILP 所需的有向边集合。

### 阶段 2：HiGHS MILP builder 落地

目标：完成变量、约束和目标函数的正式建模。

工作内容：

1. 创建 F、B、R 变量。
2. 实现容量约束。
3. 实现 GPU 流守恒约束。
4. 实现交换机流守恒约束。
5. 实现缓冲区更新约束。
6. 实现目的地约束。
7. 实现目标函数。

验收标准：

1. 小规模样例能成功 build 模型。
2. HiGHS 能正常 optimize。
3. 可导出变量数与约束数。

#### 阶段状态

状态：已完成

执行时间：2026-03-10

#### 阶段 2 产出 A：新增代码模块

本阶段已新增以下模块：

1. `simulator/schedulers/teccl_milp_builder.py`
	- 定义 `TECCLMILPBuildConfig`、`TECCLVariableBundle`、`TECCLMILPBuildResult`。
	- 实现 `build_teccl_milp_model()`。
	- 已正式创建 `F`、`B`、`R` 三类变量。
	- 已正式实现容量约束、GPU 可复制约束线性化、relay 非复制约束、交换机守恒约束、buffer 更新约束、目的地约束与加权提前完成目标函数。

2. `simulator/schedulers/teccl_highs_backend.py`
	- 定义 `TECCLHighsSolveConfig`、`TECCLHighsSolveResult`。
	- 实现 `solve_teccl_milp()`。
	- 已支持 `time_limit`、`mip_gap`、`threads`、求解状态、目标值、最优界、gap、求解耗时和模型规模读取。

3. `simulator/schedulers/__init__.py`
	- 已导出阶段 2 的 builder/backend 公共入口，便于后续调度器主路径、阶段 3 统计层和阶段 4 解码层直接复用。

#### 阶段 2 产出 B：变量与约束落地结论

本阶段已明确以下 builder 落地结论。

1. 变量索引已经稳定落地
	- `F` 变量键定义为 `(commodity_id, edge_id, epoch_index)`。
	- `B` 变量键定义为 `(commodity_id, node_id, epoch_index)`。
	- `R` 变量键定义为 `(source_node, destination_node, commodity_id, epoch_index)`。
	- 三类变量都能从 `TECCLModelInput` 反查回原始 commodity、edge、destination 语义。

2. GPU 可复制约束已做显式线性化
	- 说明中的 `max` 形式，当前在线性模型中改写为“对每条 GPU 出边分别约束”：
	- `F[commodity, edge, k] <= B[commodity, src_node, k]`。
	- 该改写保留了“GPU 只有在已持有 chunk 时才能发送”和“同一 chunk 可同时发往多条出边”这两个核心语义。

3. relay 节点被单独建模为非复制缓冲节点
	- 阶段 1 中保留下来的 `relay_nodes`，在阶段 2 未被直接并入 GPU 或 switch。
	- 当前采用 `sum(outgoing) <= B[node, k]` 的约束形式，避免 host 等 relay 节点意外获得 GPU 式复制语义。

4. 交换机约束已经按“零缓冲 + 延迟到达守恒”落地
	- 交换机节点的 `B` 变量被固定为 0。
	- 交换机守恒不再依赖旧状态机，而是直接基于 builder 中的延迟到达表达式，将“在 epoch `k` 到达交换机的流量”等于“epoch `k` 从交换机发出的流量”。

5. buffer 更新已从运行时回写改为模型内显式约束
	- 对非交换机节点，当前采用：
	- `B[k] = B[k-1] + initial_buffer_at_k + arrivals_at_k`。
	- 其中 `arrivals_at_k` 统一由 `send_epoch = k - delay_epochs - 1` 反推得到。
	- 这使后续阶段不再需要依赖 runtime flow 完成事件去反推 TE-CCL buffer。

6. 目的地约束已完成线性化
	- 当前 `R` 变量采用累计接收量语义。
	- 已实现 `R <= D`、`R <= B_dest`、`R[k] >= R[k-1]` 和末 epoch `R[last] = D`。
	- 目标函数已按 `sum_k 1/(k+1) * R[k]` 形式直接作用于 `R` 变量。

#### 阶段 2 产出 C：builder 可直接导出的模型规模摘要

`TECCLMILPBuildResult.summary` 当前已经统一导出以下字段：

1. `variable_count`
2. `constraint_count`
3. `non_zero_count`
4. `flow_variable_count`
5. `buffer_variable_count`
6. `receive_variable_count`
7. `integer_variable_count`
8. `continuous_variable_count`
9. `capacity_constraint_count`
10. `gpu_availability_constraint_count`
11. `relay_availability_constraint_count`
12. `switch_flow_conservation_constraint_count`
13. `buffer_update_constraint_count`
14. `receive_constraint_count`

这意味着阶段 3 不需要重新统计 builder 内部规模，只需要把这些字段接入 solver stats 与最终导出路径。

#### 阶段 2 已验证结果

已使用 `configs/topology/inter_dc_dual_fabric_topology.yaml` 加一个最小单播作业做 build/solve 验证，结果如下：

1. 验证作业：`stage2_minimal_unicast`
2. participants：`gpu_0 -> gpu_8`
3. chunk_count：`1`
4. total_data_mb：`24.0`
5. epoch_size_ms：`1.0`
6. planning_horizon_epochs：`40`
7. `variable_count = 9480`
8. `constraint_count = 11880`
9. `non_zero_count = 24779`
10. `flow_variable_count = 7120`
11. `buffer_variable_count = 2320`
12. `receive_variable_count = 40`
13. HiGHS `model_status = Optimal`
14. `objective_value = 13.583775798823641`
15. `best_bound = 13.583775798823641`
16. `mip_gap = 0.0`
17. `solve_time_ms = 67.44482799967955`

#### 阶段 2 结论

阶段 2 已完成，并得到以下明确结论：

1. 阶段 1 的 `TECCLModelInput` 已足以直接驱动正式 HiGHS builder，不需要再回退到旧 TE-CCL 的候选动作枚举。
2. 三类核心变量、六类核心约束和加权目标函数已经在代码中形成独立 builder，可单独 build、optimize 并返回规模摘要。
3. 后续阶段 3 可以直接围绕 `TECCLMILPBuildResult` 和 `TECCLHighsSolveResult` 接入求解统计，无需重新设计模型内部结构。

### 阶段 3：求解统计与负载统计

目标：补齐你要求的“求解时间和对应负载记录”。

工作内容：

1. 记录 model build time。
2. 记录 solver optimize time。
3. 记录 wall time。
4. 记录 job、chunk、commodity、epoch、edge、demand 等负载规模。
5. 记录 solver status、objective、gap、best bound。

验收标准：

1. 单次求解后能生成完整的 solver stats。
2. summary.json 和独立 stats json 中都能看到核心字段。

#### 阶段状态

状态：已完成

执行时间：2026-03-10

#### 阶段 3 产出 A：新增统计与导出模块

本阶段已新增以下模块：

1. `simulator/schedulers/teccl_metrics.py`
	- 定义 `TECCLSolverStats`。
	- 实现 `build_teccl_solver_stats()`。
	- 实现 `export_teccl_solver_artifacts()`。
	- 已覆盖求解时间、负载规模、模型规模、求解状态和独立 JSON 导出。

2. `simulator/experiment/teccl_planning.py`
	- 定义 `TECCLPlanningRunResult`。
	- 实现 `run_teccl_planning_export()`。
	- 已支持从 experiment 配置直接完成 topology/workload 读取、planning horizon 推导、MILP build、HiGHS solve 和 stats 导出。

3. `scripts/export_teccl_solver_stats.py`
	- 提供单次 TE-CCL 求解统计导出入口。
	- 可直接输出 `summary.json`、`scheduler_debug.json` 和 `teccl_solver_stats.json`。

#### 阶段 3 产出 B：已落地的统计字段

本阶段已正式落地以下统计字段。

1. 求解时间字段
	- `teccl_model_build_time_ms`
	- `teccl_solve_only_time_ms`
	- `teccl_solver_wall_time_ms`

2. 负载规模字段
	- `job_count`
	- `demand_count`
	- `chunk_count`
	- `commodity_count`
	- `source_gpu_count`
	- `destination_pair_count`
	- `epoch_count`
	- `node_count`
	- `edge_count`
	- `total_demand_mb`
	- `average_chunk_mb`
	- `max_chunk_mb`
	- `inter_dc_edge_count`
	- `planning_horizon_epochs`
	- `planning_horizon_ms`

3. 模型规模字段
	- `variable_count`
	- `binary_variable_count`
	- `integer_variable_count`
	- `continuous_variable_count`
	- `constraint_count`
	- `non_zero_count`

4. 求解结果字段
	- `solver_status`
	- `objective_value`
	- `best_bound`
	- `mip_gap`
	- `node_explored_count`

#### 阶段 3 产出 C：导出链路落地结论

本阶段已明确以下导出链路结论。

1. 独立导出入口已经具备
	- `export_teccl_solver_artifacts()` 当前会直接写出：
	- `summary.json`
	- `scheduler_debug.json`
	- `teccl_solver_stats.json`

2. summary.json 已包含核心 solver 字段
	- `summary.json.aggregate_metrics` 和首个 repetition 都会包含阶段 3 的核心数值字段。
	- 因此后续外部分析脚本不必额外读取内部对象，即可看见 build time、solve time、模型规模和求解状态。

3. 通用 exporter 已具备兼容能力
	- `simulator/metrics/exporters.py` 当前已经支持从 `scheduler_debug_state.teccl_solver_stats` 读取统计并补入 summary。
	- 当阶段 4/6 接回统一运行入口后，可继续复用该导出逻辑生成 `teccl_solver_stats.json`。

#### 阶段 3 已验证结果

已使用双 DC 显式拓扑和最小单播作业完成单次导出验证，结果如下：

1. 验证输出目录：`results/stage3_minimal_solver_stats`
2. 已成功写出：
	- `summary.json`
	- `scheduler_debug.json`
	- `teccl_solver_stats.json`
3. 验证作业：`stage3_minimal_unicast`
4. participants：`gpu_0 -> gpu_8`
5. `teccl_model_build_time_ms = 391.2387230002423`
6. `teccl_solve_only_time_ms = 77.03954799944768`
7. `teccl_solver_wall_time_ms = 468.3666720002293`
8. `job_count = 1`
9. `chunk_count = 1`
10. `commodity_count = 1`
11. `destination_pair_count = 1`
12. `inter_dc_edge_count = 2`
13. `variable_count = 9480`
14. `constraint_count = 11880`
15. `non_zero_count = 24779`
16. `solver_status = Optimal`
17. `objective_value = 13.583775798823641`
18. `best_bound = 13.583775798823641`
19. `mip_gap = 0.0`
20. `node_explored_count = 1`

#### 阶段 3 结论

阶段 3 已完成，并得到以下明确结论：

1. TE-CCL 的 build time、solve time、wall time、负载规模和模型规模已经可以在单次求解后独立导出。
2. `summary.json` 与 `teccl_solver_stats.json` 两条导出链路已经同时具备核心字段，满足阶段验收要求。
3. 后续阶段 4 可以直接围绕 `TECCLPlanningRunResult`、`TECCLSolverStats` 和现有 exporter 兼容层继续接 runtime，不需要回头补统计框架。

### 阶段 4：求解结果解码与 runtime 对接

目标：让 MILP 解能被当前仿真底座执行。

工作内容：

1. 编写 solution decoder。
2. 生成标准化 epoch plan。
3. 接到 runtime engine 中执行。
4. 确保执行阶段不改变求解结果。

验收标准：

1. 最小案例可从“求解”走到“执行完成”。
2. 通信完成结果与求解输出一致。

#### 阶段状态

状态：已完成

执行时间：2026-03-10

#### 阶段 4 产出 A：新增解码层与 runtime adapter

本阶段已新增以下模块：

1. `simulator/schedulers/teccl_solution_decoder.py`
	- 定义 `TECCLPlannedTransfer`、`TECCLExecutionPlan`。
	- 实现 `decode_teccl_solution()`。
	- 已将 HiGHS 求得的正流量变量解码为按 epoch 分组的一跳传输计划，并保留 `job_id`、`commodity_id`、`chunk_id`、`flow_id`、`transfer_amount_mb`、`expected_arrival_epoch` 等回溯信息。

2. `simulator/schedulers/teccl_runtime_adapter.py`
	- 实现 `build_teccl_plan_decision()`。
	- 已把 `TECCLExecutionPlan` 适配为运行时可直接消费的 `ScheduleDecision + epoch_actions`。
	- 该适配层不再引入任何二次局部路由逻辑，只下发已求出的计划动作。

#### 阶段 4 产出 B：调度器主路径改造结论

本阶段已完成以下调度器主路径改造。

1. `simulator/schedulers/teccl.py` 已新增 HiGHS 规划模式
	- 当 `solver_backend = highs` 时，`TECCLScheduler` 不再走旧的 `solve_epoch()` 候选动作路径。
	- 当前会在首次调度时一次性完成：
	- `TECCLModelInput` 构建
	- HiGHS MILP build/solve
	- 解码成 `TECCLExecutionPlan`
	- 缓存 solver stats 和 plan summary

2. 调度输出已改为“按 epoch 回放完整计划”
	- scheduler 当前缓存完整 `planned_execution`。
	- 每个 epoch 只下发该 epoch 对应的一跳发送计划。
	- 已发送过的 epoch 不会重复下发，避免运行时重复物化同一计划。

3. solver stats 已贯穿到 runtime/exporter
	- `scheduler.export_debug_state()` 当前已包含：
	- `teccl_solver_stats`
	- `teccl_plan_summary`
	- `planner_model_summary`
	- 这使阶段 3 的统计框架能够直接复用于阶段 4 的标准运行链路。

#### 阶段 4 产出 C：运行时对接改造结论

本阶段已完成以下 runtime 对接改造。

1. `simulator/core/engine.py` 已支持 planned MILP epoch_action 物化
	- `_materialize_epoch_action()` 当前优先读取 `action.metadata.flow_id`、`owner_job_id`、`transfer_amount_mb`。
	- 因此运行时已不再假设每个 TE-CCL hop 必须发送完整 chunk，也不再强依赖旧 replica-state flow id 规则。

2. TE-CCL 作业完成判定已从旧 job_states 切到 planned flow 集合
	- `_update_completed_jobs_from_decision()` 当前在 `execution_mode = planned_milp` 时，会把 `flow_ids_by_job` 写入 runtime metadata。
	- `_mark_completed_jobs()` 当前会依据“某 job 的所有 planned flow 都已完成”来标记 TE-CCL 作业完成。

3. 执行阶段不再改写求解结果
	- runtime 当前只负责把解码后的 epoch_action 物化成 flow 并按链路带宽/时延推进。
	- 不再对 TE-CCL 计划做额外 hop 选择、目的地改写或局部重路由。

#### 阶段 4 已验证结果

本阶段已完成两层验证。

1. 最小端到端执行验证
	- 使用 `inter_dc_dual_fabric_topology` 和单作业 `stage4_minimal_unicast`。
	- participants：`gpu_0 -> gpu_8`
	- scheduler backend：`highs`
	- `planning_horizon_epochs = 40`
	- 运行结果：
	- `completed_job_ids = ['stage4_minimal_unicast']`
	- `completed_flow_count = 50`
	- `plan_flow_counts = {'stage4_minimal_unicast': 50}`
	- `solver_status = Optimal`
	- `planned_transfer_count = 50`
	- `emitted_epoch_count = 22`
	- `schedule_history_len = 22`
	- 最终运行时间：`now_ms = 21.96`

2. 标准 exporter 链路验证
	- 已生成标准结果目录：`results/stage4_minimal_highs_export`
	- 已成功写出：
	- `summary.json`
	- `scheduler_debug.json`
	- `teccl_solver_stats.json`
	- `schedule_history.json`
	- `flow_trace.csv`
	- 说明 highs 规划模式已经能够进入现有统一结果导出体系。

#### 阶段 4 结论

阶段 4 已完成，并得到以下明确结论：

1. HiGHS MILP 解已经可以被正式解码为可执行 epoch 计划，并由当前 runtime 执行到完成。
2. 运行时对接已经从旧 TE-CCL 状态机完成判定切换为 planned flow 集合驱动，能够支持 planner 模式下的作业完成判断。
3. 后续阶段 5 可以直接围绕“容量约束、复制语义、buffer 更新、最终需求满足”的小规模手工案例做正确性验证，而不需要再补执行适配层。

### 阶段 5：正确性验证

目标：验证新实现确实符合建模说明。

工作内容：

1. 小拓扑人工校验容量约束。
2. 校验 GPU 复制与交换机非复制语义。
3. 校验最终需求满足。
4. 校验 buffer 更新和延迟传播。

验收标准：

1. 至少 3 个小规模手工案例通过。
2. 发现无解时能输出可解释信息。

### 阶段 6：实验回归与性能分析

目标：把新实现接回现有实验入口，并分析求解耗时。

工作内容：

1. 跑 minimal 案例。
2. 跑 inter-DC mild 案例。
3. 跑一组较重负载案例。
4. 对比 solver time 与 communication time。
5. 分析求解慢时对应的负载规模和模型规模。

验收标准：

1. 至少一组完整实验产出可用 summary、trace、solver stats。
2. 能清楚回答“慢在求解还是慢在通信”。

### 阶段 7：文档与配置同步

目标：让文档、配置说明、实现完全一致。

工作内容：

1. 更新 README。
2. 更新 explan.md。
3. 更新 TE-CCL 配置说明。
4. 增加 HiGHS 使用说明和依赖说明。

验收标准：

1. 文档中不再描述旧 exact_milp_solver 的工程近似语义。
2. 文档能正确解释求解时间与执行时间的差别。

## 12. 开发跟踪规则

后续开发时，必须按阶段推进，并遵守以下跟踪规则：

1. 每完成一个阶段，就更新该阶段的状态。
2. 每个阶段结束时，至少补一条“已验证结果”。
3. 如果某阶段发现模型定义需要回退，必须在本文件追加“设计变更记录”。
4. 未完成当前阶段前，不跳到后面的收尾文档阶段。

建议后续在本文件末尾追加如下状态块：

1. 阶段编号
2. 当前状态
3. 已完成内容
4. 待解决问题
5. 下一步动作

## 13. 风险与处理策略

### 13.1 模型规模风险

时间展开多商品流 MILP 的规模可能很大，特别是在：

1. chunk 很多
2. epoch 很多
3. 拓扑边很多
4. 目的地很多

处理策略：

1. 先支持显式 planning horizon。
2. 支持 time limit 和 gap。
3. 先做最小案例验证，再逐步扩大规模。

### 13.2 GPU 复制约束线性化风险

说明中的 GPU 流守恒采用 max 形式，不可直接原样进入 MILP。

处理策略：

1. 先给出严格等价的线性化方案。
2. 在线性化落地前，不进入后续执行对接阶段。

### 13.3 运行时兼容风险

当前 runtime 更偏向事件驱动执行，新的 TE-CCL 更偏向先求完整计划。

处理策略：

1. 新增解码层，而不是强行把 MILP 变量塞进旧 EpochAction 语义。
2. 必要时扩展 runtime adapter，而不是污染求解器层。

### 13.4 HiGHS 能力边界与环境风险

HiGHS 虽然免费开源，但在不可行性诊断、MIP 高级特性和超大规模 MILP 性能上，可能弱于商业求解器。

处理策略：

1. 尽早在环境中验证 highspy 可用性。
2. 默认支持导出 LP 或 MPS 模型与 solver log，便于无解时离线诊断。
3. 在依赖和文档中明确 HiGHS 版本要求与已验证版本。

## 14. 本计划的执行结论

本次 TE-CCL 重构的执行策略总结如下：

1. 彻底放弃当前主路径中的候选动作选择近似作为正式实现。
2. 按建模说明重建全时域多商品流 MILP。
3. 正式求解器统一使用 HiGHS。
4. 求解时间与通信执行时间必须分开统计。
5. 每次求解必须记录负载规模与模型规模。
6. 按阶段推进开发和验收，后续实现严格遵循本文件。

后续任何 TE-CCL 重构工作，都应先对照本文件确认当前阶段和目标，不再以旧 exact_milp_solver 的实现边界作为约束。
