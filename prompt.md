# 项目开发提示词

你正在参与一个位于 /home/code/simulation 的联合仿真系统开发项目。项目目标是在同一套 Python 离散事件仿真底座中实现并对比 CRUX 与 TE-CCL 两种调度策略，并确保对比过程使用统一的拓扑、链路参数、工作负载和指标体系。

## 每次进入新上下文窗口后的第一步

按以下顺序读取文件并建立上下文：

1. 读取 plan.md，理解整体架构、模块边界、TE-CCL 语义和文件化输入约束。
2. 读取 progress.md，确认当前已经完成的工作、当前状态和下一步建议。
3. 读取 handoff.md，确认当前可复用的结果资产、关键设计决策和明确下一步。
4. 读取 feature_list.json，选择最高优先级且 passes 为 false 的一项作为当前会话唯一目标。
5. 读取 configs 目录中的相关模板文件，确认输入契约没有被破坏。
6. 如需开发代码，再读取 simulator 目录下与当前任务直接相关的模块。

## 必须遵守的硬约束

1. 所有与 Python 相关的操作都必须在 conda 的 networkSimulation 虚拟环境下进行。
2. 不允许在未进入 networkSimulation 环境的情况下运行 python、pytest、pip、pylance 相关检查或任何 Python 脚本。
3. 如果需要在终端中执行 Python 命令，先确保当前环境是 networkSimulation；如果不确定，先重新激活它。
4. 拓扑相关信息必须通过文件输入，不能在代码中写死主机数量、交换机数量、网络结构、链路带宽、链路时延等核心参数。
5. feature_list.json 里每个 feature 的 steps 是验收依据，只允许更新 passes 字段，不要改写既有步骤。
6. 每个上下文窗口只解决一个 feature，避免一次同时推进多个阶段导致状态混乱。

## 推荐工作流

1. 进入项目目录后，先确认当前 shell 和 conda 环境。
2. 阅读 progress.md 和 feature_list.json，识别当前最合适的未完成 feature。
3. 只实现一个 feature 所需的最小改动。
4. 完成后进行必要的验证，优先做与当前 feature 直接相关的验证。
5. 更新 progress.md，记录本次改动、关键文件、验证结果和下一步。
6. 只有在 feature 满足其 steps 后，才把对应的 passes 改为 true。

## 关于 CRUX 与 TE-CCL 的实现边界

1. CRUX 是 job-level 的路径与优先级调度，不需要强行模拟 GPU 复制语义。
2. TE-CCL 不是普通最短路调度，它必须保留 chunk 级、epoch 驱动、GPU 可复制且有 buffer、交换机不可复制且不承担长期 buffer 的语义。
3. TE-CCL 的输出必须是时间展开的 epoch_actions，而不是单次静态路径分配。
4. 两者可以拥有不同内部控制粒度，但必须共享相同公共环境输入与统计口径。

## 建议的会话结束动作

1. 确认代码处于一致状态，没有留下半写的接口或未解释的设计改动。
2. 更新 progress.md，明确本次完成内容、验证情况和下一步。
3. 如本次改动影响长期交接，更新 handoff.md 或重生成归因报告。
4. 如果某个 feature 已满足其 steps，再把 feature_list.json 中对应的 passes 更新为 true。
5. 若遇到阻塞，必须在 progress.md 中明确记录阻塞原因和建议的解决方向。