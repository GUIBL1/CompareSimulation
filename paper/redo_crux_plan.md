# CRUX 全量重构计划
**所有操作均在conda activate networkSimulation虚拟环境下进行：/home/code/miniconda3/envs/networkSimulation**

## 1. 目标

本计划用于指导当前仓库中的 CRUX 实现进行一次按论文建模的彻底重构。重构后的 CRUX 不再停留在“强度排序 + 路径拥塞打分 + 均匀优先级分桶”的工程化近似实现，而是严格按照 [paper/crux.md](paper/crux.md) 中给出的建模说明，重建真正的 GPU-intensity-aware path selection、priority assignment 和 priority compression 主路径。

本次重构的总目标是：

1. 严格按 CRUX 建模说明实现 GPU intensity、优先级分配、争用 DAG 和优先级压缩。
2. 在运行时真正落实“高优先级流优先发送、低优先级流在拥塞时让出带宽”的执行语义，而不是只导出优先级数字。
3. 将路径选择、优先级分配、优先级压缩和执行阶段拆开统计，避免把“路径选得不好”和“优先级执行不生效”混在一起。
4. 记录优先级压缩前后的作业规模、争用图规模和压缩损失，便于分析 CRUX 的行为边界。
5. 将重构过程拆成明确阶段，便于开发时跟踪进度、定位问题和做阶段验收。

本文件是后续 CRUX 重构工作的唯一基准计划。后续实现、验收与文档更新，均以本文件为准。

## 2. 当前实现为什么必须重构

当前仓库中的 CRUX 主路径实现，本质上是一个工程化的 job/path 启发式调度器：

1. 用 `compute_phase_ms / observed_comm_time_ms` 近似 intensity score。
2. 按该 score 对作业排序。
3. 在候选路径中选择当前 `utilization + contention` 较低的路径。
4. 把排序结果按等宽 bucket 压缩到有限优先级等级。
5. 把优先级写入 flow，但运行时仍按纯 max-min fair 平均分配带宽。

这套实现存在以下根本偏差：

1. 没有显式区分论文中的 $W_j$、$t_j$、$I_j$、$k_j$ 和 $P_j$。
2. 没有实现 DLT 特性感知修正因子 $k_j$。
3. 没有构建通信争用 DAG。
4. 没有把优先级压缩建模为 DAG 上的 Max-K-Cut 近似问题。
5. 没有实现多拓扑序采样与动态规划压缩。
6. 没有在运行时真正消费优先级进行带宽调度。
7. 当前 priority compression 只是按 rank_index 均匀分桶，不是论文中的损失最小化压缩。

因此，这次重构不是继续补几个参数，而是替换 CRUX 的优先级建模核心、压缩核心和执行核心。

## 3. 重构后的目标实现形态

重构完成后，CRUX 应具备以下结构：

1. 从 unified workload 中抽取每个作业的一轮计算工作量 $W_j$ 和通信需求。
2. 从 topology 中抽取每个 flow 的可用路径集合 $P_j$。
3. 根据当前路径负载估计每个作业的最大通信传输时间 $t_j$。
4. 计算 GPU intensity：

$$
I_j = \frac{W_j}{t_j}
$$

5. 根据 DLT 特征计算修正因子 $k_j$，得到最终优先级：

$$
P_j = k_j I_j
$$

6. 基于共享链路关系构建通信争用 DAG：

$$
G=(V,E)
$$

7. 在给定硬件优先级队列数 $K$ 下，对 DAG 做多拓扑序采样和动态规划压缩，得到硬件优先级映射。
8. 在运行时把该映射真正落实到带宽调度，使高优先级流在拥塞时获得优先服务。
9. 在 metrics 中分开导出：
	- intensity 计算统计
	- path selection 统计
	- priority assignment 统计
	- priority compression 统计
	- execution 阶段通信时间与 GPU 利用率代理指标

## 4. 建模原则

### 4.1 忠实于说明，不做语义偷换

以下内容必须严格保留：

1. GPU intensity 的核心形式必须保持为 $I_j = W_j / t_j$，不能再退化成无说明依据的任意打分函数。
2. 路径选择必须体现“高 GPU intensity 作业优先获得低拥塞路径”的顺序性。
3. 优先级必须来自 $P_j = k_j I_j$，而不是只按 raw intensity 直接排序。
4. 优先级压缩必须围绕争用 DAG 和 cut loss 建模，而不是简单 rank bucket。
5. 运行时必须消费压缩后的硬件优先级，而不是把优先级只作为调试字段。

### 4.2 先建模，再执行

重构后的 CRUX 主路径应遵循：

1. 先计算作业 intensity 和修正优先级。
2. 再做路径选择。
3. 再构建争用 DAG 与硬件优先级压缩。
4. 再把结果交给运行时执行。

不能再让运行时的“纯平均带宽分配”反过来破坏 CRUX 的优先级语义。

### 4.3 优先级必须在执行阶段真正生效

CRUX 不是纯离线分析器，而是通信调度器。因此必须显式区分：

1. Priority Construction Time
	表示 intensity 计算、路径选择、争用 DAG 构建与优先级压缩的耗时。
2. Communication Execution Time
	表示运行时按 CRUX 优先级执行流传输所经历的通信时间。

最终报告中不能只给 completion_time_ms，而必须能回答：

1. CRUX 算优先级和路径花了多久。
2. 按优先级执行通信花了多久。
3. 优先级执行是否真的比纯公平带宽更改善高 intensity 作业的完成行为。

## 5. 算法与运行时选型

### 5.1 正式优先级压缩算法

本次重构统一采用“争用 DAG + 多拓扑序采样 + 动态规划连续分割”作为 CRUX 的正式优先级压缩算法。

原因如下：

1. 这是 [paper/crux.md](paper/crux.md) 中给出的核心设计，不是可选装饰模块。
2. 当前等宽 bucket 压缩无法表达“压缩损失最小化”的目标。
3. DAG 上的多序列近似求解可以在现有仿真规模下落地，不需要引入外部复杂求解器。

### 5.2 运行时执行要求

重构时必须同时落实以下能力：

1. 运行时支持按 flow.priority 区分服务顺序。
2. 链路带宽分配逻辑必须能区分至少 $K$ 个优先级等级。
3. 当多个优先级共享链路时，必须先分高优先级，再向低优先级释放剩余带宽。
4. 导出层必须能区分“优先级构建时间”和“执行时间”。
5. 可在调试状态中导出每个 job 的 $I_j$、$k_j$、$P_j$、压缩前等级和压缩后硬件优先级。

## 6. 新实现的模块设计

### 6.1 模块划分

重构后建议 CRUX 至少拆成以下模块：

1. crux_model_input.py
	负责把 unified workload 和 topology 转成 CRUX 输入对象。
2. crux_intensity.py
	负责计算 $W_j$、$t_j$、$I_j$。
3. crux_paths.py
	负责按 intensity 顺序做 GPU-aware path selection。
4. crux_priority_assignment.py
	负责计算修正因子 $k_j$ 和最终优先级 $P_j$。
5. crux_contention_dag.py
	负责构建争用 DAG、边权与共享链路关系。
6. crux_priority_compression.py
	负责多拓扑序采样、动态规划连续分割和硬件优先级映射。
7. crux_runtime_adapter.py
	负责把压缩后的优先级映射到运行时可执行结构。
8. crux_metrics.py
	负责记录路径、优先级和压缩阶段的独立指标。

### 6.2 主调度器职责

CruxScheduler 的职责要收敛成：

1. 接收统一 workload。
2. 构造 CRUX 输入模型。
3. 计算 GPU intensity 与修正优先级。
4. 完成路径选择。
5. 构建争用 DAG 并做优先级压缩。
6. 把硬件优先级和路径分发给 runtime。
7. 导出调试状态、压缩统计和执行统计。

它不再负责：

1. 只按 `compute_phase_ms / observed_comm_time_ms` 做单一强度排序。
2. 用等宽分桶模拟优先级压缩。
3. 把优先级只写进 metadata 而不影响实际链路调度。

## 7. 数学模型与算法落地计划

### 7.1 输入对象映射

这一阶段要把说明中的数学量映射到代码对象：

1. 作业集合 $J$
2. 计算工作量 $W_j$
3. 可用路径集合 $P_j$
4. 最大通信传输时间 $t_j$
5. GPU intensity $I_j$
6. 修正因子 $k_j$
7. 最终优先级 $P_j = k_j I_j$
8. 硬件优先级数量 $K$
9. 争用 DAG $G=(V,E)$
10. DAG 边权 $w_{u,v}$

要求：

1. 所有对象必须从统一 workload、topology 和运行时链路负载中推导，不能硬编码。
2. 所有数学对象都必须能稳定映射回原始 job、flow、path 和链路。

### 7.2 GPU intensity 设计

本次重构将正式引入以下量：

1. $W_j$
	表示作业 $j$ 单轮训练的计算工作量。
2. $t_j$
	表示作业 $j$ 当前路径选择下的最大通信传输时间。
3. $I_j$
	表示作业 $j$ 的 GPU intensity。

要求：

1. $W_j$ 的取值来源必须在代码中明确定义并文档化。
2. $t_j$ 必须与通信路径和链路负载相关，不能只用历史总完成时间代替。
3. intensity 计算必须可导出并可复核。

### 7.3 路径选择设计

路径选择必须按 [paper/crux.md](paper/crux.md) 中的顺序语义实现：

1. 先按 $I_j$ 降序排序作业。
2. 再对每个作业在可用路径集合 $P_j$ 中选择当前最不拥塞路径：

$$
p^* = \arg\min_{p \in P_j} L(p)
$$

要求：

1. 路径负载 $L(p)$ 的定义必须清晰落地。
2. 高 intensity 作业的路径保留效果必须能被导出验证。
3. 路径选择结果必须能回溯到具体链路负载变化。

### 7.4 优先级分配设计

必须显式实现：

$$
P_j = k_j I_j
$$

实现要点：

1. 必须给出 $k_j$ 的代码定义与输入来源。
2. 必须能导出每个 job 的 $I_j$、$k_j$ 和 $P_j$。
3. 若当前 [paper/crux.md](paper/crux.md) 中对 $k_j$ 细节仍不足以唯一决定实现方式，必须在计划执行时补“设计变更记录”，说明采用的 faithful approximation。

### 7.5 通信争用 DAG 设计

必须显式构建：

$$
G=(V,E)
$$

其中：

1. $V=J$。
2. 若两个作业共享同一链路且 $P_{j_1} > P_{j_2}$，则构建边 $j_1 \rightarrow j_2$。
3. 边权为：

$$
w_{j_1,j_2} = I_{j_1}
$$

要求：

1. DAG 构建必须可导出节点数、边数和边权统计。
2. 共享链路关系必须可回溯到具体 path overlap。
3. 压缩前的冲突结构必须可视化或可调试导出。

### 7.6 优先级压缩设计

在给定硬件优先级数 $K$ 下，必须将优先级压缩问题落地为：

$$
\max \sum_{(u,v)\in E_{cut}} w_{u,v}
$$

具体执行路径为：

1. 生成多个 DAG 拓扑序。
2. 对每个拓扑序将问题转为连续分段问题。
3. 用动态规划计算：

$$
f(i,k) = \max_{j < i} \left( f(j,k-1) + C_{j,i} \right)
$$

4. 选取 cut 权重最大的压缩结果。

要求：

1. 必须导出压缩前优先级排序和压缩后硬件优先级。
2. 必须记录 cut 权重、未切断损失和使用的拓扑序样本数。
3. 不允许继续使用单纯 rank bucket 作为正式压缩逻辑。

### 7.7 执行层设计

最终通信调度必须通过以下方式执行：

1. 使用路径选择结果确定 flow path。
2. 使用压缩后的硬件优先级设置 flow.priority。
3. 链路带宽分配按 priority 层级而不是纯平均分配。
4. 导出高优先级和低优先级流的实际完成差异。

## 8. 运行时与导出对接计划

### 8.1 运行时对接

当前 runtime engine 仍可复用，但带宽共享逻辑必须调整为：

1. 先按链路上的 flow.priority 分组。
2. 高优先级组先分配带宽。
3. 低优先级组只消费剩余带宽。
4. 同一优先级组内部可继续使用 max-min fair。

### 8.2 执行正确性要求

执行层必须满足：

1. 高优先级 flow 在拥塞链路上不会与低优先级 flow 做无差别平分。
2. CRUX 的 path selection 和 priority compression 结果在 runtime 中不被二次改写。
3. 导出层可以对比“priority-aware execution”和“纯平均执行”的差异。

## 9. 指标与统计设计

这是本次重构必须新增的重点内容。

### 9.1 分离路径/优先级构建时间与通信执行时间

CRUX 结果中必须新增以下指标：

1. crux_path_selection_time_ms
	路径选择耗时。
2. crux_priority_assignment_time_ms
	priority assignment 耗时。
3. crux_priority_compression_time_ms
	DAG 构建、拓扑序采样和动态规划压缩耗时。
4. crux_scheduler_wall_time_ms
	整个 CRUX 调度构建阶段总耗时。
5. crux_communication_execution_time_ms
	按 CRUX 优先级执行通信的耗时。
6. crux_end_to_end_time_ms
	调度构建时间与执行时间之和。

### 9.2 记录 CRUX 负载与图规模信息

每次 CRUX 调度时，必须同时记录：

1. job_count
2. flow_count
3. path_candidate_count
4. unique_link_count
5. overlapping_link_pair_count
6. priority_level_count_raw
7. hardware_priority_count
8. contention_dag_node_count
9. contention_dag_edge_count
10. topological_order_sample_count

### 9.3 记录压缩与执行结果

每次调度还必须导出：

1. average_intensity
2. max_intensity
3. average_priority_score
4. max_priority_score
5. total_cut_weight
6. lost_cut_weight
7. average_high_priority_flow_completion_time_ms
8. average_low_priority_flow_completion_time_ms
9. priority_execution_gain_ratio

### 9.4 记录输出位置

上述统计建议至少进入以下位置：

1. summary.json
2. scheduler_debug.json
3. 单独新增 crux_scheduler_stats.json

其中 crux_scheduler_stats.json 应尽量完整，以便后续做 profiling 和与 TE-CCL 的对照分析。

## 10. 配置重构计划

当前 CRUX 配置项不足以支撑新模型，需新增或重定义以下字段：

1. hardware_priority_count
2. topology_order_sample_count
3. enable_priority_compression
4. enable_priority_aware_bandwidth
5. intensity_definition_mode
6. priority_factor_mode
7. path_load_metric
8. export_crux_stats
9. export_contention_dag_debug

对旧配置字段的处理原则：

1. 旧字段若与新模型冲突，则明确废弃。
2. 旧字段若还能表达同一语义，则做兼容映射。
3. 不允许保留会误导用户的旧字段名或旧行为。

## 11. 分阶段执行计划

以下阶段必须按顺序推进，每一阶段都要完成后再进入下一阶段。

### 阶段 0：基线冻结与接口勘察

当前状态：已完成（2026-03-10）

落地产物：

1. 当前 CRUX 旧实现边界已在 `simulator/schedulers/crux.py` 中通过 stage0_baseline 调试导出固化。
2. 复用接口已明确限定为 runtime 的 RuntimeState / ScheduleDecision 契约，以及 exporter 的 summary.json / scheduler_debug.json 契约。
3. 新旧影响面已明确：阶段 0/1 只新增输入建模层，不改变 runtime 的 max-min fair 语义。

目标：冻结当前 CRUX 代码路径，明确哪些代码保留、哪些转入 legacy。

工作内容：

1. 标记当前 crux.py 的旧实现边界。
2. 识别必须复用的 runtime / exporter / metrics 接口。
3. 梳理现有 experiment 配置与结果导出契约。

验收标准：

1. 输出一份旧路径保留清单。
2. 输出新旧接口影响面清单。

### 阶段 1：数学对象与输入映射

当前状态：已完成（2026-03-10）

落地产物：

1. 新增 `simulator/schedulers/crux_model_input.py`，统一承载 job / flow / path / intensity / priority 输入对象。
2. 当前代码已能从 unified workload、candidate_paths 和 link_states 构建 `W_j`、`t_j`、`I_j`、`k_j`、`P_j` 的阶段 1 映射，并通过 scheduler_debug 导出完整对象。
3. 阶段 1 采用的 faithful approximation 已固定：`W_j := UnifiedJob.compute_phase_ms`，默认 `k_j := 1.0`，默认 `t_j` 仍走 legacy observed communication proxy，同时保留 path-estimated 切换入口。

目标：完成 $W_j$、$t_j$、$I_j$、$k_j$、$P_j$、路径集合和硬件优先级数的确定映射。

工作内容：

1. 定义 CRUX 输入对象。
2. 定义 intensity 计算对象。
3. 定义 path candidate 与 path load 对象。
4. 定义 priority score 对象。

验收标准：

1. 小规模样例能打印完整 intensity/priority 输入对象。
2. 输入对象之间能双向回溯。

### 阶段 2：路径选择与优先级分配落地

当前状态：已完成（2026-03-10）

落地产物：

1. CRUX 调度主路径已改为先按 `I_j` 排序、再顺序执行路径选择，并将高 intensity 作业的路径预留效应传递到后续作业。
2. 已在代码中落地 `P_j = k_j I_j`，其中默认 `k_j` 采用 documented faithful approximation：由 participant、chunk、communication pattern、dependency 和 overlap 五类 DLT 特征组合得到。
3. 选路完成后会基于最终选中路径重新计算 `t_j`、`I_j` 和 `P_j`，并据此生成最终 priority rank 与硬件优先级分桶。
4. scheduler_debug 与 summary 已补充 final intensity、final priority score、path selection time 与 priority assignment time 等阶段 2 结果。

目标：完成 intensity-aware path selection 和 $P_j = k_j I_j$ 的正式建模。

工作内容：

1. 实现 $W_j$、$t_j$、$I_j$ 计算。
2. 实现修正因子 $k_j$。
3. 实现按 intensity 顺序的路径选择。
4. 实现最终优先级排序。

验收标准：

1. 小规模样例能输出每个 job 的 $I_j$、$k_j$、$P_j$ 和选中路径。
2. 高 intensity 作业能稳定优先获得低负载路径。

### 阶段 3：争用 DAG 与优先级压缩落地

当前状态：已完成（2026-03-10）

落地产物：

1. 新增 `simulator/schedulers/crux_priority_compression.py`，实现了 contention DAG 构建、边权计算、多拓扑序采样与连续分段 DP。
2. CRUX 调度器已改为基于已选路径构建 DAG，并用 DAG 压缩结果生成硬件优先级，不再默认使用简单 rank bucket 作为正式逻辑。
3. scheduler_debug 与 summary 已补充 DAG 节点数、边数、cut weight、lost cut weight、拓扑序样本数和最终硬件优先级映射等阶段 3 结果。

目标：完成 DAG 构建、多拓扑序采样和动态规划压缩。

工作内容：

1. 构建 contention DAG。
2. 实现边权计算。
3. 实现拓扑序采样。
4. 实现连续分段 DP。
5. 输出硬件优先级映射。

验收标准：

1. 小规模样例能导出 DAG 节点数、边数、边权和 cut weight。
2. 压缩后优先级不再等于简单 rank bucket。

### 阶段 4：priority-aware runtime 落地

当前状态：已完成（2026-03-10）

落地产物：

1. `RuntimeEngine` 已在 CRUX 模式下支持 priority-aware bandwidth allocation：高优先级组先占用链路残余带宽，低优先级组仅消费剩余容量。
2. 同一优先级组内部仍保持现有的 max-min fair 近似，不改变 TE-CCL 与非 CRUX 场景的运行契约。
3. 运行时会在 metadata 中导出 `priority_aware_bandwidth_enabled`，并可通过 `scheduler.crux.enable_priority_aware_bandwidth` 显式开关。

目标：让 CRUX 的优先级在运行时真正生效。

工作内容：

1. 改造链路带宽分配逻辑。
2. 支持按 flow.priority 分层分配带宽。
3. 保证同级内部继续 max-min fair。
4. 验证高优先级流的执行优势。

验收标准：

1. 最小拥塞案例中高优先级流明显早于低优先级流完成。
2. 不会破坏 CRUX 之外调度器的现有运行契约。

### 阶段 5：统计与导出链路接入

当前状态：已完成（2026-03-10）

落地产物：

1. 新增 `simulator/schedulers/crux_metrics.py`，统一计算 CRUX 的构建时间、执行时间、图规模、cut 统计和优先级执行效果指标。
2. `summary.json` 已补充 `crux_communication_execution_time_ms`、`crux_end_to_end_time_ms`、`crux_overlapping_link_pair_count`、`crux_priority_execution_gain_ratio` 等阶段 5 指标。
3. JSON 导出已新增独立产物 `crux_scheduler_stats.json`，用于完整保存每次 repetition 的 CRUX profiling 与压缩统计。
4. `flow_trace.csv` 已补充 `priority` 字段，便于后续验证高低优先级流的执行差异。

目标：补齐 CRUX 的构建时间、图规模、压缩统计和执行效果导出。

工作内容：

1. 记录 path selection / assignment / compression 时间。
2. 记录 DAG 与 cut 统计。
3. 记录高低优先级流执行差异。
4. 导出 crux_scheduler_stats.json。

验收标准：

1. 单次调度后能生成完整 stats。
2. summary.json 和独立 stats json 中都能看到核心字段。

### 阶段 6：正确性验证

当前状态：已完成（2026-03-10）

落地产物：

1. 已通过 3 个手工可解释小案例验证 CRUX 当前实现：高 intensity 作业优先占据低拥塞路径、contention DAG 边方向与压缩后硬件优先级满足顺序约束、priority-aware runtime 下高优先级流先于低优先级流完成。
2. 阶段 6 验证直接通过内存代码片段执行，没有保留额外验证脚本或临时文件；符合“验证通过后删除临时文件”的执行要求。
3. 已验证的关键数值包括：最小拥塞 runtime 案例下高优先级流 12.8ms 完成、低优先级流 25.6ms 完成；3 作业共享链路案例导出的 DAG 边为 `job_a -> job_b`、`job_a -> job_c`、`job_b -> job_c`。

目标：验证新实现确实符合 CRUX 建模说明。

工作内容：

1. 小拓扑人工校验高 intensity 作业路径优先。
2. 校验 contention DAG 构建正确性。
3. 校验优先级压缩不违反排序约束。
4. 校验运行时优先级执行生效。

验收标准：

1. 至少 3 个小规模手工案例通过。
2. 可导出可解释的 DAG/priority 调试信息。

### 阶段 7：实验回归与文档同步

当前状态：已完成（2026-03-10）

落地产物：

1. 已新增最小 CRUX 端到端回归入口：`configs/topology/minimal_e2e_topology.yaml`、`configs/workload/minimal_e2e_workload.yaml`、`configs/experiment/minimal_crux_e2e.yaml`。
2. 已完成三档 CRUX 回归：`results/minimal_crux_e2e`、`results/inter_dc_mild_crux`、`results/inter_dc_parallel_triple_heavy_crux`，三者均成功导出 `summary.json`、`flow_trace.csv`、`scheduler_debug.json` 与 `crux_scheduler_stats.json`。
3. 已验证 CRUX 构建时间与执行时间拆分：minimal 结果为 0.20ms 构建 + 3.84ms 通信，dual mild 为 1.06ms 构建 + 2.88ms 通信，triple heavy 为 18.37ms 构建 + 1877.33ms 通信。
4. 已同步 compare/export/visualization 口径：`simulator/metrics/visualization.py` 现会把 CRUX 也画成“规划构建时间 + 通信执行时间”的堆叠完成时间图；`results/stage7_inter_dc_mild_crux_vs_teccl/comparison/comparison_summary.json` 已验证 mild 场景下 CRUX 与 TE-CCL 两侧都按该口径展示。
5. 已同步文档：`README.md`、`explan.md`、`configs/experiment/README.md` 已更新到 paper-faithful CRUX 与 stage-5/export 口径。

目标：把新实现接回现有实验入口，并同步 README / explan / 配置说明。

工作内容：

1. 跑 minimal 案例。
2. 跑 inter-DC mild 案例。
3. 跑较重负载案例。
4. 对比 priority construction time 与 communication execution time。
5. 更新 README、explan.md 和 CRUX 配置说明。

验收标准：

1. 至少一组完整实验产出可用 summary、trace、crux stats。
2. 文档中不再把当前近似实现描述为论文级完整 CRUX。

## 12. 开发跟踪规则

后续开发时，必须按阶段推进，并遵守以下跟踪规则：

1. 每完成一个阶段，就更新该阶段的状态。
2. 每个阶段结束时，至少补一条“已验证结果”。
3. 如果某阶段发现论文说明仍不足以唯一确定实现细节，必须在本文件追加“设计变更记录”。
4. 未完成当前阶段前，不跳到后面的收尾文档阶段。

建议后续在本文件末尾追加如下状态块：

1. 阶段编号
2. 当前状态
3. 已完成内容
4. 待解决问题
5. 下一步动作

## 13. 风险与处理策略

### 13.1 $k_j$ 语义落地风险

[paper/crux.md](paper/crux.md) 当前说明了 $k_j$ 的存在，但没有在现有仓库上下文中给出足以唯一决定代码实现的完整细节。

处理策略：

1. 阶段 1 必须先明确 $k_j$ 的输入来源和 faithful approximation。
2. 若需要工程化补足，必须写入“设计变更记录”。

### 13.2 runtime 优先级执行兼容风险

当前 runtime 以纯 max-min fair 为核心，而 CRUX 要求显式优先级执行。

处理策略：

1. 通过分层分配带宽扩展 runtime，而不是在调度器里伪造执行结果。
2. 保证 CRUX 之外的调度器仍可使用现有公平执行路径。

### 13.3 DAG 压缩复杂度风险

当作业数量较多、共享链路关系稠密时，争用 DAG 和多拓扑序采样会带来额外开销。

处理策略：

1. 支持限制拓扑序采样数。
2. 优先完成小规模正确性验证，再逐步放大规模。
3. 导出 DAG 规模与压缩耗时，便于分析热点。

### 13.4 与 TE-CCL 对比口径风险

CRUX 重构后会增加 priority construction time 和 execution time 双阶段统计，若导出层不同步，容易与 TE-CCL 对比口径错位。

处理策略：

1. 阶段 5 和阶段 7 必须同步更新 compare/export/visualization 口径。
2. 对比图中明确区分 CRUX 的调度构建时间与通信执行时间。

## 14. 本计划的执行结论

本次 CRUX 重构的执行策略总结如下：

1. 彻底放弃当前主路径中的“强度排序 + 等宽分桶 + 纯公平执行”作为正式 CRUX 实现。
2. 按建模说明重建 intensity、priority、争用 DAG 和优先级压缩主路径。
3. 在运行时真正落实优先级执行语义。
4. 将调度构建时间与通信执行时间分开统计。
5. 每次调度必须记录 DAG 规模、压缩结果和执行增益。
6. 按阶段推进开发和验收，后续实现严格遵循本文件。

后续任何 CRUX 重构工作，都应先对照本文件确认当前阶段和目标，不再以当前近似版 `simulator/schedulers/crux.py` 的实现边界作为约束。
