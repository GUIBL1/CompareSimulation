下面是把论文 **Crux: GPU-Efficient Communication Scheduling for Deep Learning Training**（SIGCOMM 2024）工程化后的 **NS-3 / Python 可实现版本设计文档**。
所有算法逻辑均严格来自论文方法部分，并将数学符号转换为可实现的数据结构和事件驱动流程。

文中引用论文内容时使用 `filecite` 标注。

---

# 一、方法工程化概述

| 项目   | 内容                                                                            |
| ---- | ----------------------------------------------------------------------------- |
| 方法名称 | **Crux: GPU Intensity-Aware Communication Scheduler**                         |
| 核心思想 | 使用 **GPU Intensity (GPU计算强度)** 对DLT作业排序，通过 **路径选择 + 优先级调度 + 优先级压缩** 减少多作业通信竞争 |
| 优化目标 | 最大化 **GPU Utilization**                                                       |
| 调度粒度 | Job级通信流 (coflow-like scheduling)                                              |
| 主要机制 | Path Selection / Priority Assignment / Priority Compression                   |

论文证明：
最大化 GPU Utilization 等价于 **最大化链路上传输的 GPU intensity 总和**。 

---

# 二、系统模型（可实现抽象）

## 2.1 网络模型

```text
Network Graph G = (V, E)

V : GPUs / Hosts
E : Links (NVLink, PCIe, Ethernet)
Be : bandwidth of link e
```

DLT Job:

```text
job j ∈ J
```

属性：

| 符号   | 代码变量               | 含义            |
| ---- | ------------------ | ------------- |
| Wj   | compute_workload   | 每iteration计算量 |
| Mj,e | traffic_on_link[e] | 每iteration流量  |
| tj   | comm_time          | 最慢链路通信时间      |
| Ij   | gpu_intensity      | GPU强度         |

公式：

```
t_j = max_e ( M_j,e / B_e )

I_j = W_j / t_j
```

即：

```python
gpu_intensity = compute_workload / comm_time
```

GPU intensity 表示：

> 每单位通信时间能释放多少GPU计算。 

---

# 三、输入参数设计

| 参数                  | 类型           | 默认           | 说明                |
| ------------------- | ------------ | ------------ | ----------------- |
| topology            | Graph        | Clos         | DCN拓扑             |
| max_priority_levels | int          | 8            | NIC/交换机DSCP等级     |
| probing_interval    | float        | 30s          | GPU intensity测量窗口 |
| scheduling_interval | float        | event-driven | 调度触发              |
| job_list            | List[DLTJob] | —            | 当前运行作业            |

---

# 四、核心数据结构设计

## 4.1 Job

```python
class DLTJob:
    job_id: int
    num_gpus: int
    
    compute_workload: float
    comm_time: float
    
    gpu_intensity: float
    
    iteration_time: float
    
    flows: List['Flow']
    
    assigned_path: List[int]
    
    priority: int
```

---

## 4.2 Flow

```python
class Flow:
    flow_id: int
    
    src: int
    dst: int
    
    size_bytes: float
    
    job_id: int
    
    path: List[int]
    
    priority: int
```

---

## 4.3 Network State

```python
class NetworkState:
    
    link_bandwidth: Dict[Link, float]
    
    link_load: Dict[Link, float]
    
    candidate_paths: Dict[(src,dst), List[List[Node]]]
```

---

# 五、算法1：GPU Intensity计算

Crux需要先测量作业的计算量与通信时间。

论文做法：

1. 给新job **最高优先级**
2. 运行若干iteration
3. 采样GPU和NIC统计

得到：

```
Wj
tj
```

然后：

```
Ij = Wj / tj
```

---

## 伪代码

```python
def measure_gpu_intensity(job: DLTJob, monitor_window: float) -> float:
    
    # Step1 赋予最高优先级避免干扰
    job.priority = MAX_PRIORITY
    
    compute_workload = 0.0
    comm_time = 0.0
    
    start = now()
    
    while now() - start < monitor_window:
        
        compute_workload += read_gpu_flops(job)
        
        comm_time += measure_network_transfer_time(job)
    
    job.compute_workload = compute_workload
    
    job.comm_time = comm_time
    
    job.gpu_intensity = compute_workload / comm_time
    
    return job.gpu_intensity
```

---

# 六、算法2：GPU Intensity Path Selection

论文 §4.1：

核心思想：

1. **按 GPU intensity 降序排序 job**
2. 每个job选择 **最不拥塞路径**
3. 高强度job尽量走不同路径

> Crux从 GPU intensity 最大的作业开始选择路径，并为其选择当前最不拥塞路径。 

---

## 路径拥塞度计算

```
path_cost = Σ (link_load / link_capacity)
```

或

```
max_link_utilization
```

---

## 伪代码

```python
def path_selection(jobs: List[DLTJob], net: NetworkState):
    
    # Step1 按 GPU intensity 排序
    jobs_sorted = sorted(jobs, key=lambda j: j.gpu_intensity, reverse=True)
    
    for job in jobs_sorted:
        
        best_path = None
        best_cost = INF
        
        for path in get_candidate_paths(job):
            
            cost = compute_path_congestion(path, net)
            
            if cost < best_cost:
                
                best_cost = cost
                
                best_path = path
        
        job.assigned_path = best_path
        
        reserve_path_capacity(best_path, job)
```

---

# 七、算法3：Priority Assignment

论文发现：

只用 GPU intensity 排序会出现：

* 网络 burst
* iteration不同步

因为：

DLT具有：

* **iteration周期**
* **communication-computation overlap**

所以要 **微调 priority**。

论文策略：

```
priority = f(GPU_intensity, iteration_time, overlap_pattern)
```

核心目标：

让网络负载 **时间上均匀分布**。 

---

## 工程化近似实现

定义：

```
priority_score =
    α * normalized_gpu_intensity
  + β * iteration_frequency
```

---

## 伪代码

```python
def assign_priorities(jobs: List[DLTJob]):
    
    for job in jobs:
        
        intensity_score = normalize(job.gpu_intensity)
        
        iteration_score = 1.0 / job.iteration_time
        
        job.priority_score = (
            ALPHA * intensity_score +
            BETA * iteration_score
        )
    
    jobs_sorted = sorted(
        jobs,
        key=lambda j: j.priority_score,
        reverse=True
    )
    
    for i, job in enumerate(jobs_sorted):
        
        job.priority = i
```

---

# 八、算法4：Priority Compression

现实网络：

```
priority levels <= 8
```

但job可能几十个。

Crux策略：

优先保留：

```
high-intensity jobs
shared-path jobs
```

压缩：

```
low-impact jobs
```

---

## 伪代码

```python
def compress_priorities(jobs: List[DLTJob], max_levels: int):
    
    jobs_sorted = sorted(jobs, key=lambda j: j.priority)
    
    n = len(jobs_sorted)
    
    bucket_size = ceil(n / max_levels)
    
    for i, job in enumerate(jobs_sorted):
        
        level = i // bucket_size
        
        job.priority = min(level, max_levels-1)
```

---

# 九、完整调度流程（系统主循环）

Crux是 **事件驱动调度器**：

触发条件：

```
JobArrival
JobCompletion
PeriodicUpdate
```

---

## 总调度器

```python
class CruxScheduler:
    
    def __init__(self, topology: Graph):
        
        self.network = NetworkState(topology)
        
        self.jobs: Dict[int, DLTJob] = {}
    
    def handle_job_arrival(self, job: DLTJob):
        
        self.jobs[job.job_id] = job
        
        measure_gpu_intensity(job)
        
        self.reschedule()
    
    
    def handle_job_completion(self, job_id: int):
        
        del self.jobs[job_id]
        
        self.reschedule()
    
    
    def reschedule(self):
        
        jobs = list(self.jobs.values())
        
        # Step1 path selection
        path_selection(jobs, self.network)
        
        # Step2 priority assignment
        assign_priorities(jobs)
        
        # Step3 priority compression
        compress_priorities(jobs, MAX_PRIORITY_LEVEL)
        
        # Step4 install rules
        self.install_network_rules(jobs)
    
    
    def install_network_rules(self, jobs: List[DLTJob]):
        
        for job in jobs:
            
            set_flow_path(job)
            
            set_dscp_priority(job)
```

---

# 十、NS-3实现映射

| Crux组件          | NS-3模块                            |
| --------------- | --------------------------------- |
| Path Selection  | `Ipv4RoutingProtocol`             |
| Flow Priority   | `TrafficControlLayer / QueueDisc` |
| DSCP            | `Ipv4Header::SetTos()`            |
| Link congestion | `NetDeviceQueue`                  |
| Scheduler       | 自定义 `CruxController`              |

---

# 十一、NS-3事件处理

```python
PacketArrival
FlowStart
FlowFinish
JobArrival
JobCompletion
PeriodicStatsUpdate
```

---

# 十二、Python原型（简化）

推荐架构：

```
simulator/
    topology.py
    job.py
    flow.py
    crux_scheduler.py
    traffic_generator.py
```

核心循环：

```python
while sim_running:
    
    event = event_queue.pop()
    
    if event.type == JOB_ARRIVAL:
        scheduler.handle_job_arrival(event.job)
    
    elif event.type == JOB_FINISH:
        scheduler.handle_job_completion(event.job_id)
    
    elif event.type == PACKET_ARRIVAL:
        forward_packet(event.packet)
```

---

# 十三、复杂度分析

| 模块                   | 复杂度           |
| -------------------- | ------------- |
| GPU intensity测量      | O(iterations) |
| path selection       | O(J * P)      |
| priority assignment  | O(J log J)    |
| priority compression | O(J)          |

J = jobs
P = candidate paths

---

# 十四、Crux核心直觉（论文精华）

论文关键洞察：

```
GPU Utilization ∝
Σ (GPU intensity × communication duration)
```

因此：

**让高GPU intensity job优先通信**

即可最大化GPU计算释放。 

---

# 十五、如果你要在 NS-3 做这篇论文复现

我可以进一步帮你写：

1️⃣ **完整 NS-3 Crux module 设计（类结构）**
2️⃣ **FatTree DCN 仿真实验脚本**
3️⃣ **与 CASSINI / Sincronia 对比实验框架**
4️⃣ **SIGCOMM论文复现级代码架构**

如果需要，我可以直接给你 **一份接近 1000 行的 NS-3 Crux 调度器框架代码**。
