"""planner 核心算法的单元测试。运行：python -m pytest -q"""

from __future__ import annotations

import random
from decimal import Decimal
from itertools import product

import pytest

from app.planner import PlanningError, plan_pages


def _ranges(plan):
    return [(p.start_measure, p.end_measure) for p in plan.pages]


def _d(value):
    """按十进制字面量精确转 Decimal（float 取其最短十进制表示）。"""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def brute_force_best(heights, silences, page_height, min_turn_time):
    """枚举所有断页位置组合，按目标字典序排序取最优；无方案返回 None。"""
    n = len(heights)
    best = None

    for mask in product([0, 1], repeat=n - 1):
        breaks = [b + 1 for b, bit in enumerate(mask) if bit]
        bounds = [0] + breaks + [n]
        ok = True
        turn_values = []
        for idx in range(len(bounds) - 1):
            a, e = bounds[idx], bounds[idx + 1]
            if sum(heights[a:e]) > page_height + 1e-9:
                ok = False
                break
            if e < n and silences[e - 1] + 1e-9 < min_turn_time:
                ok = False
                break
            if e < n:
                turn_values.append(silences[e - 1])
        if not ok:
            continue
        key = (
            len(bounds) - 1,                            # 页数最少
            -min(turn_values) if turn_values else -float("inf"),  # 最短静默最大（无翻页点视为无穷大）
            breaks,                                      # 断页序号字典序最小
        )
        if best is None or key < best[0]:
            best = (key, breaks)
    return None if best is None else best[1]


def brute_force_best_decimal(heights, silences, page_height, min_turn_time):
    """与 ``brute_force_best`` 相同的枚举，但容量/静默比较走 Decimal 精确值，
    用于验证仅在第 10 位小数处有差异的边界用例。"""
    n = len(heights)
    dh = [_d(h) for h in heights]
    ds = [_d(s) for s in silences]
    dph = _d(page_height)
    dmt = _d(min_turn_time)
    best = None

    for mask in product([0, 1], repeat=n - 1):
        breaks = [b + 1 for b, bit in enumerate(mask) if bit]
        bounds = [0] + breaks + [n]
        ok = True
        turn_values = []
        for idx in range(len(bounds) - 1):
            a, e = bounds[idx], bounds[idx + 1]
            if sum(dh[a:e]) > dph:  # 恰好等于允许，略超即非法
                ok = False
                break
            if e < n:
                if ds[e - 1] < dmt:  # 恰好等于允许，略短即非法
                    ok = False
                    break
                turn_values.append(ds[e - 1])
        if not ok:
            continue
        key = (
            len(bounds) - 1,
            -min(turn_values) if turn_values else None,
            breaks,
        )
        if best is None or key < best[0]:
            best = (key, breaks)
    return None if best is None else best[1]


def test_single_page():
    plan = plan_pages(
        heights=[3, 3, 3],
        silences=[1, 1, 0],
        page_height=10,
        min_turn_time=1,
    )
    assert plan.page_count == 1
    assert plan.breaks == []
    assert plan.shortest_turn_silence is None
    assert _ranges(plan) == [(0, 2)]


def test_min_pages_only_feasible_turn_positions():
    # 容量 2 的 5 小节；小节 3 后静默 0.1 不达标，只能在位置 1、3 之后翻页。
    plan = plan_pages(
        heights=[1] * 5,
        silences=[10, 0.1, 1, 100, 0],
        page_height=2,
        min_turn_time=1,
    )
    assert plan.page_count == 3
    assert plan.breaks == [1, 3]
    assert plan.shortest_turn_silence == 1
    assert _ranges(plan) == [(0, 0), (1, 2), (3, 4)]
    assert [p.turn_silence for p in plan.pages] == [10, 1, None]


def test_maximin_prefers_larger_min_silence():
    # 容量 3 的 5 小节，2 页切法：b=1(min=1), b=2(min=5), b=3(min=9)
    plan = plan_pages(
        heights=[1] * 5,
        silences=[1, 5, 9, 1, 0],
        page_height=3,
        min_turn_time=1,
    )
    assert plan.page_count == 2
    assert plan.breaks == [3]
    assert plan.shortest_turn_silence == 9


def test_lexicographically_smallest_breaks_on_tie():
    # 两个 2 页方案最短静默相同，选断页序号字典序最小（更早翻页）。
    plan = plan_pages(
        heights=[1] * 4,
        silences=[5, 5, 5, 0],
        page_height=3,
        min_turn_time=5,
    )
    assert plan.page_count == 2
    assert plan.breaks == [1]
    assert plan.shortest_turn_silence == 5

    # 多断页并列：容量 2 的 7 小节，所有内部静默相同，共需 4 页且有 1 小节余量。
    # 可行断页序列 [1,3,5]/[2,3,5]/[2,4,5]/[2,4,6]，字典序最小为 [1,3,5]。
    plan2 = plan_pages(
        heights=[1] * 7,
        silences=[5] * 6 + [0],
        page_height=2,
        min_turn_time=5,
    )
    assert plan2.page_count == 4
    assert plan2.breaks == [1, 3, 5]
    assert plan2.shortest_turn_silence == 5


def test_measure_too_tall():
    with pytest.raises(PlanningError, match="超过页面可用高度"):
        plan_pages(
            heights=[5, 8],
            silences=[1, 0],
            page_height=6,
            min_turn_time=1,
        )


def test_no_valid_plan():
    # 两小节各高 5、容量 6 -> 必须分开，但小节后静默 0 不达标。
    with pytest.raises(PlanningError, match="无合法翻页方案"):
        plan_pages(
            heights=[5, 5],
            silences=[0, 0],
            page_height=6,
            min_turn_time=1,
        )


def test_float_thresholds():
    # 容量 2、4 小节：2 页方案唯一断页 b=2，翻页静默 = s[1] = 1.5。
    # b=1 的方案需 3 页（页数更多，不优）。
    plan = plan_pages(
        heights=[1.0] * 4,
        silences=[2.5, 1.5, 2.5, 0],
        page_height=2.0,
        min_turn_time=1.5,
    )
    assert plan.page_count == 2
    assert plan.shortest_turn_silence == 1.5
    assert plan.breaks == [2]


def test_exact_fill_page_boundary():
    # 页恰好装满：高度等于 page_height 必须允许（<= 判定）；两个方案等价时字典序优先
    plan = plan_pages(
        heights=[3, 3, 3],
        silences=[2, 2, 0],
        page_height=6,
        min_turn_time=2,
    )
    assert plan.page_count == 2
    assert plan.breaks == [1]
    assert _ranges(plan) == [(0, 0), (1, 2)]


def test_decimal_boundaries_exact_no_tolerance():
    """容量与静默按十进制精确值比较：略超/略短不合格，恰好等于合格。"""
    # 单小节高度哪怕只比页高高一点点，也必须按"超高无法排版"拒绝。
    with pytest.raises(PlanningError, match="超过页面可用高度"):
        plan_pages(heights=[1.0000000005], silences=[0], page_height=1, min_turn_time=0)

    # 三小节各高 1、页高 2：静默 1、1.0000000005、0 时，最优断页只能是 2
    # （断页 1 的静默仅 1，达不到可达的最短静默 1.0000000005）。
    plan = plan_pages(
        heights=[1, 1, 1],
        silences=[1, 1.0000000005, 0],
        page_height=2,
        min_turn_time=1,
    )
    assert plan.breaks == [2]
    assert plan.shortest_turn_silence == 1.0000000005
    assert _ranges(plan) == [(0, 1), (2, 2)]

    # 十进制下 0.1 + 0.2 恰好等于 0.3：两小节必须能同页（二进制浮点下
    # 0.1 + 0.2 > 0.3，精确十进制口径不得因此误拆页）。
    plan = plan_pages(
        heights=[0.1, 0.2], silences=[1, 0], page_height=0.3, min_turn_time=1
    )
    assert plan.page_count == 1
    assert plan.breaks == []
    assert plan.pages[0].used_height == 0.3

    # 反向边界：累计高度哪怕只略超页高也必须拆页。
    plan = plan_pages(
        heights=[0.1, 0.2000000001], silences=[1, 0],
        page_height=0.3, min_turn_time=1,
    )
    assert plan.page_count == 2
    assert plan.breaks == [1]

    # 静默略低于最低翻页时间（差 1e-10）不得作为翻页点：单页容不下时无方案。
    with pytest.raises(PlanningError, match="无合法翻页方案"):
        plan_pages(
            heights=[1, 1], silences=[0.9999999999, 0],
            page_height=1, min_turn_time=1,
        )

    # 多小节累计恰好等于页高（0.05+0.05+0.2=0.3）允许同页，且静默恰好等于
    # 最低翻页时间也算达标。
    plan = plan_pages(
        heights=[0.05, 0.05, 0.2], silences=[1, 1, 0],
        page_height=0.3, min_turn_time=1,
    )
    assert plan.page_count == 1
    assert plan.pages[0].used_height == 0.3


def test_decimal_maximin_excludes_barely_short_silence():
    # 两页方案 b=1（静默 5）或 b=2（静默 4.9999999999，略短于 5）：
    # 最短静默阈值搜索不得把略短的 4.9999999999 当成 5，最优阈值仍为 5、
    # 重构必须选 b=1（而非旧容差下可能误选的 b=2）。
    plan = plan_pages(
        heights=[1, 1, 1],
        silences=[5, 4.9999999999, 0],
        page_height=2,
        min_turn_time=4,
    )
    assert plan.breaks == [1]
    assert plan.shortest_turn_silence == 5.0

    # 必翻点静默略低于最低翻页时间：必翻点静默不足，直接无解。
    with pytest.raises(PlanningError, match="必翻位置 2"):
        plan_pages(
            heights=[1, 1, 1], silences=[1, 0.9999999999, 0],
            page_height=2, min_turn_time=1, required_breaks=[2],
        )


def test_large_scale_performance():
    # 10 万小节、页容 50：恰好 2000 页，所有翻页静默为 3 秒，1 秒级完成。
    n = 100_000
    plan = plan_pages([1] * n, [3.0] * n, 50, 2)
    assert plan.page_count == 2000
    assert plan.shortest_turn_silence == 3.0
    assert all(p.used_height <= 50 + 1e-9 for p in plan.pages)


@pytest.mark.parametrize("seed", range(200))
def test_matches_bruteforce(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 8)
    if seed % 2 == 0:
        heights = [rng.randint(1, 5) for _ in range(n)]
    else:  # 浮点高度
        heights = [rng.choice([0.5, 1.25, 1.5, 2.75, 3.0]) for _ in range(n)]
    silences = [rng.choice([0.0, 1.0, 5.0, 9.0]) for _ in range(n)]
    page_height = rng.choice([1, 2, 3, 5, 6, 10]) + (0.5 if seed % 2 else 0)
    min_turn_time = rng.choice([0.0, 1.0, 5.0])

    too_tall = any(h > page_height for h in heights)
    expected = brute_force_best(heights, silences, page_height, min_turn_time)

    if too_tall:
        with pytest.raises(PlanningError, match="超过页面可用高度"):
            plan_pages(heights, silences, page_height, min_turn_time)
        return

    if expected is None:
        with pytest.raises(PlanningError, match="无合法翻页方案"):
            plan_pages(heights, silences, page_height, min_turn_time)
    else:
        plan = plan_pages(heights, silences, page_height, min_turn_time)
        assert plan.breaks == expected, (
            f"mismatch h={heights} s={silences} H={page_height} t={min_turn_time}"
        )
        # 页高度均不超限
        for p in plan.pages:
            assert p.used_height <= page_height + 1e-9
        # 断页静默均达标
        for b in plan.breaks:
            assert silences[b - 1] + 1e-9 >= min_turn_time


@pytest.mark.parametrize("seed", range(60))
def test_matches_bruteforce_decimal_adjacent(seed):
    """高度/静默围绕整数容量与阈值在第 9~11 位小数附近抖动：验证全程十进制
    精确比较（无 epsilon 容差），与 Decimal 口径的暴力枚举一致。"""
    rng = random.Random(seed + 42_000)
    n = rng.randint(1, 7)
    base_h = [rng.randint(1, 3) for _ in range(n)]
    jitter = rng.choice([-1, 0, 1])
    heights = [
        float(Decimal(h) + Decimal(jitter) * Decimal(1) / Decimal(10**rng.choice([9, 10, 11])))
        if h < 3 else float(h)
        for h in base_h
    ]
    page_height = rng.choice([2, 3, 4, 5])
    silences = [
        rng.choice([0.0, 0.9999999999, 1.0, 1.0000000001, 5.0, 9.0])
        for _ in range(n)
    ]
    silences[-1] = 0.0
    min_turn_time = rng.choice([0.0, 1.0])

    if any(_d(h) > _d(page_height) for h in heights):
        with pytest.raises(PlanningError, match="超过页面可用高度"):
            plan_pages(heights, silences, page_height, min_turn_time)
        return

    expected = brute_force_best_decimal(
        heights, silences, page_height, min_turn_time
    )
    if expected is None:
        with pytest.raises(PlanningError, match="无合法翻页方案"):
            plan_pages(heights, silences, page_height, min_turn_time)
    else:
        plan = plan_pages(heights, silences, page_height, min_turn_time)
        assert plan.breaks == expected, (
            f"decimal mismatch h={heights} s={silences} H={page_height} "
            f"t={min_turn_time}"
        )
        # 输出页高按十进制精确口径均不超限
        for p in plan.pages:
            assert _d(p.used_height) <= _d(page_height)
        for b in plan.breaks:
            assert _d(silences[b - 1]) >= _d(min_turn_time)
