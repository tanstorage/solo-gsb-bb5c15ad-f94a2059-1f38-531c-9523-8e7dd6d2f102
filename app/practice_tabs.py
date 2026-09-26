"""纸质谱练习页签规划核心。

背景
====
一份已保存的翻页方案把乐谱分成若干页（页码 1..page_count，每页对应一个
连续小节区间）。纸质练习时，希望在谱本上贴 K 个**页签**作索引，只针对常练
小节：每个常练小节给出一个正整数"查找权重"（越常练权重越大）。若多个常练
小节落在同一页，按方案把权重**合并**到该页；没有练习目标的页不贴页签。

对每个目标页，练习时从离它**最近**的页签开始翻找，代价为

    页码距离 × 该页权重 ＝ |目标页 − 最近页签页| × 该页权重。

距离相同（页签恰在两侧等距）时归**较小页码**的页签。目标是在**恰好 K 个
不同页签**（1 ≤ K ≤ 目标页数）下全局最小化总代价；总代价并列时，取
**升序页签列表字典序最小**的方案。

这正是直线上的加权 K-中位数（facility location）问题：在直线的 m 个目标点
中选 K 个作为设施，每个点由最近设施服务。直线上存在最优解，其各设施的
服务范围为连续区间（有序分割），设施取所在区间的**下加权中位数**（区间
累计权重首次达到 ceil(W/2) 的点；两侧恰好等重时取较小页码，与"距离并列
取较小页码"口径一致）。据此做有序分割 DP：

    dp[k][i] = 把前 i 个目标页分成 k 个连续区间的最小代价，
    dp[k][i] = min_{k-1 ≤ j < i} dp[k-1][j] + g[j][i]，

其中 g[j][i] 为区间 [j, i) 以其下加权中位数为页签时的代价。所有区间的
g 与中位数下标一次性 O(m²) 预计算（固定左端点向右扩展时中位数下标单调
右移、总计移动 m 次）；g 满足 Monge（凹四边形不等式），每层 DP 用分治
优化，整体 O(m² + K·m·log m)（m ≤ MAX_TARGET_PAGES）。

字典序最小页签
==============
代价 DP 只取最小代价。另求一份后缀代价 DP 作为"剩余部分最优代价"，再从
左到右贪心重构：第一段分割 r 从小到大考察，只在保持全局最优代价的分割中，
取第一段中位数页码最小者（中位数随 r 单调右移，一旦右移即可停止），同
中位数下沿记忆化链表递归比较剩余升序列表，得到全局代价最优且升序页签
列表字典序最小的方案。

本模块仅依赖标准库，便于脱离 Web 层直接单测。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

#: 合并去重后目标页数的上限（与纸质谱页序核对的页数上限一致）
MAX_TARGET_PAGES = 500

#: 不可达状态的代价上界
INF = 10**30


class PracticeTabError(ValueError):
    """练习页签规划输入不合法：空目标、小节越界/重复、K 越界或目标过多等。"""


@dataclass(frozen=True)
class PracticeTarget:
    """一个目标页的最近页签归属。"""

    page: int  # 目标页码（1 基）
    weight: int  # 该页合并后的查找权重
    nearest_tab: int  # 最近页签页码（1 基；距离并列取较小页码）
    distance: int  # 到最近页签的页码距离（非负）


@dataclass(frozen=True)
class PracticeTabPlan:
    """练习页签规划结果。"""

    tab_pages: List[int]  # 升序的 K 个页签页码（1 基）
    tab_count: int  # 页签数 K
    target_page_count: int  # 合并去重后的目标页数
    total_cost: int  # 全局最小总代价
    targets: List[PracticeTarget]  # 每个目标页的最近页签归属（按页码升序）


def _validate(
    page_starts: Sequence[int],
    measure_count: int,
    entries: Sequence[Tuple[int, int]],
    tab_count: int,
) -> List[Tuple[int, int]]:
    """校验输入并把常练小节合并为 (页码, 权重) 升序列表。

    :param page_starts: 每页起始小节（0 基）的升序列表，页码即下标 +1
    :param measure_count: 规划小节总数
    :param entries: 常练条目 (小节序号, 权重)，小节序号 0 基
    :param tab_count: 页签数 K
    :raises PracticeTabError: 任一约束不满足
    """
    if not isinstance(tab_count, int) or isinstance(tab_count, bool):
        raise PracticeTabError(f"页签数必须为整数（收到 {tab_count!r}）")
    if not entries:
        raise PracticeTabError("practice_measures 不能为空：至少需要一个常练小节")

    # 小节序号 -> 页码：page_starts 升序，bisect_left(page_starts, m+1) 恰为
    # 起始小节 ≤ m 的页数，也就是该小节所在页的 1 基页码。
    merged: Dict[int, int] = {}
    seen: set[int] = set()
    for idx, (measure, weight) in enumerate(entries):
        if not isinstance(measure, int) or isinstance(measure, bool):
            raise PracticeTabError(
                f"第 {idx + 1} 个常练小节序号必须为整数（收到 {measure!r}）"
            )
        if measure < 0 or measure >= measure_count:
            raise PracticeTabError(
                f"第 {idx + 1} 个常练小节序号 {measure} 越界："
                f"合法范围为 0..{measure_count - 1}"
            )
        if measure in seen:
            raise PracticeTabError(
                f"常练小节序号 {measure} 重复，每个小节只能给出一次权重"
            )
        seen.add(measure)
        if not isinstance(weight, int) or isinstance(weight, bool):
            raise PracticeTabError(
                f"第 {idx + 1} 个查找权重必须为正整数（收到 {weight!r}）"
            )
        if weight < 1:
            raise PracticeTabError(
                f"第 {idx + 1} 个查找权重必须为正整数（实际 {weight}）"
            )
        page = bisect_left(page_starts, measure + 1)
        merged[page] = merged.get(page, 0) + weight

    targets = sorted(merged.items())
    if len(targets) > MAX_TARGET_PAGES:
        raise PracticeTabError(
            f"目标页数（{len(targets)}）超过上限 {MAX_TARGET_PAGES}，无法规划页签"
        )

    if tab_count < 1 or tab_count > len(targets):
        raise PracticeTabError(
            f"页签数 K（{tab_count}）必须在 1 到目标页数（{len(targets)}）之间"
        )
    return targets


def _prefix_sums(
    positions: Sequence[int], weights: Sequence[int]
) -> Tuple[List[int], List[int]]:
    """前缀权重和 psum 与带位置前缀和 qpos：

    psum[t] = Σ_{u<t} weights[u]；qpos[t] = Σ_{u<t} weights[u]·positions[u]。
    """
    psum = [0]
    qpos = [0]
    for p, w in zip(positions, weights):
        psum.append(psum[-1] + w)
        qpos.append(qpos[-1] + w * p)
    return psum, qpos


def _build_block_tables(
    positions: Sequence[int],
    weights: Sequence[int],
    psum: Sequence[int],
    qpos: Sequence[int],
) -> Tuple[List[List[int]], List[List[int]]]:
    """预计算全部区间的代价表 g 与下中位数下标表 med。

    g[j][i] 为目标下标区间 [j, i) 以其**下加权中位数**为页签时的总查找
    代价；med[j][i] 为该中位数下标。固定 j 向右扩展 i 时，中位数下标单调
    右移、总计移动 m 次，故整张表 O(m²) 构建。
    """
    m = len(positions)
    g: List[List[int]] = [[0] * (m + 1) for _ in range(m + 1)]
    med: List[List[int]] = [[0] * (m + 1) for _ in range(m + 1)]
    for j in range(m):
        t = j  # 随 i 右移的下中位数下标
        for i in range(j + 1, m + 1):
            # 区间累计权重首次达到 ceil(W/2)：psum[t+1]-psum[j] >= need。
            need = psum[j] + (psum[i] - psum[j] + 1) // 2
            while psum[t + 1] < need:
                t += 1
            med[j][i] = t
            pt = positions[t]
            left = (psum[t] - psum[j]) * pt - (qpos[t] - qpos[j])
            right = qpos[i] - qpos[t + 1] - (psum[i] - psum[t + 1]) * pt
            g[j][i] = left + right
    return g, med


def _divide_conquer_layer(
    prev: Sequence[int], k: int, m: int, g: Sequence[Sequence[int]]
) -> List[int]:
    """分治优化计算一层：layer[i] = min_{k-1 ≤ j < i} prev[j] + g[j][i]。

    仅 i ∈ [k, m] 可达（i 个点用 k 个设施须 i ≥ k）；同代价取最小 j，
    保持最优分割点随 i 单调，分治才成立。
    """
    layer = [INF] * (m + 1)

    def solve(lo: int, hi: int, opt_lo: int, opt_hi: int) -> None:
        if lo > hi:
            return
        i = (lo + hi) // 2
        best = INF
        best_j = opt_lo
        for j in range(opt_lo, min(i - 1, opt_hi) + 1):
            pj = prev[j]
            if pj >= INF:
                continue  # 不可达状态不参与转移
            val = pj + g[j][i]
            if val < best:
                best = val
                best_j = j
        layer[i] = best
        solve(lo, i - 1, opt_lo, best_j)
        solve(i + 1, hi, best_j, opt_hi)

    solve(k, m, k - 1, m - 1)
    return layer


def _forward_cost_layers(
    m: int, g: Sequence[Sequence[int]], k_limit: int
) -> List[List[int]]:
    """正向代价表 dp[k][i]：前 i 个目标页恰用 k 个页签的最小代价。"""
    layers: List[List[int]] = [[INF] * (m + 1) for _ in range(k_limit + 1)]
    for i in range(1, m + 1):
        layers[1][i] = g[0][i]
    for k in range(2, k_limit + 1):
        layers[k] = _divide_conquer_layer(layers[k - 1], k, m, g)
    return layers


def _suffix_cost_layers(
    m: int, g: Sequence[Sequence[int]], k_limit: int
) -> List[List[int]]:
    """后缀代价表 sdp[t][s]：目标下标 s..m-1 恰用 t 个页签的最小代价。

    递推 ``sdp[t][s] = min_{s<r} g[s][r] + sdp[t-1][r]``。对固定左割点 s，
    最优右割点随 s 右移而单调右移，故同样分治优化：把状态 s 按区间二分，
    在合法 r 范围 [s+1, m-(t-1)] 内取最小值。
    """
    layers: List[List[int]] = [[INF] * (m + 1) for _ in range(k_limit + 1)]
    for s in range(0, m):
        layers[1][s] = g[s][m]
    layers[1][m] = 0  # 空后缀（防御性，不参与转移）

    for t in range(2, k_limit + 1):
        layer = [INF] * (m + 1)
        prev = layers[t - 1]
        r_upper = m - (t - 1)  # 给后续 t-1 段至少留 t-1 个点

        def solve(lo: int, hi: int, opt_lo: int, opt_hi: int) -> None:
            if lo > hi:
                return
            s = (lo + hi) // 2
            best = INF
            best_r = opt_lo
            for r in range(max(s + 1, opt_lo), min(r_upper, opt_hi) + 1):
                pr = prev[r]
                if pr >= INF:
                    continue
                val = g[s][r] + pr
                if val < best:
                    best = val
                    best_r = r
            layer[s] = best
            # s 越小最优 r 越小：左侧状态的最优 r 不大于 best_r。
            solve(lo, s - 1, opt_lo, best_r)
            solve(s + 1, hi, best_r, opt_hi)

        solve(0, m - t, 1, m - 1)
        layers[t] = layer
    return layers


def plan_practice_tabs(
    page_starts: Sequence[int],
    measure_count: int,
    entries: Sequence[Tuple[int, int]],
    tab_count: int,
) -> PracticeTabPlan:
    """为已保存翻页方案规划纸质谱练习页签。

    :param page_starts: 每页起始小节（0 基）的升序列表，长度即规划页数，
        页码为其下标 +1
    :param measure_count: 规划小节总数（小节序号范围 0..measure_count-1）
    :param entries: 常练条目 (小节序号, 查找权重)：小节序号 0 基、互不
        重复，权重为正整数；同页多条的权重合并
    :param tab_count: 页签数 K，满足 1 ≤ K ≤ 合并后的目标页数
    :return: 升序页签页码、最小总代价与每个目标页的最近页签
    :raises PracticeTabError: 空目标、小节序号越界/重复、权重非正整数、
        K 越界或目标页数超过 MAX_TARGET_PAGES
    """
    targets = _validate(page_starts, measure_count, entries, tab_count)
    m = len(targets)
    k = tab_count
    positions = [p for p, _ in targets]
    weights = [w for _, w in targets]

    psum, qpos = _prefix_sums(positions, weights)
    g, med = _build_block_tables(positions, weights, psum, qpos)

    # ---- 正向最优代价总表 ----
    fwd = _forward_cost_layers(m, g, k)
    total_cost = fwd[k][m]

    # ---- 后缀最优代价 sdp[t][s]：下标 s..m-1 恰用 t 个页签的最小代价 ----
    sdp = _suffix_cost_layers(m, g, k)

    # ---- 字典序最小升序页签重构（记忆化，链表式存储避免元组复制） ----
    # choice[(s, t)] = (本段中位数页码, 本段结束下标 r)，表示目标 s..m-1
    # 恰用 t 个页签、保持最优代价时的字典序最小方案的第一段。
    choice: Dict[Tuple[int, int], Tuple[int, int]] = {}

    def build(s: int, t: int) -> Tuple[int, int]:
        key = (s, t)
        cached = choice.get(key)
        if cached is not None:
            return cached
        if t == 1:
            result = (positions[med[s][m]], m)
            choice[key] = result
            return result

        best: Optional[Tuple[int, int]] = None  # (本段中位数页码, 选中的 r)
        best_med: Optional[int] = None
        # r 上界：须给剩余 t-1 个页签留下至少 t-1 个点，r ≤ m-t+1。
        for r in range(s + 1, m - t + 2):
            if g[s][r] + sdp[t - 1][r] != sdp[t][s]:
                continue  # 该分割不能保持全局最优代价
            med_page = positions[med[s][r]]
            if best_med is None:
                best_med = med_page
                best = (med_page, r)
            elif med_page == best_med:
                # 首元素相同：比较剩余升序列表（沿记忆化链表字典序走）。
                assert best is not None
                if _chain_less(r, t - 1, best[1], t - 1):
                    best = (med_page, r)
            else:
                # med(s,r) 随 r 单调右移：一旦大于已知最小中位数，后续均更大。
                break
        assert best is not None, "内部错误：页签方案重构失败"
        choice[key] = best
        return best

    def _chain_less(sa: int, ta: int, sb: int, tb: int) -> bool:
        """比较两个等长重构方案的升序页签列表：A=(sa,ta) 是否字典序小于
        B=(sb,tb)。沿链表逐段比较中位数页码，完全相同返回 False。"""
        while ta > 0:
            ma, ra = build(sa, ta)
            mb, rb = build(sb, tb)
            if ma != mb:
                return ma < mb
            sa, ta = ra, ta - 1
            sb, tb = rb, tb - 1
        return False

    # 顺链表取出最终升序页签页码。
    tab_pages: List[int] = []
    s, t = 0, k
    while t > 0:
        med_page, r = build(s, t)
        tab_pages.append(med_page)
        s, t = r, t - 1

    # ---- 每个目标页的最近页签：双指针随页码右移；严格更近才右移，
    # 等距时保留左侧（较小页码）页签。 ----
    targets_out: List[PracticeTarget] = []
    ti = 0
    for p, w in targets:
        while ti + 1 < len(tab_pages) and abs(tab_pages[ti + 1] - p) < abs(
            tab_pages[ti] - p
        ):
            ti += 1
        nearest = tab_pages[ti]
        targets_out.append(
            PracticeTarget(
                page=p, weight=w, nearest_tab=nearest, distance=abs(nearest - p)
            )
        )

    # 防御：按"每目标页归最近页签"重算的总代价必须等于 DP 最优值。
    recomputed = sum(tg.weight * tg.distance for tg in targets_out)
    assert recomputed == total_cost, "内部错误：重构代价与最优代价不一致"

    return PracticeTabPlan(
        tab_pages=tab_pages,
        tab_count=k,
        target_page_count=m,
        total_cost=total_cost,
        targets=targets_out,
    )
