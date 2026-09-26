"""巡演装运的乐谱册装箱核心。

背景
====
巡演出发前要把若干**已保存方案**各自印制的**乐谱册**装进运输箱。每份方案
对应一册，册厚即该方案规划结果的**页数**（一页为一单位厚度）。装箱时：

- 每册只能**整本**放入一个箱，不可拆分；
- 每箱**至少一册**；
- 每箱各册页数之和（装载页数）不超过箱体页容量 ``capacity``。

输入 2~12 个互异的已保存方案（以方案 ID 标识）与一个正整数箱体页容量。

优化目标（按优先级）
====================
在所有合法装箱中依次比较：

1. **箱数最少**（少带箱子）；
2. 箱数相同时，**最重箱的装载页数最小**（各箱尽量均衡，便于搬运）；
3. 仍并列时，把**每个箱内的方案 ID 按字典序升序排列**，再把**箱列表按
   字典序排序**（先比各箱 ID 元组的第一个，再依次比后续；更短的前缀元组
   更小），取**字典序最小**的分箱，使结果确定、可复现。

求解
====
册数 n ≤ 12，用子集/回溯搜索即可：

1. 可装判定 ``_can_pack(weights, bins, cap)``：降序放置 + 剪枝的回溯，
   判定能否把全部册装入至多 ``bins`` 个容量 cap 的箱（每册整本）；
2. 最少箱数：从 ceil(总页数/容量) 下界起递增判定，首次可行即最少箱数；
3. 最重箱装载页数最小值：在 [最厚册页数, 容量] 上二分，判定式为
   "能装入最少箱数、每箱不超过候选页数"；
4. 字典序最小分箱：ID 升序后逐箱贪心——第一个箱必含最小 ID，枚举包含它
   的子集（ID 元组字典序从小到大），取第一个使"剩余册能恰分进剩余箱数、
   每箱非空且不超过最优最重页数"的子集；递归处理剩余册。逐位取最小可行
   箱，最终箱列表恰为规范（箱内 ID 升序、箱间按元组升序）形式，字典序最小。

本模块仅依赖标准库，便于脱离 Web 层直接单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

#: 一次装运的方案（乐谱册）数范围
MIN_BOOKS = 2
MAX_BOOKS = 12


class TourPackingError(ValueError):
    """乐谱册装箱输入不合法：方案数不在 2~12、ID 重复/非字符串、
    册厚非正整数、箱体容量非正整数或单册超容量等。"""


@dataclass(frozen=True)
class PackingBox:
    """一个运输箱的装载结果（箱内方案 ID 已按字典序升序）。"""

    box_index: int  # 箱序号（1 基，按箱列表字典序顺序）
    plan_ids: List[str]  # 箱内方案 ID（字典序升序）
    loaded_pages: int  # 装载页数（各册页数之和）
    remaining_capacity: int  # 剩余容量（箱体页容量 − 装载页数）


@dataclass(frozen=True)
class TourPacking:
    """乐谱册装箱结果。"""

    plan_ids: List[str]  # 参与装箱的方案 ID（字典序升序）
    book_pages: List[int]  # 与 plan_ids 一一对应的各册页数（册厚）
    capacity: int  # 箱体页容量
    box_count: int  # 最少箱数
    max_loaded_pages: int  # 最重箱的装载页数（同箱数下最小）
    total_pages: int  # 全部册的总页数
    boxes: List[PackingBox]  # 规范排序后的各箱


def _can_pack(weights: Sequence[int], bins: int, cap: int) -> bool:
    """判定能否把 weights 中各册整本装入至多 bins 个容量 cap 的箱。

    降序放置、同负载箱去重的回溯；空箱只试第一个（交换等价）。
    每个 weights 元素须 ≤ cap（由调用方保证）。
    """
    if bins <= 0:
        return False
    if len(weights) <= bins:
        # 册数不超过箱数时各自一箱即可（每册均不超容量）
        return True

    items = sorted(weights, reverse=True)
    loads = [0] * bins

    def place(pos: int) -> bool:
        if pos == len(items):
            return True
        w = items[pos]
        tried: set[int] = set()
        for b in range(bins):
            load = loads[b]
            if load in tried or load + w > cap:
                continue
            tried.add(load)
            loads[b] = load + w
            if place(pos + 1):
                return True
            loads[b] = load
            # 放入空箱仍失败：放入其他空箱与之对称，不必再试
            if load == 0:
                return False
        return False

    return place(0)


def _can_pack_subset(
    weights: Sequence[int],
    mask: int,
    bins: int,
    cap: int,
    subset_sum: Sequence[int],
) -> bool:
    """掩码子集 mask 中的册能否恰分进 bins 个非空、负载 ≤ cap 的箱。

    枚举第一个箱（必含掩码中最低位的最小 ID 册）的成员子集递归判定，
    在固定剩余箱数下记忆已证伪的掩码。
    """
    if bins == 0:
        return mask == 0
    if mask.bit_count() < bins:
        return False  # 每箱至少一册：册数不足
    if bins == 1:
        return subset_sum[mask] <= cap

    failed: set[Tuple[int, int]] = set()

    def split(rem: int, b: int) -> bool:
        if b == 1:
            return subset_sum[rem] <= cap
        if rem.bit_count() < b:
            return False
        key = (rem, b)
        if key in failed:
            return False
        first = rem & -rem
        others = rem ^ first
        sub = others
        while True:
            box = first | sub
            if subset_sum[box] <= cap and split(rem ^ box, b - 1):
                return True
            if sub == 0:
                break
            sub = (sub - 1) & others
        failed.add(key)
        return False

    return split(mask, bins)


def _minimum_bin_count(weights: Sequence[int], capacity: int) -> int:
    """最少箱数：从 ceil(总页数/容量) 下界起递增判定。"""
    total = sum(weights)
    lower = (total + capacity - 1) // capacity
    for bins in range(lower, len(weights) + 1):
        if _can_pack(weights, bins, capacity):
            return bins
    # 每册均 ≤ 容量时 n 个箱必然可行，不会走到这里
    raise AssertionError("unreachable: each book fits its own box")


def _minimum_max_load(
    weights: Sequence[int], capacity: int, bin_count: int
) -> int:
    """箱数固定为 bin_count 时，二分求最重箱装载页数的最小值。"""
    lo, hi = max(weights), capacity
    while lo < hi:
        mid = (lo + hi) // 2
        if _can_pack(weights, bin_count, mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def _lexicographic_partition(
    ids: Sequence[str], weights: Sequence[int], bin_count: int, cap: int
) -> List[Tuple[Tuple[str, ...], int]]:
    """构造规范形式下字典序最小的分箱。

    :param ids: 字典序升序的方案 ID
    :param weights: 与 ids 一一对应的册厚
    :param bin_count: 最少箱数
    :param cap: 每箱装载页数上界（最重箱最优值）
    :return: 各箱 (箱内 ID 升序元组, 装载页数)，箱列表按 ID 元组升序
    """
    n = len(ids)
    # 子集位掩码 -> 总页数
    subset_sum = [0] * (1 << n)
    for mask in range(1, 1 << n):
        low = mask & -mask
        idx = low.bit_length() - 1
        subset_sum[mask] = subset_sum[mask ^ low] + weights[idx]

    def build(
        avail: int, bins_left: int
    ) -> Optional[List[Tuple[Tuple[str, ...], int]]]:
        if bins_left == 0:
            return [] if avail == 0 else None
        # 规范分箱中第一个箱必含剩余最小 ID
        first = avail & -avail
        rest = avail ^ first
        # 枚举与 first 同箱的其余册，按箱内 ID 升序元组字典序试探。
        # n ≤ 12，含 first 的候选至多 2^(n-1) = 2048 个，直接构造排序。
        candidates: List[Tuple[int, Tuple[str, ...]]] = []
        sub = rest
        while True:
            box_mask = first | sub
            box_ids = tuple(
                ids[i] for i in range(n) if box_mask >> i & 1
            )
            candidates.append((box_mask, box_ids))
            if sub == 0:
                break
            sub = (sub - 1) & rest
        candidates.sort(key=lambda item: item[1])

        for box_mask, box_ids in candidates:
            remaining = avail ^ box_mask
            if subset_sum[box_mask] <= cap and _can_pack_subset(
                weights, remaining, bins_left - 1, cap, subset_sum
            ):
                tail = build(remaining, bins_left - 1)
                if tail is not None:
                    return [(box_ids, subset_sum[box_mask])] + tail
        return None

    result = build((1 << n) - 1, bin_count)
    assert result is not None, "最优参数下必存在合法分箱"
    return result


def _validate(
    books: Sequence[Tuple[str, int]], capacity: int
) -> Tuple[List[Tuple[str, int]], int]:
    """校验全部输入，返回 (按 ID 升序的 (ID, 页数) 列表, 容量)。

    :raises TourPackingError: 任一约束不满足
    """
    if not isinstance(capacity, int) or isinstance(capacity, bool):
        raise TourPackingError(f"箱体页容量必须为整数（收到 {capacity!r}）")
    if capacity < 1:
        raise TourPackingError(f"箱体页容量必须为正整数（实际 {capacity}）")

    if not isinstance(books, Sequence) or isinstance(books, (str, bytes)):
        raise TourPackingError("books 必须为 (方案 ID, 页数) 序列")
    if len(books) < MIN_BOOKS:
        raise TourPackingError(
            f"方案数（{len(books)}）少于下限 {MIN_BOOKS}："
            f"一次装运必须提交 {MIN_BOOKS}~{MAX_BOOKS} 个方案"
        )
    if len(books) > MAX_BOOKS:
        raise TourPackingError(
            f"方案数（{len(books)}）超过上限 {MAX_BOOKS}："
            f"一次装运只能提交 {MIN_BOOKS}~{MAX_BOOKS} 个方案"
        )

    seen: set[str] = set()
    normalized: List[Tuple[str, int]] = []
    for idx, item in enumerate(books):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise TourPackingError(
                f"第 {idx + 1} 个方案必须为 (ID, 页数) 二元组（收到 {item!r}）"
            )
        plan_id, pages = item
        if not isinstance(plan_id, str) or not plan_id:
            raise TourPackingError(
                f"第 {idx + 1} 个方案 ID 必须为非空字符串（收到 {plan_id!r}）"
            )
        if not isinstance(pages, int) or isinstance(pages, bool):
            raise TourPackingError(
                f"方案 {plan_id} 的页数必须为整数（收到 {pages!r}）"
            )
        if pages < 1:
            raise TourPackingError(
                f"方案 {plan_id} 的页数必须为正整数（实际 {pages}）"
            )
        if plan_id in seen:
            raise TourPackingError(f"方案 ID {plan_id} 重复，每个方案只能出现一次")
        seen.add(plan_id)
        normalized.append((plan_id, pages))

    normalized.sort(key=lambda item: item[0])
    for plan_id, pages in normalized:
        if pages > capacity:
            raise TourPackingError(
                f"方案 {plan_id} 的乐谱册共 {pages} 页，超过箱体页容量"
                f" {capacity} 页：每册只能整本放入一箱，无法装箱"
            )
    return normalized, capacity


def pack_tour_books(
    books: Sequence[Tuple[str, int]], capacity: int
) -> TourPacking:
    """为巡演装运把若干已保存方案的乐谱册装入运输箱。

    :param books: ``(方案 ID, 页数)`` 序列，2~12 个、ID 互异、页数为正；
        页数即该方案结果的页数，也是该册的厚度
    :param capacity: 箱体页容量（正整数），每箱装载页数不得超过该值
    :return: 按"箱数最少 → 最重箱装载页数最小 → 规范分箱字典序最小"
        三级优先级求得的装箱结果
    :raises TourPackingError: 方案数不在 2~12、ID 重复/非字符串、
        页数非正整数、容量非正整数或单册页数超过容量
    """
    normalized, capacity = _validate(books, capacity)
    ids = [plan_id for plan_id, _ in normalized]
    weights = [pages for _, pages in normalized]

    bin_count = _minimum_bin_count(weights, capacity)
    max_load = _minimum_max_load(weights, capacity, bin_count)
    partition = _lexicographic_partition(ids, weights, bin_count, max_load)

    boxes: List[PackingBox] = []
    for idx, (box_ids, load) in enumerate(partition, start=1):
        boxes.append(
            PackingBox(
                box_index=idx,
                plan_ids=list(box_ids),
                loaded_pages=load,
                remaining_capacity=capacity - load,
            )
        )

    return TourPacking(
        plan_ids=ids,
        book_pages=weights,
        capacity=capacity,
        box_count=bin_count,
        max_loaded_pages=max_load,
        total_pages=sum(weights),
        boxes=boxes,
    )
