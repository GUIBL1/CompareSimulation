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
- TE-CCL 深化语义、小规模求解器和指标导出尚未完成。

### 下一步建议
- 优先完成 stage-06-teccl-semantics，补齐 TE-CCL 的 epoch、buffer 和节点类型差异语义。
- 然后完成 TE-CCL 小规模求解后端。

### 交接约束
- 所有与 Python 相关的操作必须在 conda 的 networkSimulation 虚拟环境下进行。
- 任何新的上下文窗口开始工作前，必须先读取 prompt.md、progress.md、feature_list.json 和 plan.md。
- feature_list.json 中的步骤内容不应随意改写，完成状态只更新 passes 字段。