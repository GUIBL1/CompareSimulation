# 项目交接说明

## 当前完成状态

- stage-01 到 stage-12 已全部完成，当前代码库已经具备统一配置加载、拓扑构建、统一工作负载、运行时执行器、CRUX、TE-CCL、结果导出、公平矩阵和交接归因资产。
- 最小端到端基线结果已经验证通过：CRUX 结果目录为 [results/minimal_crux_e2e](results/minimal_crux_e2e)，TE-CCL 结果目录为 [results/minimal_teccl_e2e](results/minimal_teccl_e2e)。
- 公平对比矩阵配置位于 [configs/experiment/fair_comparison_matrix.yaml](configs/experiment/fair_comparison_matrix.yaml)，矩阵枚举与校验入口位于 [simulator/experiment/matrix.py](simulator/experiment/matrix.py)。
- baseline_minimal 公共案例已经通过批处理入口执行，结果位于 [results/fair_comparison_matrix/scale_extension/baseline_minimal/crux](results/fair_comparison_matrix/scale_extension/baseline_minimal/crux) 和 [results/fair_comparison_matrix/scale_extension/baseline_minimal/teccl](results/fair_comparison_matrix/scale_extension/baseline_minimal/teccl)。
- baseline_minimal 的 CRUX/TE-CCL 对比图已经生成，位于 [results/visualizations/baseline_minimal](results/visualizations/baseline_minimal)。
- stage-12 生成的交接归因报告位于 [results/project_handoff/project_handoff_report.json](results/project_handoff/project_handoff_report.json) 和 [results/project_handoff/project_handoff_report.md](results/project_handoff/project_handoff_report.md)。

## 关键设计决策

- 拓扑、工作负载和实验参数都必须通过文件输入；公共环境参数不能在调度器内部被隐式修改。
- CRUX 维持 job-level 的优先级与路径决策，不模拟 TE-CCL 式 GPU 复制语义。
- TE-CCL 保持 chunk/epoch 语义，GPU 可复制且可持久保留 buffer，交换机只做瞬时转发且不承担长期 buffer。
- 所有实验结果统一通过 ExperimentRunner.export_results 导出为 summary、link_load_trace、flow_trace、schedule_history 和 scheduler_debug 五类产物。
- 公平矩阵要求 CRUX 与 TE-CCL 共享 topology_file、workload_file、random_seed 和公共 simulation/metrics 设置，只有算法私有参数允许变化。

## 推荐读取顺序

1. 读取 [plan.md](plan.md) 了解整体架构和公平对比约束。
2. 读取 [progress.md](progress.md) 确认最近完成的阶段与验证记录。
3. 读取 [handoff.md](handoff.md) 了解当前可直接复用的结果资产和下一步建议。
4. 读取 [feature_list.json](feature_list.json) 确认是否还有未完成 feature。
5. 若要继续实验，读取 [configs/experiment/fair_comparison_matrix.yaml](configs/experiment/fair_comparison_matrix.yaml) 与 [results/project_handoff/project_handoff_report.md](results/project_handoff/project_handoff_report.md)。

## 直接可复用的入口

- 最小基线实验： [configs/experiment/minimal_crux_e2e.yaml](configs/experiment/minimal_crux_e2e.yaml) 与 [configs/experiment/minimal_teccl_e2e.yaml](configs/experiment/minimal_teccl_e2e.yaml)
- 公平矩阵配置： [configs/experiment/fair_comparison_matrix.yaml](configs/experiment/fair_comparison_matrix.yaml)
- 公平矩阵批处理入口： [scripts/run_fair_matrix.py](scripts/run_fair_matrix.py) 与 [simulator/experiment/batch.py](simulator/experiment/batch.py)
- 结果归因工具： [simulator/metrics/reporting.py](simulator/metrics/reporting.py)
- CRUX/TE-CCL 对比可视化： [scripts/visualize_crux_vs_teccl.py](scripts/visualize_crux_vs_teccl.py) 与 [simulator/metrics/visualization.py](simulator/metrics/visualization.py)
- 批量矩阵枚举入口： [simulator/experiment/matrix.py](simulator/experiment/matrix.py)

## 明确下一步

1. 继续执行至少一组 scale_extension 和一组 load_sensitivity 公共案例，把 fair_comparison_matrix 下的结果覆盖到非最小基线场景。
2. 对新增矩阵结果重复调用 reporting 模块，扩展项目交接报告和归因摘要。
3. 继续在绘图或 notebook 层消费 [results/visualizations/baseline_minimal](results/visualizations/baseline_minimal) 与后续对比图，产出论文图表。