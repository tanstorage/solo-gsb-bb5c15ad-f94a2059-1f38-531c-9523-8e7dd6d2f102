"""已保存方案练习路线规划核心的单元测试。"""

from __future__ import annotations

import itertools
import random
import time

import pytest

from app.practice_route import (
    MAX_PRACTICE_MEASURES,
    PracticeRouteError,
    plan_practice_route,
)


# ------------------------------------------------------------ 暴力参考实现


def _brute(measures, page_of, start_page, deps):
    """枚举全部排列：满足依赖的最小总距离 + 同成本小节序号序列字典序最小。"""
    best_cost = None
    best_order = None
    for perm in itertools.permutations(measures):
        pos = {m: i for i, m in enumerate(perm)}
        if any(pos[a] >= pos[b] for a, b in deps):
            continue
        cost = abs(start_page - page_of(perm[0]))
        cost += sum(
            abs(page_of(x) - page_of(y)) for x, y in zip(perm, perm[1:])
        )
        if (
            best_cost is None
            or cost < best_cost
            or (cost == best_cost and list(perm) < list(best_order))
        ):
            best_cost = cost
            best_order = perm
    assert best_order is not None, "测试用例应保证依赖无环"
    return list(best_order), best_cost


def _random_plan(rng):
    """随机分页：n 个小节切出若干页，返回 (page_starts, n, page_of)。"""
    n = rng.randint(1, 12)
    npage = rng.randint(1, n)
    cuts = sorted(rng.sample(range(1, n), npage - 1)) if npage > 1 else []
    bounds = [0] + cuts + [n]
    page_starts = bounds[:-1]

    def page_of(m):
        return next(i + 1 for i in range(npage) if bounds[i] <= m < bounds[i + 1])

    return page_starts, n, page_of


def _random_dag_deps(rng, measures, density=0.25):
    """按随机拓扑序加边，保证无环（允许传递依赖隐含成立）。"""
    order = list(measures)
    rng.shuffle(order)
    pos = {m: i for i, m in enumerate(order)}
    deps = []
    for a, b in itertools.permutations(measures, 2):
        if pos[a] < pos[b] and rng.random() < density:
            deps.append((a, b))
    return deps


# ------------------------------------------------------------ 基本语义


def test_single_measure():
    # 小节 3 在页 2（每页 2 小节），起始页 1
    res = plan_practice_route([0, 2, 4], 6, [3], 1, [])
    assert res.order == [3]
    assert res.order_pages == [2]
    assert res.segment_distances == [1]
    assert res.total_distance == 1


def test_single_measure_on_start_page_zero_distance():
    res = plan_practice_route([0, 2, 4], 6, [3], 2, [])
    assert res.order == [3]
    assert res.segment_distances == [0]
    assert res.total_distance == 0


def test_no_deps_same_page_gives_ascending_order():
    # 全部小节同页：任何顺序距离都是 0，取小节序号升序
    res = plan_practice_route([0], 5, [3, 1, 4], 1, [])
    assert res.order == [1, 3, 4]
    assert res.order_pages == [1, 1, 1]
    assert res.segment_distances == [0, 0, 0]
    assert res.total_distance == 0


def test_unsorted_input_measures_accepted():
    res = plan_practice_route([0, 2, 4, 6], 8, [7, 0, 5, 2], 1, [(7, 2)])
    assert res.order == [0, 5, 7, 2]


def test_dependencies_force_order_regardless_of_distance():
    # 页 1..3 各一个小节（0、1、2），起始页 1。无依赖时最近路线为
    # [0, 1, 2]（距离 0+1+1=2）；依赖 2→0 强制先练 2，可行顺序中
    # [1, 2, 0] 与 [2, 1, 0] 同为最优 4，字典序最小为 [1, 2, 0]。
    res = plan_practice_route([0, 1, 2], 3, [0, 1, 2], 1, [(2, 0)])
    assert res.order == [1, 2, 0]
    assert res.order_pages == [2, 3, 1]
    assert res.segment_distances == [1, 1, 2]
    assert res.total_distance == 4


def test_transitive_dependency_respected():
    # 依赖只给 0→1、1→2，传递地强制 0 在 2 前：唯一拓扑序 [0, 1, 2]
    res = plan_practice_route([0, 1, 2], 3, [2, 0, 1], 1, [(0, 1), (1, 2)])
    assert res.order == [0, 1, 2]


def test_duplicate_dependencies_allowed():
    res = plan_practice_route(
        [0, 1, 2], 3, [0, 1], 1, [(0, 1), (0, 1)]
    )
    assert res.order == [0, 1]


def test_lex_min_tiebreak_among_optimal_orders():
    # 8 小节、每页 2 小节：页1=小节0..1，页2=小节2..3，页3=小节4..5，
    # 页4=小节6..7。常练小节 0、2、5、7，起始页 1，依赖 7→2。
    # 最优总距离 5 有两条：[0,7,5,2]（页 1,4,3,2）与 [0,5,7,2]
    # （页 1,3,4,2），字典序最小为 [0,5,7,2]。
    res = plan_practice_route([0, 2, 4, 6], 8, [0, 2, 5, 7], 1, [(7, 2)])
    assert res.order == [0, 5, 7, 2]
    assert res.order_pages == [1, 3, 4, 2]
    assert res.segment_distances == [0, 2, 1, 2]
    assert res.total_distance == 5


def test_start_page_in_the_middle():
    # 页 1..4 各两小节，起始页 3，常练小节跨全部页
    res = plan_practice_route([0, 2, 4, 6], 8, [0, 2, 5, 7], 3, [])
    # 页码 1、2、3、4，从页 3 出发：最优为 3→4→2→1 或 3→2→4→…
    # 距离 = min(覆盖 [1,4] 从 3 出发) = (4-1) + min(3-1, 4-3) = 3+1 = 4
    assert res.total_distance == 4
    assert sum(res.segment_distances) == res.total_distance


# ------------------------------------------------------------ 输入校验


def test_empty_measures_rejected():
    with pytest.raises(PracticeRouteError, match="不能为空"):
        plan_practice_route([0], 1, [], 1, [])


def test_too_many_measures_rejected():
    n = MAX_PRACTICE_MEASURES + 1
    with pytest.raises(PracticeRouteError, match="超过上限"):
        plan_practice_route(list(range(n)), n, list(range(n)), 1, [])


def test_max_measures_accepted():
    n = MAX_PRACTICE_MEASURES
    res = plan_practice_route(list(range(n)), n, list(range(n)), 1, [])
    assert len(res.order) == MAX_PRACTICE_MEASURES


def test_measure_out_of_range_rejected():
    with pytest.raises(PracticeRouteError, match="越界"):
        plan_practice_route([0, 1], 2, [2], 1, [])
    with pytest.raises(PracticeRouteError, match="越界"):
        plan_practice_route([0, 1], 2, [-1], 1, [])


def test_duplicate_measure_rejected():
    with pytest.raises(PracticeRouteError, match="重复"):
        plan_practice_route([0, 1, 2], 3, [1, 1], 1, [])


def test_non_integer_measure_rejected():
    with pytest.raises(PracticeRouteError, match="整数"):
        plan_practice_route([0, 1], 2, [1.5], 1, [])  # type: ignore[list-item]
    with pytest.raises(PracticeRouteError, match="整数"):
        plan_practice_route([0, 1], 2, [True], 1, [])  # type: ignore[list-item]


def test_start_page_out_of_range_rejected():
    with pytest.raises(PracticeRouteError, match="越界"):
        plan_practice_route([0, 1], 2, [0], 0, [])
    with pytest.raises(PracticeRouteError, match="越界"):
        plan_practice_route([0, 1], 2, [0], 3, [])


def test_non_integer_start_page_rejected():
    with pytest.raises(PracticeRouteError, match="整数"):
        plan_practice_route([0, 1], 2, [0], 1.5, [])  # type: ignore[arg-type]
    with pytest.raises(PracticeRouteError, match="整数"):
        plan_practice_route([0, 1], 2, [0], True, [])  # type: ignore[arg-type]


def test_dependency_to_non_target_rejected():
    with pytest.raises(PracticeRouteError, match="非目标小节"):
        plan_practice_route([0, 1, 2], 3, [0, 1], 1, [(0, 2)])
    with pytest.raises(PracticeRouteError, match="非目标小节"):
        plan_practice_route([0, 1, 2], 3, [0, 1], 1, [(2, 0)])


def test_malformed_dependency_rejected():
    with pytest.raises(PracticeRouteError, match="二元组"):
        plan_practice_route([0, 1, 2], 3, [0, 1], 1, [(0,)])  # type: ignore[list-item]
    with pytest.raises(PracticeRouteError, match="二元组"):
        plan_practice_route([0, 1, 2], 3, [0, 1], 1, [(0, 1, 0)])  # type: ignore[list-item]
    with pytest.raises(PracticeRouteError, match="整数"):
        plan_practice_route([0, 1, 2], 3, [0, 1], 1, [(0, 1.0)])  # type: ignore[list-item]
    with pytest.raises(PracticeRouteError, match="整数"):
        plan_practice_route([0, 1, 2], 3, [0, 1], 1, [(False, 1)])  # type: ignore[list-item]


def test_self_loop_rejected_as_cycle():
    with pytest.raises(PracticeRouteError, match="成环"):
        plan_practice_route([0, 1], 2, [0, 1], 1, [(0, 0)])


def test_two_cycle_rejected():
    with pytest.raises(PracticeRouteError, match="成环"):
        plan_practice_route([0, 1], 2, [0, 1], 1, [(0, 1), (1, 0)])


def test_longer_cycle_rejected():
    deps = [(0, 1), (1, 2), (2, 0)]
    with pytest.raises(PracticeRouteError, match="成环"):
        plan_practice_route([0, 1, 2], 3, [0, 1, 2], 1, deps)


# ------------------------------------------------------------ 随机对照


@pytest.mark.parametrize("seed", range(80))
def test_random_against_brute_force(seed):
    rng = random.Random(seed)
    page_starts, n, page_of = _random_plan(rng)
    measures = sorted(rng.sample(range(n), rng.randint(1, min(8, n))))
    start_page = rng.randint(1, len(page_starts))
    deps = _random_dag_deps(rng, measures, density=rng.choice([0.0, 0.2, 0.5]))
    res = plan_practice_route(page_starts, n, measures, start_page, deps)
    expected_order, expected_cost = _brute(measures, page_of, start_page, deps)
    assert res.order == expected_order
    assert res.total_distance == expected_cost
    # 分段距离与页码映射自洽
    assert res.order_pages == [page_of(m) for m in expected_order]
    prev = start_page
    for page, seg in zip(res.order_pages, res.segment_distances):
        assert seg == abs(page - prev)
        prev = page
    assert sum(res.segment_distances) == res.total_distance


@pytest.mark.parametrize("seed", range(40))
def test_random_dense_dependencies(seed):
    rng = random.Random(1000 + seed)
    page_starts, n, page_of = _random_plan(rng)
    measures = sorted(rng.sample(range(n), rng.randint(min(2, n), min(8, n))))
    start_page = rng.randint(1, len(page_starts))
    # 稠密依赖（近乎全序），仍保证无环
    deps = _random_dag_deps(rng, measures, density=0.8)
    res = plan_practice_route(page_starts, n, measures, start_page, deps)
    expected_order, expected_cost = _brute(measures, page_of, start_page, deps)
    assert res.order == expected_order
    assert res.total_distance == expected_cost


# ------------------------------------------------------------ 性能


def test_performance_at_limit():
    # 15 个常练小节、无依赖、每小节一页：子集 DP 的最坏情形
    n = MAX_PRACTICE_MEASURES
    start = time.perf_counter()
    res = plan_practice_route(list(range(n)), n, list(range(n)), 8, [])
    elapsed = time.perf_counter() - start
    assert len(res.order) == n
    assert elapsed < 5.0


def test_performance_at_limit_with_dependencies():
    rng = random.Random(20260925)
    n = MAX_PRACTICE_MEASURES
    measures = list(range(n))
    deps = _random_dag_deps(rng, measures, density=0.3)
    start = time.perf_counter()
    res = plan_practice_route(list(range(n)), n, measures, 1, deps)
    elapsed = time.perf_counter() - start
    assert len(res.order) == n
    assert elapsed < 5.0
