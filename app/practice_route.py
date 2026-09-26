"""已保存方案的练习路线规划核心。

背景
====
一份已保存的翻页方案把乐谱分成若干页（页码 1..page_count，每页对应一个
连续小节区间）。练习时给定 1~15 个**互异的常练小节**、一个**起始页码**，
以及若干"先练 a 再练 b"的**先后依赖**（以小节序号二元组给出，两端都必须
是常练小节）。按方案把每个常练小节映射到所在页后，要排出一个满足全部
依赖的练习顺序，使翻页总距离最小：

    总距离 = |起始页 − 首项所在页| + Σ |相邻两项所在页之差|

总距离并列时，取**小节序号序列字典序最小**的顺序（先比第一项，再依次比
后续各项）。

求解
====
常练小节至多 15 个，用子集动态规划（Held-Karp 式后缀 DP）：把小节按序号
升序编号为 0..k-1，``dp[visited][last]`` 表示已练集合为 ``visited``、最后
练的是 ``last``（``last ∈ visited``）时，练完剩余小节所需的最小距离。
转移时只允许加入**所有直接前驱都已练过**的小节，依赖因此天然满足；状态
数不超过 k·2^(k-1)（k ≤ 15 时约 24 万），按掩码从大到小一趟递推。

先后依赖成环时不存在任何满足全部依赖的顺序：先用 Kahn 拓扑排序显式判环
（自环即长度为 1 的环），给出明确错误，再进入 DP。

字典序最小重构
==============
后缀 DP 给出全局最优总距离后，从左到右贪心重构：每个位置按小节序号升序
试探候选小节，取第一个满足"已花距离 + 本段距离 + 后缀最优距离 == 全局
最优"者。字典序由前缀决定，逐位取最小可行值即得同成本下字典序最小的
顺序。

本模块仅依赖标准库，便于脱离 Web 层直接单测。
"""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import dataclass
from typing import List, Sequence, Tuple

#: 常练小节数上限（子集 DP 状态数随之指数增长，15 时约 24 万状态）
MAX_PRACTICE_MEASURES = 15

#: 不可达状态的距离上界
INF = 10**15


class PracticeRouteError(ValueError):
    """练习路线规划输入不合法：小节/页码越界、重复小节、依赖指向非目标
    小节或先后依赖成环等。"""


@dataclass(frozen=True)
class PracticeRoute:
    """练习路线规划结果。"""

    order: List[int]  # 练习顺序（原谱 0 基小节序号）
    order_pages: List[int]  # 顺序中各小节所在页码（1 基）
    segment_distances: List[int]  # 各段翻页距离：起始页→首项，及相邻项之间
    total_distance: int  # 总翻页距离（各段距离之和）


def _validate(
    page_starts: Sequence[int],
    measure_count: int,
    measures: Sequence[int],
    start_page: int,
    dependencies: Sequence[Tuple[int, int]],
) -> Tuple[List[int], List[int], List[int]]:
    """校验输入，返回 (升序小节列表, 各小节所在页码, 各小节前驱位掩码)。

    :param page_starts: 每页起始小节（0 基）的升序列表，页码即下标 +1
    :param measure_count: 规划小节总数
    :param measures: 常练小节序号（0 基），1~15 个、互异
    :param start_page: 起始页码（1 基）
    :param dependencies: 先后依赖二元组 (先练, 后练)，两端都必须是常练小节
    :raises PracticeRouteError: 任一约束不满足
    """
    page_count = len(page_starts)
    if not isinstance(start_page, int) or isinstance(start_page, bool):
        raise PracticeRouteError(f"起始页码必须为整数（收到 {start_page!r}）")
    if start_page < 1 or start_page > page_count:
        raise PracticeRouteError(
            f"起始页码 {start_page} 越界：合法范围为 1..{page_count}"
        )
    if not measures:
        raise PracticeRouteError("practice_measures 不能为空：至少需要一个常练小节")
    if len(measures) > MAX_PRACTICE_MEASURES:
        raise PracticeRouteError(
            f"常练小节数（{len(measures)}）超过上限 {MAX_PRACTICE_MEASURES}"
        )

    seen: set[int] = set()
    for idx, m in enumerate(measures):
        if not isinstance(m, int) or isinstance(m, bool):
            raise PracticeRouteError(
                f"第 {idx + 1} 个常练小节序号必须为整数（收到 {m!r}）"
            )
        if m < 0 or m >= measure_count:
            raise PracticeRouteError(
                f"第 {idx + 1} 个常练小节序号 {m} 越界："
                f"合法范围为 0..{measure_count - 1}"
            )
        if m in seen:
            raise PracticeRouteError(
                f"常练小节序号 {m} 重复，每个小节只能出现一次"
            )
        seen.add(m)

    deps: List[Tuple[int, int]] = []
    for idx, pair in enumerate(dependencies):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise PracticeRouteError(
                f"第 {idx + 1} 个依赖必须是小节序号二元组（收到 {pair!r}）"
            )
        a, b = pair
        for endpoint in (a, b):
            if not isinstance(endpoint, int) or isinstance(endpoint, bool):
                raise PracticeRouteError(
                    f"第 {idx + 1} 个依赖的小节序号必须为整数（收到 {endpoint!r}）"
                )
            if endpoint not in seen:
                raise PracticeRouteError(
                    f"第 {idx + 1} 个依赖（{a} → {b}）指向非目标小节 {endpoint}："
                    "依赖两端都必须是常练小节"
                )
        deps.append((a, b))

    # 小节序号 -> 页码：page_starts 升序，bisect_left(page_starts, m+1) 恰为
    # 起始小节 ≤ m 的页数，也就是该小节所在页的 1 基页码。
    items = sorted(seen)
    index = {m: i for i, m in enumerate(items)}
    pages = [bisect_left(page_starts, m + 1) for m in items]

    # pre[j]：必须先于第 j 个小节（升序编号）练完的小节位掩码（直接前驱）。
    pre = [0] * len(items)
    for a, b in deps:
        pre[index[b]] |= 1 << index[a]
    return items, pages, pre


def _check_acyclic(pre: Sequence[int]) -> None:
    """Kahn 拓扑排序判环：先后依赖成环（含自环）时报错。

    :param pre: 各小节的直接前驱位掩码
    :raises PracticeRouteError: 存在环，不存在满足全部依赖的练习顺序
    """
    k = len(pre)
    successors: List[List[int]] = [[] for _ in range(k)]
    indegree = [0] * k
    for j in range(k):
        indegree[j] = pre[j].bit_count()
        for i in range(k):
            if pre[j] >> i & 1:
                successors[i].append(j)

    queue = deque(i for i in range(k) if indegree[i] == 0)
    removed = 0
    while queue:
        i = queue.popleft()
        removed += 1
        for j in successors[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                queue.append(j)
    if removed < k:
        raise PracticeRouteError(
            "先后依赖成环，不存在满足全部依赖的练习顺序"
        )


def plan_practice_route(
    page_starts: Sequence[int],
    measure_count: int,
    measures: Sequence[int],
    start_page: int,
    dependencies: Sequence[Tuple[int, int]] = (),
) -> PracticeRoute:
    """为已保存翻页方案规划练习路线。

    :param page_starts: 每页起始小节（0 基）的升序列表，长度即规划页数，
        页码为其下标 +1
    :param measure_count: 规划小节总数（小节序号范围 0..measure_count-1）
    :param measures: 常练小节序号（0 基），1~15 个、互异
    :param start_page: 起始页码（1 基），范围 1..规划页数
    :param dependencies: 先后依赖二元组 (先练, 后练)，两端都必须是常练
        小节；不得成环（含自环）
    :return: 满足全部依赖且总翻页距离最小的练习顺序（同成本取小节序号
        序列字典序最小）、各段翻页距离与总距离
    :raises PracticeRouteError: 小节/页码越界、重复小节、依赖指向非目标
        小节或依赖成环
    """
    items, pages, pre = _validate(
        page_starts, measure_count, measures, start_page, dependencies
    )
    _check_acyclic(pre)

    k = len(items)
    full = (1 << k) - 1

    # dist[a][b]：第 a 个小节（升序编号）所在页到第 b 个的页码距离。
    dist = [[abs(pages[b] - pages[a]) for b in range(k)] for a in range(k)]
    # start_dist[j]：起始页码到第 j 个小节所在页的距离（首段）。
    start_dist = [abs(pages[j] - start_page) for j in range(k)]
    bit_of = [1 << j for j in range(k)]

    # addable[visited]：已练集合为 visited 时下一步可练的小节（未练且直接
    # 前驱已全部练完）。只依赖 visited，与最后练的是谁无关，按掩码预计算。
    addable: List[List[int]] = [[] for _ in range(full + 1)]
    for visited in range(full + 1):
        remaining = full & ~visited
        while remaining:
            bit = remaining & -remaining
            remaining ^= bit
            j = bit.bit_length() - 1
            if not pre[j] & ~visited:
                addable[visited].append(j)

    # 后缀 DP：dp[visited * k + last] = 已练集合 visited、最后练的是 last
    # （last ∈ visited）时，练完剩余小节的最小距离。visited | bit 恒大于
    # visited，故按掩码从大到小递推即可；终态 visited == full 距离为 0，
    # 与列表初始值一致。状态数不超过 k·2^(k-1)（k ≤ 15 时约 24 万）。
    dp = [0] * ((full + 1) * k)
    for visited in range(full - 1, -1, -1):
        candidates = addable[visited]
        if not candidates:
            continue  # 无可练小节的非终态只在有环时出现（已提前判环）
        base = visited * k
        lasts = visited
        while lasts:
            bit = lasts & -lasts
            lasts ^= bit
            last = bit.bit_length() - 1
            dist_row = dist[last]
            best = INF
            for j in candidates:
                cost = dist_row[j] + dp[(visited | bit_of[j]) * k + j]
                if cost < best:
                    best = cost
            dp[base + last] = best

    # 全局最优总距离：首项必须无直接前驱，首段距离自起始页码起算。
    total = INF
    for j in range(k):
        if pre[j]:
            continue
        cost = start_dist[j] + dp[bit_of[j] * k + j]
        if cost < total:
            total = cost
    # 已判环，DAG 上必有拓扑序，total 必然有限。
    assert total < INF, "内部错误：无环依赖下未找到可行练习顺序"

    # ---- 字典序最小重构：items 按小节序号升序，逐位取最小可行候选 ----
    order_idx: List[int] = []
    visited = 0
    current_page = start_page
    spent = 0
    while visited != full:
        for j in range(k):
            bit = bit_of[j]
            if visited & bit or pre[j] & ~visited:
                continue
            seg = abs(pages[j] - current_page)
            if spent + seg + dp[(visited | bit) * k + j] == total:
                order_idx.append(j)
                visited |= bit
                current_page = pages[j]
                spent += seg
                break
        else:  # 理论上不可达：最优值由同一 DP 算出
            raise PracticeRouteError("内部错误：练习顺序重构失败")

    order = [items[j] for j in order_idx]
    order_pages = [pages[j] for j in order_idx]
    segment_distances: List[int] = []
    prev_page = start_page
    for page in order_pages:
        segment_distances.append(abs(page - prev_page))
        prev_page = page
    # 防御：分段距离之和必须等于 DP 最优总距离。
    assert sum(segment_distances) == total, "内部错误：分段距离与最优总距离不一致"

    return PracticeRoute(
        order=order,
        order_pages=order_pages,
        segment_distances=segment_distances,
        total_distance=total,
    )
