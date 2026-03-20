\section{问题建模与算法设计}

\subsection{系统模型与输入}

我们将训练网络建模为有向图 $G = (V, E)$，其覆盖数据中心内部网络与跨数据中心互联。节点集合 $V$ 包含训练端点（主机）$H \subset V$ 与交换机 $S = V \setminus H$。每条有向链路 $e \in E$ 具有容量 $C_e > 0$，全双工链路建模为两条反向有向边。

定义域标签函数 $dc(v) \in \mathcal{D} = \{1, \dots, D\}$，将节点划分至不同数据中心，并将链路划分为
\[
E = E_{\text{intra}} \cup E_{\text{dci}}, \quad E_{\text{intra}} \cap E_{\text{dci}} = \emptyset.
\]

对于每个数据中心 $d \in \mathcal{D}$，记其子图为 $G_d = (V_d, E_d)$，边界网关集合为 $B_d \subseteq V_d$。

系统以时间片（slot）为单位运行，每个时间片长度为 $\Delta$。设 $\mathcal{J}$ 为并发训练作业集合。每个作业 $j \in \mathcal{J}$ 在时间片开始时提供一组主机间通信需求：
\[
\mathcal{F}_j = \{f\}, \quad f = (s_f, d_f, B_f),
\]
其中 $s_f, d_f \in H$ 且 $B_f$ 为剩余数据量。

定义全局需求集合：
\[
\mathcal{F} = \bigcup_{j \in \mathcal{J}} \mathcal{F}_j,
\]
并划分为跨域与域内需求：
\[
\mathcal{F}^{\text{cross}} = \{f \mid dc(s_f) \neq dc(d_f)\}, \quad
\mathcal{F}^{\text{intra}} = \mathcal{F} \setminus \mathcal{F}^{\text{cross}}.
\]

将流量归一化为速率需求：
\[
b_f = \frac{B_f}{\Delta}.
\]

对于每个跨域需求 $f$，选择出口与入口边界网关：
\[
\beta_f^{\text{out}} \in B_{dc(s_f)}, \quad \beta_f^{\text{in}} \in B_{dc(d_f)},
\]
并定义其经过的 DCI 链路集合为 $dci(f) \subseteq E_{\text{dci}}$。

传播时延定义为：
\[
L_f = \sum_{\ell \in dci(f)} L_\ell,
\]
队列等待时间为 $Q_f \ge 0$。

为降低复杂度，引入 ToR 聚合映射 $\tau: H \rightarrow T$，并构建 ToR 层需求：
\[
B_{a,b} =
\sum_{\substack{f \in \mathcal{F}^{\text{intra}}: \\ \tau(s_f)=a, \tau(d_f)=b}} B_f，其中，
a, b ∈ T_d.
\]

调度器输出：
\begin{itemize}
\item 跨域带宽分配结果
\item 域内多路径转发表（WCMP 权重）
\end{itemize}

---

\subsection{感知瓶颈的跨域带宽承诺}

\subsubsection{阶段 I-A：跨域速率分配}

为每个跨域需求 $f \in \mathcal{F}^{\text{cross}}$ 分配速率 $r_f \ge 0$。

满足 DCI 容量约束
\[dci(f ) ⊆ E_{\text{dci}}\]
\[
\sum_{f : \ell \in dci(f)} r_f \le C_\ell, \quad \forall \ell \in E_{\text{dci}}.
\]

优化目标为最小化作业级尾时延：
\[
\min \max_{j \in \mathcal{J}} T_j,
\]
其中
\[
T_j = \max_{f \in \mathcal{F}_j^{\text{cross}}}
\left( Q_f + L_f + \frac{B_f}{r_f} \right).
\]

where \[
{F}_j^{\text{cross}}= F_j ∩ F^{\text{cross}}
\] Here
\[
Q_f + L_f + B_f /r_f 
\]is a conservative completion proxy of transfer f under the committed rate.

采用二分搜索求解。对于候选 $\theta$：
\[
Q_f + L_f + \frac{B_f}{r_f} \le \theta
\Rightarrow
r_f \ge \hat{r}_f(\theta) =
\frac{B_f}{\max\{\theta - Q_f - L_f, \epsilon\}}.
\]

可行性条件：
\[
\sum_{f : \ell \in dci(f)} \hat{r}_f(\theta) \le C_\ell,∀l ∈ E_{\text{dci}}
\]

通过二分搜索得到最小可行 $\theta^\star$，并令：
\[
r_f = \hat{r}_f(\theta^\star).
\]

---

\subsubsection{阶段 I-B：域内实现与资源占用}

For a domain \[d ∈ D\]
, we first compute the ToR-level egress/ingress rates implied by \[r_f\] :定义 ToR 层入/出流量：
\[
out_d(a) =
\sum_{\substack{f \in \mathcal{F}^{\text{cross}} : \\ dc(s_f)=d, \tau(s_f)=a}} r_f,
\]
\[
in_d(a) =
\sum_{\substack{f \in \mathcal{F}^{\text{cross}}  : \\ dc(d_f)=d, \tau(d_f)=a}} r_f.
\]
\[a ∈ T_d.\]

Let
\[cap^{\text{}{out}} _b\]
and \[cap^{\text{}{in}} _b\]denote the effective egress/ingress capacities of border\[ b ∈ B_d\]. We compute nonnegative ToR–border splits  \[y^{{out}}  _{\text{a→b}}\] and  \[y^{{in}}  _{\text{b→a}}\]  such that，ToR 与边界网关之间流量划分：
\[
\sum_{b \in B_d} y^{out}_{a \to b} = out_d(a), \quad
\sum_{a \in T_d} y^{out}_{a \to b} \le cap^{out}_b,
\]
\[
\sum_{b \in B_d} y^{in}_{b \to a} = in_d(a), \quad
\sum_{a \in T_d} y^{in}_{b \to a} \le cap^{in}_b.
\]

采用乘法权重更新（MWU）进行路径分配。

定义链路保留容量：
\[
\tilde{C}_e = (1 - \eta) C_e.
\]

更新规则：
\[
\lambda_e^{(t+1)} =
\lambda_e^{(t)} \cdot
\exp\left(
\epsilon \cdot \frac{\Delta x_e^{(t)}}{\tilde{C}_e}
\right).e ∈ E_d,
\]

\subsection{基于剩余预算的域内最小完成时间规划}

Stage II schedules intra-domain demands on the residual intra-datacenter network after Stage I has committed and realized cross-domain traffic. We treat the footprint \[x^{\text{cross}}_e\] produced by Stage I as an explicit reservation. Since Stage I-B enforces per-link headroom and capacity-respecting updates, the reservation satisfies \[x^{\text{cross}} _e ≤ (1 − η)C_e\] for all \[e ∈ E_d\], leaving a well-defined residual budget. Accordingly, we define the residual capacity of domain d as
定义剩余容量：
\[
C_e^{\text{res}} = C_e - x_e^{\text{cross}}.∀e ∈ E_d.
\]

Stage II aggregates host-to-host intra-domain demands into a sparse ToR–ToR matrix ${B_{\text{a,b}}}$ over ToRs $a, b ∈ T_d$.
对于 ToR 对 $(a,b)$：
\[
R_{a,b}(T) = \frac{B_{a,b}}{T}.
\]
目标：最小化完成时间T，使每个域内ToR需求都能够在T时间内满足（为域间流量预留带宽后）。 最小化完工时间转化为一个可行性问题：确定速率矩阵R\textsubscript{a,b}(T)，是否能够在域内剩余容量下被路由。
利用单调性：如果 T 可行，那么任何更大的 T 也是可行的。
因此，我们应用二分搜索来寻找最小可行完工时间 T*。 
可行性条件：
\[
\sum_{c \in K_{a,b}} x_{a,b,c} = R_{a,b}(T),
\]

链路负载：
\[
load(e) =
\sum_{(a,b)} \sum_{c: e \in path(a,b,c)} x_{a,b,c}.
\]

容量约束：
\[
load(e) \le C_e^{\text{res}}.
\]

价格更新：
\[
\lambda_e \leftarrow
\left[
\lambda_e + \gamma (load(e) - C_e^{\text{res}})
\right]^+.
\]

最终 WCMP 权重：
\[
w_{a,b,c} = \frac{x_{a,b,c}}{R_{a,b}(T^\star)}.
\]

---

\subsection{算法：两阶段跨域多作业调度}

\begin{algorithm}[H]
\caption{两阶段跨域多作业调度}
\begin{algorithmic}[1]

\STATE 将 $\mathcal{F}$ 划分为 $\mathcal{F}^{\text{cross}}$ 和 $\mathcal{F}^{\text{intra}}$

\STATE \textbf{阶段 I-A：跨域带宽分配}
\STATE 初始化区间 $[\theta_{\min}, \theta_{\max}]$
\STATE 计算 $\hat{r}_f(\theta)$
\STATE 检查 DCI 可行性
\STATE 二分搜索得到 $\theta^\star$
\STATE $r_f \leftarrow \hat{r}_f(\theta^\star)$

\FOR{每个数据中心 $d$}

\STATE \textbf{阶段 I-B：域内实现}
\STATE 构建 ToR 流量
\STATE MWU 路由，得到 $x_e^{\text{cross}}$
\STATE 计算剩余容量 $C_e^{\text{res}}$
\STATE 聚合域内需求 $B_{a,b}$

\STATE \textbf{阶段 II：最小完成时间规划}
\STATE 对 $T$ 进行二分搜索
\STATE 求解 $x_{a,b,c}$
\IF{不可行}
    \STATE 扩展候选路径集合 $K_{a,b}$
\ENDIF

\STATE 计算权重：
\[
w_{a,b,c} = \frac{x_{a,b,c}}{R_{a,b}(T^\star)}
\]

\STATE 缓存状态

\ENDFOR

\STATE 返回 $\{r_f\}, \{w_{a,b,c}\}$

\end{algorithmic}
\end{algorithm}