"""必翻/禁翻约束下 planner 核心算法的单元测试。运行：python -m pytest -q"""

from __future__ import annotations

import random
from itertools import product

import pytest

from app.planner import PlanningError, plan_pages


def brute_force_best_constrained(
    heights, silences, page_height, min_turn_time, required=(), forbidden=()
):
    """枚举所有断页位置组合（含必翻/禁翻约束），按全篇统一目标取最优。

    注意：枚举时目标在**全篇所有可行方案**中统一比较，不按必翻点分段，
    因此本函数也用于验证实现没有退化为分段局部最优。
    无方案返回 None。
    """
    n = len(heights)
    required, forbidden = set(required), set(forbidden)
    best = None

    for mask in product([0, 1], repeat=n - 1):
        breaks = frozenset(b + 1 for b, bit in enumerate(mask) if bit)
        if not required <= breaks or breaks & forbidden:
            continue
        break_list = sorted(breaks)
        bounds = [0] + break_list + [n]
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
            len(bounds) - 1,
            -min(turn_values) if turn_values else -float("inf"),
            break_list,
        )
        if best is None or key < best[0]:
            best = (key, break_list)
    return None if best is None else best[1]


def test_required_break_forces_page_boundary():
    # 容量 10 本可单页装下 [3,3,3]；必翻 2 强制两页。
    plan = plan_pages(
        [3, 3, 3], [1, 1, 0], 10, 1, required_breaks=[2]
    )
    assert plan.page_count == 2
    assert plan.breaks == [2]
    assert [(p.start_measure, p.end_measure) for p in plan.pages] == [(0, 1), (2, 2)]


def test_required_break_can_increase_page_count():
    # 容量 3 的 5 小节，无约束时 2 页 [3]（最短静默 9）；必翻 1 时全局最优为
    # 3 页 [1,2]（必翻点静默 1，全篇最短静默 1，无法再大）。
    plan = plan_pages(
        [1] * 5, [1, 5, 9, 1, 0], 3, 1, required_breaks=[1]
    )
    assert plan.page_count == 3
    assert plan.breaks == [1, 2]
    assert plan.shortest_turn_silence == 1


def test_forbidden_break_removes_boundary():
    # 容量 3 的 5 小节：b=3 被禁，2 页最优改为 b=2（最短静默 5）。
    plan = plan_pages(
        [1] * 5, [1, 5, 9, 1, 0], 3, 1, forbidden_breaks=[3]
    )
    assert plan.page_count == 2
    assert plan.breaks == [2]
    assert 3 not in plan.breaks


def test_required_and_forbidden_together():
    plan = plan_pages(
        [1] * 7, [5] * 6 + [0], 2, 5,
        required_breaks=[5], forbidden_breaks=[3],
    )
    assert 5 in plan.breaks and 3 not in plan.breaks
    assert brute_force_best_constrained(
        [1] * 7, [5] * 6 + [0], 2, 5, required=[5], forbidden=[3]
    ) == plan.breaks


def test_required_break_unsorted_input():
    plan = plan_pages(
        [1] * 7, [5] * 6 + [0], 2, 5, required_breaks=[5, 1]
    )
    assert plan.breaks == [1, 3, 5]


def test_unsatisfiable_required_when_single_page_possible():
    # 整篇本可一页装下，但必翻位置静默不足：无法满足，422 语义错误。
    with pytest.raises(PlanningError, match="必翻点静默不足"):
        plan_pages([3, 3, 3], [1, 0, 0], 10, 1, required_breaks=[2])


def test_required_silence_insufficient():
    with pytest.raises(PlanningError, match="必翻位置 4"):
        plan_pages([1] * 5, [1, 5, 9, 0.5, 0], 3, 1, required_breaks=[4])


def test_constraints_make_no_feasible_plan():
    # 两小节各高 5、容量 6：必须在 1 处断页；禁翻 1 -> 无方案。
    with pytest.raises(PlanningError, match="无合法翻页方案"):
        plan_pages([5, 5], [9, 0], 6, 1, forbidden_breaks=[1])
    # 必翻点静默足，但必翻之后剩余小节无法在允许位置排完：
    # 4 小节、容量 1 只能每节一页；位置 2 静默 0 不允许翻页，必翻 1 也救不了。
    with pytest.raises(PlanningError, match="无合法翻页方案"):
        plan_pages([1] * 4, [9, 0, 9, 0], 1, 1, required_breaks=[1])


def test_invalid_break_indices():
    with pytest.raises(PlanningError, match="越界序号 0"):
        plan_pages([1] * 3, [1, 1, 0], 5, 1, required_breaks=[0])
    with pytest.raises(PlanningError, match="越界序号 3"):
        plan_pages([1] * 3, [1, 1, 0], 5, 1, required_breaks=[3])
    with pytest.raises(PlanningError, match="越界序号"):
        plan_pages([1] * 3, [1, 1, 0], 5, 1, forbidden_breaks=[-1])
    with pytest.raises(PlanningError, match="重复序号 2"):
        plan_pages([1] * 3, [1, 1, 0], 5, 1, required_breaks=[2, 2])
    with pytest.raises(PlanningError, match="重复序号"):
        plan_pages([1] * 3, [1, 1, 0], 5, 1, forbidden_breaks=[1, 1])
    with pytest.raises(PlanningError, match="不能交叉"):
        plan_pages([1] * 3, [1, 1, 0], 5, 1,
                   required_breaks=[1], forbidden_breaks=[1])
    with pytest.raises(PlanningError, match="必须为整数"):
        plan_pages([1] * 3, [1, 1, 0], 5, 1, required_breaks=[1.5])
    with pytest.raises(PlanningError, match="必须为整数"):
        plan_pages([1] * 3, [1, 1, 0], 5, 1, forbidden_breaks=[True])


def test_optimization_is_global_not_per_segment():
    """三个目标必须全篇统一比较，不能按必翻点分段各取局部最优。

    n=11、容量 3、必翻 5（静默 9）与 8（静默 1）。必翻点把全篇切成
    [0..4]/[5..7]/[8..10] 三段：[5..7] 恰好装满一页，右段唯一方案，
    左段独立做"最大化最短静默"会选 b=3（局部最短静默 9）。
    但必翻点 8 的静默只有 1，全局最短静默恒为 1；在全局阈值 1 下左段
    字典序最小的合法切法是 b=2（页 [0,1] 高 2、[2,4] 高 3）。
    故全篇统一最优为 [2,5,8]，分段各取局部最优会错误得到 [3,5,8]。
    """
    heights = [1] * 11
    silences = [1, 5, 9, 5, 9, 5, 5, 1, 5, 5, 0]
    plan = plan_pages(heights, silences, 3, 1, required_breaks=[5, 8])
    expected = brute_force_best_constrained(
        heights, silences, 3, 1, required=[5, 8]
    )
    assert expected == [2, 5, 8]  # 与分段局部最优 [3,5,8] 明确区分
    assert plan.breaks == expected
    assert plan.page_count == 4
    assert plan.shortest_turn_silence == 1


def test_large_scale_with_constraints_performance():
    n = 100_000
    plan = plan_pages(
        [1] * n, [3.0] * n, 50, 2,
        required_breaks=[1000, 50000], forbidden_breaks=[25000],
    )
    assert 1000 in plan.breaks and 50000 in plan.breaks and 25000 not in plan.breaks
    assert all(p.used_height <= 50 + 1e-9 for p in plan.pages)


@pytest.mark.parametrize("seed", range(300))
def test_matches_bruteforce_constrained(seed):
    rng = random.Random(seed + 10_000)
    n = rng.randint(1, 8)
    heights = [rng.choice([0.5, 1.0, 1.25, 2.0, 3.0]) for _ in range(n)]
    silences = [rng.choice([0.0, 1.0, 5.0, 9.0]) for _ in range(n)]
    page_height = rng.choice([1, 2, 3, 5, 6]) + 0.5
    min_turn_time = rng.choice([0.0, 1.0, 5.0])

    positions = list(range(1, n))
    rng.shuffle(positions)
    # 随机把位置分到必翻/禁翻/不约束三组
    required, forbidden = [], []
    for p in positions:
        bucket = rng.random()
        if bucket < 0.25:
            required.append(p)
        elif bucket < 0.45:
            forbidden.append(p)

    too_tall = any(h > page_height for h in heights)
    expected = brute_force_best_constrained(
        heights, silences, page_height, min_turn_time, required, forbidden
    )

    if too_tall:
        with pytest.raises(PlanningError, match="超过页面可用高度"):
            plan_pages(
                heights, silences, page_height, min_turn_time,
                required_breaks=required, forbidden_breaks=forbidden,
            )
        return

    quiet_required = any(
        silences[b - 1] + 1e-9 < min_turn_time for b in required
    )
    if expected is None:
        with pytest.raises(PlanningError):
            plan_pages(
                heights, silences, page_height, min_turn_time,
                required_breaks=required, forbidden_breaks=forbidden,
            )
    else:
        plan = plan_pages(
            heights, silences, page_height, min_turn_time,
            required_breaks=required, forbidden_breaks=forbidden,
        )
        assert plan.breaks == expected, (
            f"mismatch h={heights} s={silences} H={page_height} t={min_turn_time}"
            f" req={required} forb={forbidden} quiet_req={quiet_required}"
        )
        assert all(b in plan.breaks for b in required)
        assert not (set(plan.breaks) & set(forbidden))
        for p in plan.pages:
            assert p.used_height <= page_height + 1e-9
        for b in plan.breaks:
            assert silences[b - 1] + 1e-9 >= min_turn_time
