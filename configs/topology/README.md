# Topology 配置说明

本目录存放网络拓扑输入。当前支持两种写法：

- generated：用少量参数生成拓扑。
- explicit：显式列出节点和链路。

## 基本结构

```yaml
meta:
  name: example_topology
  version: 1
  description: user editable topology input for the simulator

topology:
  mode: generated
  type: fat_tree
  parameters:
    k: 4
    hosts_per_tor: 2
    gpu_per_host: 4

nodes:
  host_count: 16
  switch_count: 20
  gpu_per_host: 4
  explicit_nodes: []

links:
  default_bandwidth_gbps: 100
  default_latency_us: 2
  bidirectional: true
  explicit_links: []
  overrides: []

routing:
  ecmp: true
  max_paths_per_pair: 8
  path_selection_mode: k_shortest

constraints:
  oversubscription_ratio: 1.0
  switch_buffer_mb: 32
  host_nic_bandwidth_gbps: 100
```

## 字段说明

### meta

- `name`：配置名，必填。
- `version`：版本号，通常填 `1`。
- `description`：用途说明，建议填写。

### topology

- `mode`：必填，只能是 `generated` 或 `explicit`。
- `type`：必填，拓扑类型名。生成式可以是 `fat_tree` 一类，自定义显式拓扑也应写清类型名。
- `parameters`：生成式参数字典。`generated` 模式下常把 `k`、`hosts_per_tor`、`gpu_per_host` 放在这里。

### nodes

- `host_count`：`generated` 模式必填，且必须大于 0。
- `switch_count`：`generated` 模式必填，且必须大于 0。
- `gpu_per_host`：每台 host 上 GPU 数。`generated` 模式必须大于 0。
- `explicit_nodes`：`explicit` 模式必填，必须非空。

`explicit_nodes` 每项至少包含：

- `node_id`：节点唯一标识，例如 `gpu_0`、`switch_0`。
- `node_type`：节点类型，当前实践中使用 `gpu`、`switch`，如后续扩展 host 级显式节点，也应保持命名一致。

### links

- `default_bandwidth_gbps`：默认链路带宽，必填，必须大于 0。
- `default_latency_us`：默认链路时延，单位微秒，必填，必须大于等于 0。
- `bidirectional`：是否按双向链路处理。
- `explicit_links`：`explicit` 模式必填，必须非空。
- `overrides`：对部分链路覆盖默认参数。

`explicit_links` 每项至少包含：

- `src`：起点节点 ID。
- `dst`：终点节点 ID。

常见可选附加字段：

- `bandwidth_gbps`：覆盖默认带宽。
- `latency_us`：覆盖默认时延。
- `bidirectional`：覆盖全局双向设置。

### routing

- `ecmp`：是否开启 ECMP 候选路径。
- `max_paths_per_pair`：每个端点对最多保留多少条候选路径。
- `path_selection_mode`：路径生成模式，当前模板使用 `k_shortest`。

### constraints

- `oversubscription_ratio`：可用于记录目标超卖比。
- `switch_buffer_mb`：交换机 buffer 容量。对 TECCL 当前实现来说，该字段属于物理约束背景信息，不等价于“允许交换机长期缓存副本”。
- `host_nic_bandwidth_gbps`：主机网卡带宽。

## generated 模式写法

适合 fat-tree 一类规则拓扑。最少应满足：

- `topology.mode: generated`
- `topology.type` 非空
- `nodes.host_count > 0`
- `nodes.switch_count > 0`
- `nodes.gpu_per_host > 0`

建议同时在 `topology.parameters` 中保留生成参数，便于审计。

## explicit 模式写法

适合最小复现、论文图示网络、非规则网络。最少应满足：

- `topology.mode: explicit`
- `nodes.explicit_nodes` 非空
- `links.explicit_links` 非空
- 每个节点含 `node_id`、`node_type`
- 每条链路含 `src`、`dst`

最小示例：

```yaml
topology:
  mode: explicit
  type: minimal_gpu_switch
  parameters: {}

nodes:
  host_count: 0
  switch_count: 1
  gpu_per_host: 0
  explicit_nodes:
    - node_id: gpu_0
      node_type: gpu
    - node_id: gpu_1
      node_type: gpu
    - node_id: switch_0
      node_type: switch

links:
  default_bandwidth_gbps: 100
  default_latency_us: 1000
  bidirectional: true
  explicit_links:
    - src: gpu_0
      dst: switch_0
    - src: gpu_1
      dst: switch_0
```

## 书写建议

- 节点 ID 在 topology 和 workload 之间必须一致，尤其是 GPU 名称。
- 如果实验要做 CRUX/TECCL 公平对比，不要在两个实验之间分别改拓扑文件。
- 对于 generated 模式，`nodes.gpu_per_host` 与 `topology.parameters.gpu_per_host` 应保持一致。
- `default_latency_us` 使用微秒，避免和 experiment 里的毫秒单位混淆。

## 常见错误

- `mode` 写成未支持的值。
- `explicit` 模式只写了节点，没写链路。
- workload 里引用了不存在的 GPU ID。
- 带宽和时延填成负数或 0。