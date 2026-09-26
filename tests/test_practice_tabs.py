"""纸质谱练习页签规划核心的单元测试。"""

from __future__ import annotations

import random
import time
from itertools import combinations

import pytest

from app.practice_tabs import (
    INF,
    MAX_TARGET_PAGES,
    PracticeTabError,
    _build_block_tables,
    _forward_cost_layers,
    _prefix_sums,
    _suffix_cost_layers,
    plan_practice_tabs,
)


# ------------------------------------------------------------ 暴力参考实现


def _brute(positions, weights, k):
    """枚举全部 K 元页签组合：最小代价 + 同代价升序列表字典序最小。

    每个目标页归最近页签（距离并列取较小页码）。
    """
    best_cost = None
    best_tabs = None
    for tabs in combinations(range(len(positions)), k):
        tabpos = [positions[i] for i in tabs]
        cost = 0
        for p, w in zip(positions, weights):
            cost += w * min(abs(p - t) for t in tabpos)
        if (
            best_cost is None
            or cost < best_cost
            or (cost == best_cost and tabpos < best_tabs)
        ):
            best_cost = cost
            best_tabs = tabpos
    mapping = []
    for p, w in zip(positions, weights):
        nt = min(best_tabs, key=lambda t: (abs(p - t), t))
        mapping.append((p, w, nt, abs(p - nt)))
    return list(best_tabs), best_cost, mapping


def _naive_forward(positions, weights, k_limit):
    """O(K·m²) 朴素正向 DP，用于对照分治优化结果。"""
    m = len(positions)
    psum, qpos = _prefix_sums(positions, weights)
    g, _ = _build_block_tables(positions, weights, psum, qpos)
    layers = [[INF] * (m + 1) for _ in range(k_limit + 1)]
    for i in range(1, m + 1):
        layers[1][i] = g[0][i]
    for k in range(2, k_limit + 1):
        for i in range(k, m + 1):
            layers[k][i] = min(
                layers[k - 1][j] + g[j][i] for j in range(k - 1, i)
            )
    return layers


def _naive_suffix(positions, weights, k_limit):
    """O(K·m²) 朴素后缀 DP，用于对照分治优化结果。"""
    m = len(positions)
    psum, qpos = _prefix_sums(positions, weights)
    g, _ = _build_block_tables(positions, weights, psum, qpos)
    layers = [[INF] * (m + 1) for _ in range(k_limit + 1)]
    for s in range(m):
        layers[1][s] = g[s][m]
    layers[1][m] = 0
    for t in range(2, k_limit + 1):
        for s in range(0, m - t + 1):
            layers[t][s] = min(
                g[s][r] + layers[t - 1][r]
                for r in range(s + 1, m - (t - 1) + 1)
            )
    return layers


# ------------------------------------------------------------ 基本语义


def test_single_target_single_tab():
    res = plan_practice_tabs([0], 1, [(0, 5)], 1)
    assert res.tab_pages == [1]
    assert res.tab_count == 1
    assert res.target_page_count == 1
    assert res.total_cost == 0
    assert res.targets[0].page == 1
    assert res.targets[0].weight == 5
    assert res.targets[0].nearest_tab == 1
    assert res.targets[0].distance == 0


def test_weights_merge_by_page():
    # 4 小节排在 2 页（页 1 = 小节 0..1，页 2 = 小节 2..3）
    page_starts = [0, 2]
    res = plan_practice_tabs(
        page_starts, 4, [(0, 3), (1, 2), (2, 1), (3, 4)], 1
    )
    # 合并后：页 1 权重 5、页 2 权重 5；K=1 取下中位数（较小页码）页 1，
    # 代价 = 5*0 + 5*1 = 5。
    assert res.target_page_count == 2
    assert [(t.page, t.weight) for t in res.targets] == [(1, 5), (2, 5)]
    assert res.tab_pages == [1]
    assert res.total_cost == 5
    # 页 2 距页签 1 等重无并列问题，归属页 1
    assert res.targets[1].nearest_tab == 1


def test_pages_without_targets_get_no_tabs():
    # 4 页，只有第 1、4 页有目标
    res = plan_practice_tabs(
        [0, 1, 2, 3], 4, [(0, 1), (3, 1)], 2
    )
    assert res.tab_pages == [1, 4]
    assert res.total_cost == 0


def test_distance_tie_goes_to_smaller_page():
    # 3 页权重 [4,1,3]、K=2：最优页签 {1,3}（代价 1），页 2 等距，
    # 归属取较小页码的页签 1。
    res = plan_practice_tabs([0, 1, 2], 3, [(0, 4), (1, 1), (2, 3)], 2)
    assert res.tab_pages == [1, 3]
    assert res.total_cost == 1
    middle = res.targets[1]
    assert middle.page == 2
    assert middle.distance == 1
    assert middle.nearest_tab == 1  # 等距取较小页码

    # 更长的情形：6 页权重 [4,2,1,4,1,4]、K=4，最优 {1,2,4,6}，
    # 页 3（距 2/4 等距）归 2、页 5（距 4/6 等距）归 4。
    res2 = plan_practice_tabs(
        list(range(6)),
        6,
        [(0, 4), (1, 2), (2, 1), (3, 4), (4, 1), (5, 4)],
        4,
    )
    assert res2.tab_pages == [1, 2, 4, 6]
    nearest = {t.page: t.nearest_tab for t in res2.targets}
    assert nearest[3] == 2
    assert nearest[5] == 4


def test_k_equals_target_count_zero_cost():
    res = plan_practice_tabs(
        [0, 1, 2, 3, 4],
        5,
        [(0, 2), (1, 3), (2, 1), (3, 9), (4, 4)],
        5,
    )
    assert res.tab_pages == [1, 2, 3, 4, 5]
    assert res.total_cost == 0
    assert all(t.distance == 0 for t in res.targets)


def test_weighted_median_shifts_under_unequal_weights():
    # 三页权重 1、10、1，K=1：中位数为页 2
    res = plan_practice_tabs([0, 1, 2], 3, [(0, 1), (1, 10), (2, 1)], 1)
    assert res.tab_pages == [2]
    assert res.total_cost == 2  # 1*1 + 10*0 + 1*1


def test_sparse_target_pages_use_real_page_distances():
    # 10 页，目标仅在页 1、6、10，权重均 1，K=2。
    # {1,10}: 6 距二者 5/4 -> 归 10，代价 5+4=9；{1,6}: 代价 0+0+4=4；
    # {6,10}: 5+0+0=5 -> 最优 {1,6}。
    res = plan_practice_tabs(
        list(range(10)), 10, [(0, 1), (5, 1), (9, 1)], 2
    )
    assert res.tab_pages == [1, 6]
    assert res.total_cost == 4
    assert [t.nearest_tab for t in res.targets] == [1, 6, 6]


def test_lex_min_tiebreak_prefers_smaller_tab_list():
    # 均匀三页、权重均 1、K=1：页签在 1、2、3 的代价分别为 3、2、3，
    # 无并列；构造一个真正并列：两页等重时 K=1，代价相同取下页码。
    res = plan_practice_tabs([0, 1], 2, [(0, 1), (1, 1)], 1)
    assert res.tab_pages == [1]

    # 四页权重 1，K=2：{1,3} 与 {2,4} 代价均为 2（{1,3}: 0+1+0+1；
    # {2,4}: 1+0+1+0），字典序最小为 {1,3}。
    res = plan_practice_tabs(
        [0, 1, 2, 3], 4, [(i, 1) for i in range(4)], 2
    )
    assert res.total_cost == 2
    assert res.tab_pages == [1, 3]


def test_lex_min_tiebreak_with_unequal_weights():
    # 页 1..5 权重 3、1、1、1、3，K=2。
    # {1,5}: 0 + 1 + 2 + 1 + 0 = 4；{1,4}: 0+1+1+0+3 = 5；
    # {2,5}: 3+0+1+1+0 = 5；检查 {1,3}: 0+1+0+1+6 = 8 ...
    # 暴力实现兜底校验，此处只断言与暴力一致。
    entries = [(0, 3), (1, 1), (2, 1), (3, 1), (4, 3)]
    res = plan_practice_tabs(list(range(5)), 5, entries, 2)
    bt, bc, _ = _brute([1, 2, 3, 4, 5], [3, 1, 1, 1, 3], 2)
    assert res.tab_pages == bt
    assert res.total_cost == bc


# ------------------------------------------------------------ 输入校验


def test_empty_targets_rejected():
    with pytest.raises(PracticeTabError, match="不能为空"):
        plan_practice_tabs([0], 1, [], 1)


def test_measure_out_of_range_rejected():
    with pytest.raises(PracticeTabError, match="越界"):
        plan_practice_tabs([0, 1], 2, [(2, 1)], 1)
    with pytest.raises(PracticeTabError, match="越界"):
        plan_practice_tabs([0, 1], 2, [(-1, 1)], 1)


def test_duplicate_measure_rejected():
    with pytest.raises(PracticeTabError, match="重复"):
        plan_practice_tabs([0, 1, 2], 3, [(0, 1), (0, 2)], 1)


def test_non_positive_weight_rejected():
    with pytest.raises(PracticeTabError, match="正整数"):
        plan_practice_tabs([0], 1, [(0, 0)], 1)
    with pytest.raises(PracticeTabError, match="正整数"):
        plan_practice_tabs([0], 1, [(0, -3)], 1)


def test_non_integer_weight_or_measure_rejected():
    with pytest.raises(PracticeTabError, match="正整数"):
        plan_practice_tabs([0], 1, [(0, 1.5)], 1)
    with pytest.raises(PracticeTabError, match="整数"):
        plan_practice_tabs([0], 1, [(1.0, 1)], 1)
    with pytest.raises(PracticeTabError, match="正整数"):
        plan_practice_tabs([0], 1, [(0, True)], 1)


def test_k_out_of_range_rejected():
    with pytest.raises(PracticeTabError, match="1 到目标页数"):
        plan_practice_tabs([0, 1], 2, [(0, 1), (1, 1)], 0)
    with pytest.raises(PracticeTabError, match="1 到目标页数"):
        plan_practice_tabs([0, 1], 2, [(0, 1), (1, 1)], 3)
    # K 的上界按**合并后目标页数**：4 个小节同页 => 仅 1 个目标页
    with pytest.raises(PracticeTabError, match="1 到目标页数（1）"):
        plan_practice_tabs([0], 4, [(i, 1) for i in range(4)], 2)


def test_k_must_be_integer():
    with pytest.raises(PracticeTabError, match="页签数必须为整数"):
        plan_practice_tabs([0], 1, [(0, 1)], 1.5)  # type: ignore[arg-type]
    with pytest.raises(PracticeTabError, match="页签数必须为整数"):
        plan_practice_tabs([0], 1, [(0, 1)], True)  # type: ignore[arg-type]


def test_too_many_target_pages_rejected():
    # MAX_TARGET_PAGES+1 个目标页（每页 1 小节）
    n = MAX_TARGET_PAGES + 1
    with pytest.raises(PracticeTabError, match="超过上限"):
        plan_practice_tabs(list(range(n)), n, [(i, 1) for i in range(n)], 1)


def test_target_pages_at_limit_accepted():
    n = MAX_TARGET_PAGES
    res = plan_practice_tabs(
        list(range(n)), n, [(i, 1) for i in range(n)], n
    )
    assert res.target_page_count == MAX_TARGET_PAGES
    assert res.total_cost == 0
    assert res.tab_pages[-1] == MAX_TARGET_PAGES


# ------------------------------------------------------------ 随机对照


@pytest.mark.parametrize("seed", range(60))
def test_random_against_brute_force_one_measure_per_page(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 10)
    chosen = sorted(rng.sample(range(n), rng.randint(1, n)))
    entries = [(m, rng.randint(1, 6)) for m in chosen]
    k = rng.randint(1, len(entries))
    res = plan_practice_tabs(list(range(n)), n, entries, k)
    positions = [m + 1 for m in chosen]
    weights = [w for _, w in entries]
    tabs, cost, mapping = _brute(positions, weights, k)
    assert res.tab_pages == tabs
    assert res.total_cost == cost
    assert [(t.page, t.weight, t.nearest_tab, t.distance) for t in res.targets] == mapping


@pytest.mark.parametrize("seed", range(60))
def test_random_against_brute_force_with_page_merge(seed):
    rng = random.Random(1000 + seed)
    n = rng.randint(1, 12)
    npage = rng.randint(1, n)
    cuts = sorted(rng.sample(range(1, n), npage - 1)) if npage > 1 else []
    bounds = [0] + cuts + [n]
    chosen = sorted(rng.sample(range(n), rng.randint(1, n)))
    wmap = {m: rng.randint(1, 7) for m in chosen}
    entries = [(m, wmap[m]) for m in chosen]
    merged: dict[int, int] = {}
    for m in chosen:
        page = next(i + 1 for i in range(npage) if bounds[i] <= m < bounds[i + 1])
        merged[page] = merged.get(page, 0) + wmap[m]
    pages = sorted(merged)
    weights = [merged[p] for p in pages]
    k = rng.randint(1, len(pages))
    res = plan_practice_tabs(bounds[:-1], n, entries, k)
    tabs, cost, mapping = _brute(pages, weights, k)
    assert res.tab_pages == tabs
    assert res.total_cost == cost
    assert [(t.page, t.weight, t.nearest_tab, t.distance) for t in res.targets] == mapping


@pytest.mark.parametrize("seed", range(30))
def test_random_large_weights_sparse_pages(seed):
    rng = random.Random(2000 + seed)
    n = rng.randint(12, 20)
    chosen = sorted(rng.sample(range(n), rng.randint(3, n)))
    entries = [(m, rng.randint(1, 100_000)) for m in chosen]
    k = rng.randint(1, len(entries))
    res = plan_practice_tabs(list(range(n)), n, entries, k)
    positions = [m + 1 for m in chosen]
    weights = [w for _, w in entries]
    tabs, cost, _ = _brute(positions, weights, k)
    assert res.tab_pages == tabs
    assert res.total_cost == cost


@pytest.mark.parametrize("seed", range(20))
def test_dp_layers_match_naive(seed):
    rng = random.Random(3000 + seed)
    m = rng.randint(1, 25)
    positions = sorted(rng.sample(range(1, 4 * m + 1), m))
    weights = [rng.randint(1, 20) for _ in range(m)]
    k_limit = rng.randint(1, m)
    psum, qpos = _prefix_sums(positions, weights)
    g, med = _build_block_tables(positions, weights, psum, qpos)

    # 下中位数定义核验：累计权重首次达到 ceil(W/2)
    for j in range(m):
        for i in range(j + 1, m + 1):
            total = sum(weights[j:i])
            threshold = (total + 1) // 2
            t = j
            acc2 = 0
            while acc2 + weights[t] < threshold:
                acc2 += weights[t]
                t += 1
            assert med[j][i] == t

    fast_f = _forward_cost_layers(m, g, k_limit)
    slow_f = _naive_forward(positions, weights, k_limit)
    fast_s = _suffix_cost_layers(m, g, k_limit)
    slow_s = _naive_suffix(positions, weights, k_limit)
    for k in range(1, k_limit + 1):
        for i in range(k, m + 1):
            assert fast_f[k][i] == slow_f[k][i]
        for s in range(0, m - k + 1):
            assert fast_s[k][s] == slow_s[k][s]


# ------------------------------------------------------------ 性能


def test_performance_at_limit():
    rng = random.Random(424242)
    n = MAX_TARGET_PAGES
    weights = [rng.randint(1, 10**6) for _ in range(n)]
    entries = list(enumerate(weights))
    start = time.perf_counter()
    res = plan_practice_tabs(list(range(n)), n, entries, n // 2)
    elapsed = time.perf_counter() - start
    assert len(res.tab_pages) == n // 2
    assert elapsed < 5.0


def test_large_plan_sparse_targets():
    # 2000 小节、500 页（每页 4 小节），随机 300 个常练小节
    rng = random.Random(99)
    page_starts = list(range(0, 2000, 4))
    chosen = sorted(rng.sample(range(2000), 300))
    entries = [(m, rng.randint(1, 9)) for m in chosen]
    start = time.perf_counter()
    res = plan_practice_tabs(page_starts, 2000, entries, 100)
    elapsed = time.perf_counter() - start
    assert len(res.tab_pages) == 100
    assert all(1 <= t <= 500 for t in res.tab_pages)
    assert elapsed < 5.0
