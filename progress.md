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
- 运行时执行器、统一工作负载转换、CRUX 路径逻辑、TE-CCL 小规模求解器和指标导出尚未完成。

### 下一步建议
- 优先完成 stage-03-unified-workload-model，补齐统一工作负载语义转换。
- 接着完成最小 runtime engine，使 scheduler 输出可以驱动一次基础仿真。
- 然后分别补齐 CRUX 基线和 TE-CCL 小规模求解后端。

### 交接约束
- 所有与 Python 相关的操作必须在 conda 的 networkSimulation 虚拟环境下进行。
- 任何新的上下文窗口开始工作前，必须先读取 prompt.md、progress.md、feature_list.json 和 plan.md。
- feature_list.json 中的步骤内容不应随意改写，完成状态只更新 passes 字段。