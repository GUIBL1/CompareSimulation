# 联合仿真系统详细设计计划

## 1. 目标与范围

本计划的目标是构建一个统一的 Python 离散事件仿真系统，在同一底层平台上运行并对比两类调度方案：

- CRUX：面向深度学习训练作业的 GPU-intensity 感知路径与优先级调度。
- TE-CCL：面向集合通信的 epoch 和 chunk 级流量工程调度。

系统的比较口径固定为“同平台、同一抽象流量模型、同一组公共参数”。
这意味着底层拓扑、链路参数、任务规模、随机种子、统计口径必须一致，算法只能改变自己的决策逻辑，不能隐式修改环境条件。

本计划不直接追求论文数值级复现，而是优先完成以下三件事：

1. 搭建统一可运行底座。
2. 让 CRUX 和 TE-CCL 都能接入并执行。
3. 让实验结果具备可比性和可解释性。

## 2. 顶层架构

系统采用 6 个核心层次：

1. Topology Layer
   负责加载用户提供的拓扑文件，生成节点、链路和路径候选集。
2. Workload Layer
   负责加载用户提供的作业或集合通信输入，生成统一工作负载对象。
3. Simulation Core
   负责事件推进、链路共享、时延传播、队列状态更新。
4. Scheduler Layer
   负责挂接 CRUX 和 TE-CCL，并将其输出映射为统一调度结果。
5. Metrics Layer
   负责记录完成时间、利用率、吞吐、排队、调度开销等指标。
6. Experiment Manager
   负责读取实验配置文件、批量运行、结果汇总和导出。

统一数据流如下：

```text
topology file + workload file + experiment file
                    ↓
             topology/workload loader
                    ↓
              unified runtime state
                    ↓
                selected scheduler
                    ↓
              event-driven execution
                    ↓
               metrics and reports
```

## 3. 文件化输入接口要求

拓扑相关信息必须全部由用户通过文件输入，不能写死在代码中。至少要把下面这些字段做成外部配置：

1. 主机节点数量。
2. 交换机节点数量。
3. 网络结构类型。
4. 节点编号与类型映射。
5. 链路连接关系。
6. 每条链路的带宽。
7. 每条链路的传播时延。
8. 每条链路的单双向属性。
9. 是否允许 ECMP 或多路径候选。
10. 是否存在 oversubscription，以及比例如何定义。

为保证可扩展性，计划采用“配置文件 + 加载器 + 内部统一对象”三段式结构：

1. 用户编辑 YAML 文件。
2. Loader 对 YAML 做校验和标准化。
3. 内部系统转换为 Node、Link、TopologyGraph 等对象。

拓扑文件只负责描述物理网络，不负责承载调度策略。
调度参数、工作负载参数和统计参数分别放在独立文件中，避免一个文件混杂所有内容。

## 4. 建议目录结构

建议按以下目录组织系统：

```text
simulation/
├── plan.md
├── paper/
├── configs/
│   ├── topology/
│   │   └── topology.template.yaml
│   ├── workload/
│   │   └── workload.template.yaml
│   └── experiment/
│       └── experiment.template.yaml
├── simulator/
│   ├── topology/
│   ├── workload/
│   ├── core/
│   ├── schedulers/
│   ├── metrics/
│   └── experiment/
└── results/
```

含义如下：

1. configs/topology
   用户维护具体拓扑文件。不同实验可以对应不同拓扑实例。
2. configs/workload
   用户维护工作负载文件，包括作业、collective、chunk 参数等。
3. configs/experiment
   用户维护实验入口文件，指定使用哪个拓扑、哪个工作负载、哪个调度器和哪些公共参数。
4. simulator
   仿真系统源代码目录。
5. results
   实验产出目录，保存日志、csv、图表和中间状态快照。

## 5. 拓扑输入接口设计

### 5.1 拓扑文件职责

拓扑文件必须允许用户完整指定物理网络，而不是仅仅选择一个名字。
即使系统内置 FatTree 或 Leaf-Spine 生成器，也要允许用户通过文件覆盖默认行为。

支持两种拓扑输入模式：

1. 生成式输入
   用户只给出结构类型和关键参数，例如 FatTree 的 k 值、Leaf-Spine 的 spine 数和 leaf 数，由系统自动展开节点和链路。
2. 显式输入
   用户直接给出节点列表和链路列表，系统原样加载。

首版建议同时支持这两种方式。

### 5.2 拓扑文件字段

拓扑 YAML 至少包括以下段落：

1. meta
   记录拓扑名称、版本、说明。
2. topology
   记录结构类型与生成参数。
3. nodes
   记录节点列表，节点类型至少区分 gpu、host、switch。
4. links
   记录链路列表，包含 src、dst、bandwidth、latency。
5. routing
   记录候选路径、是否允许 ECMP、多路径上限。
6. constraints
   记录 oversubscription、端口限制、交换机缓存等全局约束。

### 5.3 拓扑文件示例字段说明

```yaml
meta:
  name: fat_tree_k4_example
  version: 1
  description: 4-ary fat-tree baseline

topology:
  mode: generated
  type: fat_tree
  parameters:
    k: 4
    hosts_per_tor: 2

nodes:
  gpu_per_host: 4
  host_count: 16
  switch_count: 20

links:
  default_bandwidth_gbps: 100
  default_latency_us: 2
  bidirectional: true
  overrides: []

routing:
  ecmp: true
  max_paths_per_pair: 8

constraints:
  oversubscription_ratio: 1.0
  switch_buffer_mb: 32
```

这里保留了用户最关心的拓扑输入能力：主机数量、交换机数量、网络结构、链路延迟、带宽、路径策略都能通过文件修改。

## 6. 工作负载输入接口设计

统一工作负载模型是这套系统能否公平对比的关键。

工作负载文件必须同时满足两种解释方式：

1. 能被 CRUX 解释为作业级训练通信负载。
2. 能被 TE-CCL 解释为集合通信和 chunk 级流量需求。

因此建议工作负载文件包含以下字段：

1. job_id
2. arrival_time
3. participants
4. total_data_mb
5. chunk_count
6. compute_phase_ms
7. communication_pattern
8. iteration_count
9. dependency_mode
10. repeat_interval_ms

其中 compute_phase_ms 是 CRUX 估计 GPU intensity 的关键字段，chunk_count 和 communication_pattern 是 TE-CCL 调度的关键字段。

## 7. 实验输入接口设计

实验文件是总入口，不直接定义拓扑细节，而是引用其他文件。

实验文件至少包括：

1. 拓扑文件路径。
2. 工作负载文件路径。
3. 调度器类型。
4. CRUX 独立参数块。
5. TE-CCL 独立参数块。
6. 仿真公共参数。
7. 指标开关。
8. 输出目录。
9. 重复次数与随机种子。

实验文件负责把“同一组公共环境”绑定起来，确保 CRUX 和 TE-CCL 比较时真正共享同一底座。
同时，实验文件中的调度参数必须按算法拆分，不能把 CRUX 参数和 TE-CCL 参数混在同一个平面字段里。

## 8. 统一内部对象模型

为了衔接文件输入和调度逻辑，内部建议维护以下核心对象：

### 8.1 拓扑对象

1. Node
   字段包括 node_id、node_type、attributes。
2. Link
   字段包括 link_id、src、dst、bandwidth_gbps、latency_us、capacity_state。
3. TopologyGraph
   字段包括 nodes、links、adjacency、candidate_paths。

### 8.2 工作负载对象

1. UnifiedJob
   字段包括 job_id、arrival_time、participants、compute_phase_ms、communication_demands。
2. CommunicationDemand
   字段包括 collective_type、total_size_mb、chunk_count、dependency_mode。
3. Chunk
   字段包括 chunk_id、size_mb、source_set、destination_set、ready_time。

### 8.3 运行时对象

1. FlowState
   记录流或 chunk 当前剩余大小、所在路径、优先级、是否完成。
2. LinkState
   记录链路实时负载、活跃发送集合、排队量。
3. RuntimeState
   汇总当前时间、活跃作业、活跃链路、调度器缓存状态。

## 9. 调度器统一接口

统一接口建议定义为：

1. on_workload_arrival
   新作业到达时调用。
2. on_flow_completion
   流或 chunk 完成时调用。
3. maybe_reschedule
   判断当前是否需要重算。
4. compute_schedule
   生成下一控制窗口内的调度结果。
5. export_debug_state
   导出调度器内部状态，用于归因分析。

统一输出格式建议为 ScheduleDecision：

1. decision_time
2. valid_until
3. flow_assignments
4. path_assignments
5. priority_assignments
6. epoch_actions

这样可以同时容纳 CRUX 的路径和优先级输出，以及 TE-CCL 的 epoch/chunk 调度输出。

### 9.1 模块职责细化

为便于直接开发，调度和执行模块进一步拆成以下职责：

1. config.loaders
   负责读取 topology、workload、experiment 三类 YAML 文件，并完成基础校验。
2. topology.builder
   负责把生成式或显式拓扑配置转换成统一 TopologyGraph。
3. workload.models
   负责统一作业、通信需求和 chunk 的内部表达。
4. core.models
   负责维护 RuntimeState、LinkState、FlowState 等运行时状态。
5. schedulers.base
   负责定义统一 Scheduler 抽象接口与 ScheduleDecision 输出格式。
6. schedulers.crux
   负责实现 CRUX 的强度评估、优先级映射和路径选择。
7. schedulers.teccl
   负责实现 TE-CCL 的 epoch 规划、chunk 动作生成和求解器封装。
8. experiment.runner
   负责根据 experiment 文件拼装各模块并驱动一次实验运行。

## 10. CRUX 接入方案

CRUX 模块的目标是根据 GPU intensity 优先保护更值得优先传输的作业。

在统一平台中的具体实现分为 5 步：

1. 从工作负载中读取 compute_phase_ms。
2. 从仿真观测中得到通信完成时间估计值。
3. 计算每个作业的 intensity 分数。
4. 按 intensity 排序后分配路径和优先级。
5. 当优先级数量超过硬件级别时执行压缩映射。

首版可以将 intensity 简化为：

```text
intensity = compute_phase_ms / observed_comm_time_ms
```

然后把该分数映射到调度窗口内的 path 和 priority 决策上。

## 11. TE-CCL 接入方案

TE-CCL 模块的目标是在链路容量、传播时延和节点约束下，为 chunk 级集合通信生成时间展开的发送计划。

在统一平台中的接入分为 5 步：

1. 从工作负载中读取 collective 和 chunk 信息。
2. 将 chunk 和 epoch 映射为时间展开状态。
3. 区分 GPU 节点和交换机节点的约束。
4. 生成某个控制窗口内的 chunk 发送动作。
5. 将动作交给底层链路执行器推进。

其中必须保留两类节点差异：

1. GPU 节点允许复制和缓存。
2. 交换机节点不允许复制，只做传统转发。

首版可以把求解器做成接口：

1. exact_milp_solver
2. heuristic_solver
3. small_scale_debug_solver

这样后续可以在不改系统架构的前提下替换求解后端。

### 11.1 TE-CCL 策略必须明确的语义

这里对 TE-CCL 策略做出明确修正，后续实现必须遵守，不能把它退化成普通最短路或普通多商品流调度。

1. 调度粒度是 chunk，而不是 job。
2. 决策时间是 epoch，而不是纯事件触发。
3. GPU 节点允许复制 chunk，并允许缓存收到的数据。
4. 交换机节点不允许复制，也不承担长期缓存语义。
5. 链路传播时延必须进入约束，而不是只用链路带宽近似。
6. 输出结果必须是时间展开的动作表，而不是单次静态路径分配。

换言之，TE-CCL 在这套系统中的策略定义不是“给每个流找一条路”，而是“在每个 epoch 为每个 chunk 生成受约束的发送动作集合”。

### 11.2 TE-CCL 的首版实现策略

首版不要求一步到位实现完整大规模 MILP，但必须按以下层次推进：

1. 语义层完整
   必须完整保留 GPU 可复制、交换机不可复制、GPU 有 buffer、交换机无长期 buffer、链路时延进 epoch 约束这些核心语义。
2. 求解层可降级
   允许求解器从 exact MILP 降级为小规模精确求解或启发式求解，但不能改变上述语义。
3. 执行层统一
   无论求解器后端是什么，都必须输出统一的 epoch_actions，交给相同的底层执行器推进。

因此 TE-CCL 的首版策略推荐定义为：

1. 小规模场景
   使用 exact_milp_solver 或 small_scale_debug_solver，验证模型语义正确。
2. 中大规模场景
   使用 heuristic_solver，在固定 epoch 窗口内产生近似动作表。
3. 所有场景
   统一输出 chunk 在某个 epoch 的发送动作，不直接输出“最终路径”。

### 11.3 TE-CCL 的内部状态设计

为保证策略清晰，TE-CCL 模块内部至少维护以下状态：

1. flow[src, u, v, epoch, chunk]
   表示源 src 的 chunk 是否在某个 epoch 从 u 发送到 v。
2. buffer[src, node, epoch, chunk]
   表示某个 GPU 节点在某个 epoch 是否已经持有或缓存该 chunk。
3. link_delay_epochs[u, v]
   表示链路传播时延折算成的 epoch 数。
4. destination_set[src, chunk]
   表示该 chunk 的目标节点集合。

这里必须明确区分两类节点约束：

1. GPU 节点约束
   使用 buffer + incoming >= outgoing 的语义，允许复制。
2. 交换机节点约束
   使用 incoming = outgoing 的语义，不允许复制。

### 11.4 TE-CCL 的统一输出格式

为了避免 TE-CCL 实现偏离统一平台，TE-CCL 调度器输出必须映射到 ScheduleDecision 中的 epoch_actions。每个动作至少包括：

1. epoch_index
2. chunk_id
3. source_gpu
4. current_node
5. next_node
6. path_token 或 route_fragment
7. expected_arrival_epoch

底层执行器只消费这些动作，不直接理解 MILP 变量名。

### 11.5 TE-CCL 与 CRUX 的对比边界

为了保证对比公平但不失真，计划明确以下边界：

1. CRUX 不强制模拟 GPU 复制语义。
2. TE-CCL 不退化成 CRUX 式 job-level priority。
3. 两者共享同一拓扑、同一链路时延、同一数据规模、同一 chunk 粒度和同一工作负载入口。
4. 两者允许拥有不同的内部控制粒度，因为这是算法本身的一部分，而不是环境差异。

## 12. 时间推进与桥接层

这套系统最大的不一致来自时间语义差异：

1. CRUX 更偏事件驱动。
2. TE-CCL 更偏 epoch 驱动。

计划采用统一离散事件时钟，并把 epoch 视作固定控制窗口：

1. 底层仿真时间连续推进到下一个事件点。
2. TE-CCL 只在 epoch 边界上重算。
3. CRUX 在作业到达、作业完成、拥塞变化时重算。

这样二者共享同一事件执行器，但控制策略更新频率不同。

## 13. 指标体系

指标必须同时覆盖平台通用指标和算法特色指标。

### 13.1 平台通用指标

1. Average Flow Completion Time
2. Network Throughput
3. Link Utilization
4. Queue Backlog
5. Scheduler Runtime Overhead

### 13.2 CRUX 重点指标

1. Job Completion Time
2. Iteration Time
3. GPU Utilization

### 13.3 TE-CCL 重点指标

1. Collective Completion Time
2. Epoch Count to Completion
3. Chunk Delivery Efficiency

### 13.4 归因类输出

1. 每条链路的时间序列负载曲线。
2. 每个作业的计算阶段与通信阶段耗时拆分。
3. 每个 collective 的 chunk 发送时间表。
4. 每轮调度的路径与优先级快照。

## 14. 公平对比规则

下列参数必须在两种算法之间严格保持一致：

1. 拓扑文件。
2. 链路带宽与时延。
3. 主机与交换机数量。
4. GPU 参与节点集合。
5. 总数据规模。
6. chunk 粒度。
7. 计算阶段时长。
8. 作业到达模式。
9. 随机种子。
10. 实验重复次数。

只有以下参数允许因算法而不同：

1. CRUX 的优先级等级数、强度权重、路径选择策略。
2. TE-CCL 的 epoch 长度、求解时限、求解后端类型。

## 15. 实验矩阵

建议至少做 4 组实验：

### 15.1 组一：最小可运行实验

目标是验证系统通路正确。

1. 小规模拓扑。
2. 少量 GPU 节点。
3. 单作业与双作业场景。
4. 比较两种算法都能正常完成传输。

### 15.2 组二：规模扩展实验

目标是观察拓扑和节点规模变化下的趋势。

1. 扫描主机数量。
2. 扫描交换机数量。
3. 扫描 GPU 数量。
4. 扫描 oversubscription。

### 15.3 组三：负载敏感性实验

目标是观察不同业务形态对算法效果的影响。

1. 扫描总数据量。
2. 扫描 chunk 数量。
3. 扫描计算通信比。
4. 扫描作业到达间隔。

### 15.4 组四：算法参数敏感性实验

目标是理解算法内部参数对结果的影响。

1. CRUX：优先级等级数、路径候选数、强度计算窗口。
2. TE-CCL：epoch 大小、求解时限、求解后端。

## 16. 实现顺序

建议按以下顺序实施：

1. 先完成文件加载器和配置校验器。
2. 再完成拓扑对象与路径枚举模块。
3. 再完成统一工作负载对象。
4. 再完成事件执行器和链路共享逻辑。
5. 先接入 CRUX，打通第一条端到端主路径。
6. 再接入 TE-CCL，补齐 epoch 和 chunk 级控制。
7. 最后补实验管理、批量运行和结果导出。

这样可以尽快得到一个能跑通的基线版本，同时把复杂的 TE-CCL 求解逻辑放到平台稳定之后再接入。

### 16.1 第一阶段代码骨架范围

第一阶段代码骨架只做基础结构，不做完整求解：

1. 配置文件数据模型与加载器。
2. 拓扑对象与基础构建器。
3. 统一工作负载对象。
4. RuntimeState、FlowState、LinkState 等运行时对象。
5. 调度器抽象类与 CRUX、TE-CCL 占位实现。
6. 一个 experiment runner 骨架，用于把配置加载和模块初始化串起来。

这一阶段的目标不是跑出最终实验结果，而是固定接口，避免后面反复返工。

## 17. 类接口草案

### 17.1 配置加载接口

1. load_topology_config(path)
2. load_workload_config(path)
3. load_experiment_config(path)

### 17.2 拓扑构建接口

1. build_topology(topology_config)
2. enumerate_candidate_paths(topology_graph, routing_config)

### 17.3 调度器接口

1. on_workload_arrival(job, runtime_state)
2. maybe_reschedule(runtime_state)
3. compute_schedule(runtime_state)
4. export_debug_state()

### 17.4 实验运行接口

1. load_inputs(experiment_config)
2. build_runtime(topology, workload)
3. create_scheduler(experiment_config)
4. run()
5. export_results()

## 18. 首版交付物

首版至少包含以下内容：

1. 一个 plan.md 设计文档。
2. 一个拓扑模板文件。
3. 一个工作负载模板文件。
4. 一个实验入口模板文件。
5. 一套统一对象与接口定义。
6. 一套基础指标导出格式。

## 19. 风险与应对

### 18.1 粒度不一致风险

CRUX 是作业级，TE-CCL 是 chunk 级。
应对方法是用统一工作负载对象做桥接，而不是强行让两者共用完全相同的内部状态机。

### 18.2 TE-CCL 求解复杂度风险

大规模 MILP 可能过慢。
应对方法是首版保留求解器接口，优先支持小规模精确求解和大规模启发式近似。

### 18.3 输入文件复杂度风险

配置文件过大后容易出错。
应对方法是提供模板、字段校验和默认值填充逻辑。

## 20. 与用户输入直接相关的结论

你强调的“拓扑相关信息必须给用户预留输入接口，并且以文件形式提供”，本计划已经将其固定为硬要求：

1. 拓扑不允许写死在代码里。
2. 主机节点数量必须来自拓扑文件。
3. 交换机节点数量必须来自拓扑文件。
4. 网络结构必须来自拓扑文件。
5. 链路延迟必须来自拓扑文件。
6. 链路带宽、路径开关、约束条件也必须来自拓扑文件。

后续实现时，代码只能消费这些文件，不能绕过文件直接改内部参数。