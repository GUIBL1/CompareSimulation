# 项目进度日志

## 系统信息
- 操作系统: Linux
- Shell: zsh
- 工作目录: /home/code/simulation
- Python 环境: conda 的 networkSimulation
- 创建日期: 2026-03-06

---

## 2026-03-06 任务初始化
- 实现: 建立联合仿真系统的长运行交接文件。
- 文件: plan.md, feature_list.json, progress.md, prompt.md
- 状态: 进行中

## 2026-03-06 运行时基线阶段
- 实现: 完成 stage-04-runtime-engine-baseline。
- 文件: simulator/core/models.py, simulator/core/engine.py, simulator/core/__init__.py, simulator/experiment/runner.py, configs/workload/workload.template.yaml, feature_list.json, progress.md
- 状态: ✅ 已完成

## 2026-03-06 CRUX 基线阶段
- 实现: 完成 stage-05-crux-baseline。
- 文件: simulator/schedulers/crux.py, feature_list.json, progress.md
- 状态: ✅ 已完成

## 2026-03-06 TE-CCL 语义阶段
- 实现: 完成 stage-06-teccl-semantics。
- 文件: simulator/schedulers/base.py, simulator/schedulers/teccl.py, simulator/core/engine.py, feature_list.json, progress.md
- 状态: ✅ 已完成

## 2026-03-06 TE-CCL 小规模求解阶段
- 实现: 完成 stage-07-teccl-small-scale-solver。
- 文件: simulator/schedulers/teccl_solver.py, simulator/schedulers/teccl.py, feature_list.json, progress.md
- 状态: ✅ 已完成

## 2026-03-06 TE-CCL 启发式后端阶段
- 实现: 完成 stage-08-teccl-heuristic-backend。
- 文件: simulator/schedulers/teccl_solver.py, simulator/schedulers/teccl.py, feature_list.json, progress.md
- 状态: ✅ 已完成

### 本次改动
- 在 [simulator/schedulers/teccl_solver.py](simulator/schedulers/teccl_solver.py) 中新增 heuristic_solver，复用 small_scale_debug_solver 的候选生成、约束报告和 epoch_actions 输出契约。
- 将启发式搜索实现为按候选收益排序的贪心选择，优先选择更早到达、直达且更短路径的动作，同时保持交换机不可复制约束。
- 将 heuristic_solver 接入 [simulator/schedulers/teccl.py](simulator/schedulers/teccl.py)，与 small_scale_debug_solver 共享同一 scheduler 接口和 solver_reports 输出。
- 在 solver metadata 中写入适用范围与误差边界，明确该后端适用于中大规模候选空间，但不保证对 delivered destination count 的全局最优性。

### 验证结果
- heuristic_solver 已能通过 solver_backend 开关被正确选择，且输出的 epoch_actions metadata 中正确标记为 heuristic_solver。
- 在 direct GPU 三节点 broadcast 小样例上，heuristic_solver 输出 2 个 GPU 复制动作，与 small_scale_debug_solver 的行为一致。
- 在 GPU-switch-GPU 小样例上，heuristic_solver 仍保持交换机 incoming_count = 1、outgoing_count = 1 的约束语义，没有破坏交换机不可复制假设。
- heuristic_solver 的 solver_reports 中已包含 applicability 和 error_boundary，可直接用于后续对比记录。

### 下一步建议
- 进入 stage-09-runner-and-metrics，补齐 experiment runner 的结果导出、链路负载时间序列和调度器调试状态持久化。
- 让 CRUX 与 TE-CCL 两类后端的运行结果都能稳定写入统一结果目录，供后续实验矩阵和绘图使用。

### 本次改动
- 新增 [simulator/schedulers/teccl_solver.py](simulator/schedulers/teccl_solver.py)，实现 small_scale_debug_solver，用于按 epoch 对小规模 TE-CCL 动作做精确枚举搜索。
- 在求解器中显式区分 GPU 与交换机约束：GPU 候选动作以 buffer 可用性为前提，并在允许复制时支持多个 outgoing；交换机候选动作仅在 arrival epoch 存在，并在不允许复制时最多选择一个 outgoing。
- 为求解器增加 constraint_reports 和 selected_candidates 调试输出，便于直接检查流守恒假设是否符合 TE-CCL 论文语义。
- 将求解结果稳定转换为统一 epoch_actions，并通过 solver_reports 接入 TECCL scheduler 的 metadata 与 debug state。

### 验证结果
- 在 direct GPU 三节点 broadcast 小样例上，small_scale_debug_solver 输出 2 个 GPU 动作，证明 GPU 复制没有被误退化成 incoming 等于 outgoing。
- 复制样例的 constraint_reports 显示 GPU 节点 outgoing_count 为 2，且 gpu_buffer_available 为 true，符合“GPU 可复制且有 buffer”的约束语义。
- 在 GPU-switch-GPU 样例上，epoch 1 的 solver 输出仅包含 switch 到 GPU 的动作，constraint_reports 显示 switch 节点 incoming_count 为 1、outgoing_count 为 1，符合“交换机不可复制”的约束语义。
- 所有求解动作都被转换为统一 epoch_actions，并在 metadata 中标记 solver_backend 为 small_scale_debug_solver。

### 下一步建议
- 进入 stage-08-teccl-heuristic-backend，基于当前 solver/state/output 契约实现中大规模启发式后端。
- 保持与 small_scale_debug_solver 相同的 epoch_actions 输出格式，以便后续直接做行为对比。

### 本次改动
- 为 TE-CCL 增加内部状态模型，显式表示 job 级 epoch 状态、chunk replica、GPU buffer、交换机瞬时到达状态和 in-flight 目标。
- 将 GPU 与交换机的语义分开实现：GPU 支持复制并可持久保留 chunk buffer，交换机不支持复制且只在到达 epoch 参与瞬时转发。
- 将链路 latency_us 折算为 expected_arrival_epoch，并把该信息写入 epoch_actions 和 runtime flow metadata。
- 将 TE-CCL 输出固定为带 metadata 的 epoch_actions，而不是退化回普通 job-level path 分配。
- 修正 runtime bridge：TE-CCL 在空 epoch 时不再错误回退到 CRUX 风格的 job-level flow 物化。

### 验证结果
- 在 direct GPU 三节点拓扑上验证了 GPU 复制语义：同一 epoch 内，source GPU 可同时向两个目的 GPU 发起动作，两个动作的 expected_arrival_epoch 都正确为 2。
- 在 GPU-switch-GPU 拓扑上验证了交换机无长期 buffer 语义：epoch 0 为 GPU 到交换机，epoch 1 为交换机到目的 GPU，epoch 2 时旧的交换机到达状态不会继续保留并触发转发。
- 在上述验证中，链路 1500 us 和 1000 us 时延均已正确折算为 epoch 到达约束。
- 对 runtime bridge 做回归检查后，TE-CCL 执行过程中不再出现错误的 job-level fallback flow。

### 下一步建议
- 进入 stage-07-teccl-small-scale-solver，基于当前语义状态补 small_scale_debug_solver 或等价小规模精确后端。
- 在求解后端中显式区分 GPU 流守恒与交换机流守恒，并把求解结果稳定转换为统一 epoch_actions。

### 本次改动
- 为 CRUX 增加 observed_comm_time 的作业级刷新逻辑，基于通信窗口而不是单 flow 平均耗时更新 intensity 估计。
- 将作业按 intensity、到达时间和 job_id 做稳定排序，并将 rank 压缩到有限 priority level。
- 为每条 flow 增加候选路径选择逻辑，按链路利用率、路径争用和路径长度选择更优路径。
- 为 CRUX 增加路径缓存和调试状态导出，保证相同输入下 path_assignments 稳定。

### 验证结果
- 通过 experiment.template.yaml 的 CRUX 基线实验验证，schedule_history 中已稳定输出 intensity_scores、priority_assignments 和 192 条 path_assignments。
- 在双作业冒烟实验中，compute_phase_ms 更高的 job_fast 获得更高优先级，priority_assignments 为 {'job_fast': 0, 'job_slow': 1}。
- 同一运行时输入下连续两次 compute_schedule 得到一致的 path_assignments，稳定性验证通过。
- observed_comm_time 修正后，模板实验中的 intensity 不再异常放大，而是回落到合理的作业级通信时间量级。

### 下一步建议
- 进入 stage-06-teccl-semantics，补齐 epoch、chunk、flow、buffer 的显式状态表示。
- 把 GPU 可复制且有 buffer、交换机不可复制且无长期 buffer 的 TE-CCL 语义真正映射到内部运行时对象上。

### 本次改动
- 扩展 RuntimeState、FlowState、LinkState，并新增 RuntimeEvent，补齐 flow、link 和作业生命周期所需的运行时状态。
- 新增 simulator/core/engine.py，实现最小事件驱动执行器、事件队列、时间推进、完成事件处理和作业完成判定。
- 在执行器中实现 max-min fair 近似：按链路上的活跃流数量分摊带宽，并取路径瓶颈作为 flow 的有效带宽。
- 将调度器输出真正落成可执行 flow：CRUX 通过候选路径实例化 flow，TE-CCL 的 epoch_actions 也会展开成真实链路传输。
- 为 simulator/experiment/runner.py 增加 run 主路径，使实验配置可以直接驱动 runtime engine。
- 修正 configs/workload/workload.template.yaml 中的 GPU 参与者命名，使其与生成拓扑实际节点一致。

### 验证结果
- 在 networkSimulation 环境中通过 configs/experiment/experiment.template.yaml 跑通 CRUX 基线路径，运行结束时间为 655.36 ms，完成 192 条 flow，6 条链路出现实际传输。
- 在最小 explicit 拓扑上跑通 TE-CCL runtime 冒烟验证，2 条 flow 在 3.2 ms 内完成，2 条链路记录到传输量。
- 终止条件已修正：所有作业完成后，运行时不会继续无意义地推进到 max_time_ms。

### 下一步建议
- 进入 stage-05-crux-baseline，补齐候选路径选择、优先级压缩和稳定决策输出。
- 让 CRUX 不再只输出 job 级 priority_assignments，而是开始输出更具体的 path_assignments。

### 本次改动
- 为 Chunk 增加了 chunk_index、dependency_parent_ids、collective_type 和 metadata，使 chunk 不再只是简单的数据切片。
- 为 CommunicationDemand 增加了 demand_id、participants、source_set、destination_set、chunk_size_mb 和 metadata，明确 collective 内部语义。
- 为 UnifiedJob 增加 metadata，并把 communication_pattern、dependency_mode、chunk_count 等公共字段标准化保留下来。
- 实现了 communication_pattern 的内部归一化和 source_set/destination_set 推导，覆盖 all_reduce、broadcast、reduce、point_to_point 等常见模式。
- 实现了 dependency_mode 的内部语义转换，支持 independent、strict 和 barrier 类 chunk 依赖表示。
- 调整 TE-CCL 骨架，使其消费 chunk.source_set 和 chunk.destination_set，而不是硬编码使用第一个参与者作为源。

### 验证结果
- 在 networkSimulation 环境中成功将 workload.template.yaml 转换为 UnifiedJob，并保留 compute_phase_ms、chunk_count、participants 等关键字段。
- all_reduce 模板样例被标准化为 many_to_many 语义，chunk_size_mb 正确为 64.0 MB。
- broadcast 样例正确推导出单源多目的语义，reduce 样例正确推导出多源单目的语义。
- strict dependency_mode 能为后续 chunk 生成链式 dependency_parent_ids。
- TE-CCL 调度骨架已能基于统一 workload 语义为 broadcast 生成 epoch_actions。

### 下一步建议
- 进入 stage-04-runtime-engine-baseline，补齐 RuntimeState、LinkState、FlowState 的生命周期和事件推进。
- 让 scheduler 的统一输出真正驱动链路占用、带宽共享和完成事件，而不再停留在静态决策层。
- 状态: 进行中

## 2026-03-06 配置契约阶段
- 实现: 完成 stage-01-config-contracts。
- 文件: simulator/config/models.py, simulator/config/loaders.py, feature_list.json, progress.md
- 状态: ✅ 已完成

## 2026-03-06 拓扑构建阶段
- 实现: 完成 stage-02-topology-loader-builder。
- 文件: simulator/topology/builder.py, feature_list.json, progress.md
- 状态: ✅ 已完成

### 本次改动
- 为 explicit 拓扑补充了节点存在性校验和链路对象构建逻辑。
- 为 generated 模式补充了 fat-tree 展开，生成 host、gpu、tor、aggregation、core 节点及其链路关系。
- 将链路带宽、时延、单双向属性和 overrides 映射到内部 Link 对象。
- 为 gpu 和 host 端点对生成最短路径候选集，并写入 TopologyGraph.candidate_paths。

### 验证结果
- 在 networkSimulation 环境中成功构建 topology.template.yaml，对应 fat-tree 生成 100 个节点、112 条链路、6320 组候选路径。
- 冒烟验证显示 gpu_0_0 到 gpu_1_0 已能生成可消费路径。
- 额外用内存中的 explicit 拓扑验证了 explicit_links、链路 overrides 和 candidate_paths 输出。

### 下一步建议
- 进入 stage-03-unified-workload-model，补齐 UnifiedJob、CommunicationDemand、Chunk 的语义转换与字段归一化。
- 让 workload 层直接为后续 CRUX 和 TE-CCL 调度器暴露 chunk_count、participants、compute_phase_ms 等公共字段。

### 本次改动
- 为 topology、workload、experiment 三类配置补充了加载阶段的契约校验。
- 为缺省字段补充了默认值，并把缺失输入文件、错误 section 类型、非法取值等情况统一改为明确报错。
- 为 generated 拓扑补充了配置规范化逻辑，兼容 gpu_per_host 位于 topology.parameters 的模板写法。
- 在 conda 的 networkSimulation 环境中安装了 PyYAML，并用三类模板文件完成了真实加载验证。

### 验证结果
- topology.template.yaml 已成功解析，得到 generated 模式、16 个 host、每 host 4 个 GPU、100 Gbps 默认链路带宽。
- workload.template.yaml 已成功解析，保留了 CRUX 所需 compute_phase_ms 和 TE-CCL 所需 chunk/collective 字段。
- experiment.template.yaml 已成功解析，并正确识别 crux 与 teccl 分块参数结构。

### 下一步建议
- 进入 stage-02-topology-loader-builder，优先完成 generated 拓扑的真实展开逻辑和 explicit 模式的对象构建。
- 在 topology builder 中补候选路径枚举接口，为后续 CRUX 和 TE-CCL 共用。

### 已完成内容
- 已形成联合仿真系统详细设计文档，明确统一平台、统一工作负载抽象和文件化拓扑输入要求。
- 已在 configs 目录下建立 topology、workload、experiment 三类模板文件。
- 已创建 simulator 代码骨架，包括配置模型、加载器、拓扑模型、统一工作负载模型、运行时对象、CRUX 调度器骨架、TE-CCL 调度器骨架和 experiment runner 骨架。
- 已修正 experiment 配置结构，使 CRUX 和 TE-CCL 参数分块独立。
- 已明确 TE-CCL 策略边界：chunk 级、epoch 驱动、GPU 可复制且有 buffer、交换机不可复制且不承担长期 buffer、输出为 epoch_actions。
- 已完成 stage-01-config-contracts，三类配置模板能在 networkSimulation 环境下通过加载器校验和解析。

### 当前代码状态
- 文档和配置模板已落地。
- Python 代码骨架已落地并通过基础静态校验。
- 已完成 topology builder 的 generated/explicit 双模式构建与候选路径枚举。
- 已完成统一工作负载语义转换，覆盖 chunk 切分、collective 源宿集合和 dependency_mode 归一化。
- 已完成最小离散事件执行器、链路带宽共享基线和 runner.run 主路径。
- 已完成 CRUX 基线的 intensity 排序、priority 压缩和 candidate path 选择。
- 已完成 TE-CCL 的 epoch/chunk/buffer 语义和 GPU/交换机差异化状态表示。
- 已完成 TE-CCL 小规模可验证求解后端，并能输出约束报告与统一 epoch_actions。
- 已完成 TE-CCL 启发式后端，并复用相同 solver_reports 与 epoch_actions 契约。
- 指标导出尚未完成。

### 下一步建议
- 优先完成 stage-09-runner-and-metrics，补齐结果导出与指标系统。
- 然后完成最小端到端实验验证。

## 2026-03-06 Runner 与指标导出阶段
- 实现: 完成 stage-09-runner-and-metrics。
- 文件: simulator/core/models.py, simulator/core/engine.py, simulator/metrics/__init__.py, simulator/metrics/exporters.py, simulator/experiment/runner.py, feature_list.json, progress.md
- 状态: ✅ 已完成

### 本次改动
- 将 [simulator/experiment/runner.py](simulator/experiment/runner.py) 从单次运行扩展为按 repetition 批量执行，并新增 export_results 主路径，统一返回实验级结果对象。
- 在 [simulator/core/engine.py](simulator/core/engine.py) 和 [simulator/core/models.py](simulator/core/models.py) 中为每条链路补充 utilization_history，记录初始态、带宽重分配和时间推进后的负载时序。
- 新增 [simulator/metrics/exporters.py](simulator/metrics/exporters.py)，统一导出 summary、链路负载时间序列、flow trace、schedule history 和 scheduler debug state。
- 导出结果同时覆盖通用指标、CRUX 指标和 TE-CCL 指标，并保持 CSV 与 JSON 两种可复用格式，便于后续绘图与公平对比分析。

### 验证结果
- 在 networkSimulation 环境中通过 experiment.template.yaml 跑通 CRUX runner.export_results，成功生成 summary.json、summary.csv、link_load_trace.csv、scheduler_debug.json、flow_trace.csv 和 schedule_history.json。
- summary 文件已包含 completion_time_ms、completed_flow_count、average_link_utilization 等通用指标，以及 CRUX 专属 intensity/path/priority 指标。
- link_load_trace 文件已按 repetition、link_id 和 time_ms 记录链路负载时间序列，可直接用于后续绘图。
- scheduler_debug.json 已持久化 scheduler.export_debug_state 和 schedule_history，能够复用到后续归因分析。

### 下一步建议
- 进入 stage-10-minimal-end-to-end-experiments，分别用公共环境参数跑通 CRUX 与 TE-CCL 的最小实验。
- 检查结果目录中的统一指标文件是否已足够支撑后续实验矩阵与对比图表。

## 2026-03-07 最小端到端实验阶段
- 实现: 完成 stage-10-minimal-end-to-end-experiments。
- 文件: configs/topology/minimal_e2e_topology.yaml, configs/workload/minimal_e2e_workload.yaml, configs/experiment/minimal_crux_e2e.yaml, configs/experiment/minimal_teccl_e2e.yaml, simulator/experiment/runner.py, simulator/core/engine.py, simulator/schedulers/teccl.py, feature_list.json, progress.md
- 状态: ✅ 已完成

### 本次改动
- 新增共享的最小 explicit 拓扑 [configs/topology/minimal_e2e_topology.yaml](configs/topology/minimal_e2e_topology.yaml) 和共享工作负载 [configs/workload/minimal_e2e_workload.yaml](configs/workload/minimal_e2e_workload.yaml)，让 CRUX 与 TE-CCL 在完全相同的公共输入下执行。
- 新增两个最小实验入口 [configs/experiment/minimal_crux_e2e.yaml](configs/experiment/minimal_crux_e2e.yaml) 与 [configs/experiment/minimal_teccl_e2e.yaml](configs/experiment/minimal_teccl_e2e.yaml)，只切换 scheduler 类型与输出目录，不改变公共环境参数。
- 为 TE-CCL end-to-end 路径补齐 solver 结果到内部状态的回写逻辑，并修正实际流完成时的 arrival epoch 同步与 job 完成判定，确保 epoch_actions 能真正落成统一 flow 输出。
- 为 runtime metadata 增加 scheduler_type，避免 TE-CCL 被错误套用 CRUX 式“所有 flow 完成即 job 完成”的终止条件。

### 验证结果
- 在 networkSimulation 环境中用 [configs/experiment/minimal_crux_e2e.yaml](configs/experiment/minimal_crux_e2e.yaml) 跑通 CRUX 最小实验，结果目录 [results/minimal_crux_e2e](results/minimal_crux_e2e) 已生成 summary、flow_trace、scheduler_debug、link_load_trace 和 schedule_history 文件；作业在 15.36 ms 完成，共输出 4 条统一 flow。
- 在相同 topology 与 workload 下用 [configs/experiment/minimal_teccl_e2e.yaml](configs/experiment/minimal_teccl_e2e.yaml) 跑通 TE-CCL 最小实验，结果目录 [results/minimal_teccl_e2e](results/minimal_teccl_e2e) 已生成同构结果文件；作业在 40.0 ms 完成，共输出 10 条统一 flow，summary 中 teccl_completed_replica_count 为 2。
- CRUX 的 [results/minimal_crux_e2e/scheduler_debug.json](results/minimal_crux_e2e/scheduler_debug.json) 已稳定记录 priority_assignments 与 path_assignments；TE-CCL 的 [results/minimal_teccl_e2e/scheduler_debug.json](results/minimal_teccl_e2e/scheduler_debug.json) 已记录 completed_replica_ids、solver_reports 和逐 epoch 的状态演化。
- 两类调度器都通过 runner.export_results 生成了统一 summary.json、summary.csv、link_load_trace.csv、flow_trace.csv 和 schedule_history.json，可直接供后续对比矩阵与图表消费。

### 下一步建议
- 进入 stage-11-fair-comparison-matrix，固定公共拓扑、链路参数、数据规模、chunk 粒度和随机种子，开始构造公平对比矩阵。
- 将 minimal_e2e 这组实验作为后续回归基线，避免后续扩展破坏 CRUX 和 TE-CCL 的统一输出契约。

### 交接约束
- 所有与 Python 相关的操作必须在 conda 的 networkSimulation 虚拟环境下进行。
- 任何新的上下文窗口开始工作前，必须先读取 prompt.md、progress.md、feature_list.json 和 plan.md。
- feature_list.json 中的步骤内容不应随意改写，完成状态只更新 passes 字段。