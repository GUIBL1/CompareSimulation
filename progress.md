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

### 已完成内容
- 已形成联合仿真系统详细设计文档，明确统一平台、统一工作负载抽象和文件化拓扑输入要求。
- 已在 configs 目录下建立 topology、workload、experiment 三类模板文件。
- 已创建 simulator 代码骨架，包括配置模型、加载器、拓扑模型、统一工作负载模型、运行时对象、CRUX 调度器骨架、TE-CCL 调度器骨架和 experiment runner 骨架。
- 已修正 experiment 配置结构，使 CRUX 和 TE-CCL 参数分块独立。
- 已明确 TE-CCL 策略边界：chunk 级、epoch 驱动、GPU 可复制且有 buffer、交换机不可复制且不承担长期 buffer、输出为 epoch_actions。

### 当前代码状态
- 文档和配置模板已落地。
- Python 代码骨架已落地并通过基础静态校验。
- 运行时执行器、候选路径枚举、CRUX 路径逻辑、TE-CCL 小规模求解器和指标导出尚未完成。

### 下一步建议
- 优先完成 topology builder 的真实展开逻辑与候选路径枚举。
- 接着完成最小 runtime engine，使 scheduler 输出可以驱动一次基础仿真。
- 然后分别补齐 CRUX 基线和 TE-CCL 小规模求解后端。

### 交接约束
- 所有与 Python 相关的操作必须在 conda 的 networkSimulation 虚拟环境下进行。
- 任何新的上下文窗口开始工作前，必须先读取 prompt.md、progress.md、feature_list.json 和 plan.md。
- feature_list.json 中的步骤内容不应随意改写，完成状态只更新 passes 字段。