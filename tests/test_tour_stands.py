"""巡演曲目单谱架换册核心的单元测试。"""

from __future__ import annotations

import random

import pytest

from app.tour_stands import (
    MAX_SHOWS,
    MAX_SLOTS,
    MIN_SLOTS,
    StandChangeError,
    plan_stand_changes,
)


def _brute_min_loads(ids, k):
    """状态 DP 暴力求最少上架次数（不跟踪槽位编号，只跟踪架上册集合）。

    逐场转移：所需册已在架上则零成本；否则计一次上架——架未满直接加入，
    架满则枚举撤下任一册。返回全部可能末态中的最小成本。
    """
    states = {frozenset(): 0}
    for book in ids:
        nxt = {}
        for stand, cost in states.items():
            if book in stand:
                if cost < nxt.get(stand, float("inf")):
                    nxt[stand] = cost
            elif len(stand) < k:
                key = frozenset(stand | {book})
                if cost + 1 < nxt.get(key, float("inf")):
                    nxt[key] = cost + 1
            else:
                for victim in stand:
                    key = frozenset((stand - {victim}) | {book})
                    if cost + 1 < nxt.get(key, float("inf")):
                        nxt[key] = cost + 1
        states = nxt
    return min(states.values())


def _next_use_after(ids, i):
    """独立前向扫描：各册在下标 i 之后首次被需要的场次下标（不再用为 n）。"""
    n = len(ids)
    result = {}
    for book in set(ids):
        j = i + 1
        while j < n and ids[j] != book:
            j += 1
        result[book] = j
    return result


def _check_invariants(result, ids, k):
    """逐场结构不变量 + 独立复算换册规则（空槽选择、撤下对象）。"""
    n = len(ids)
    assert result.slot_count == k
    assert result.plan_ids == list(ids)
    assert len(result.shows) == n
    assert result.total_loads == sum(
        1 for s in result.shows if s.load is not None
    )

    prev_slots = [None] * k
    for i, show in enumerate(result.shows):
        assert show.show_index == i + 1
        assert show.plan_id == ids[i]
        assert len(show.slots_after) == k
        # 场后各槽位：空槽为 None，其余为输入中的 ID 且同册至多占一槽
        on_stand = [b for b in show.slots_after if b is not None]
        assert len(set(on_stand)) == len(on_stand)
        assert set(on_stand) <= set(ids)
        # 每场所需册须在架上
        assert ids[i] in on_stand

        if show.load is None:
            # 无需上架 ⇔ 所需册此前已在架上，槽位不变、无撤下
            assert show.unload is None
            assert ids[i] in prev_slots
            assert show.slots_after == prev_slots
        else:
            assert show.load.plan_id == ids[i]
            assert 1 <= show.load.slot <= k
            if None in prev_slots:
                # 有空槽：用编号最小的空槽，无撤下
                assert show.unload is None
                assert show.load.slot == prev_slots.index(None) + 1
            else:
                # 架满：撤下未来最晚再次使用者（永不再用最晚），
                # 并列取方案 ID 字典序最小；腾出槽位即上架槽位
                assert show.unload is not None
                nxt = _next_use_after(ids, i)
                victim = min(
                    prev_slots, key=lambda b: (-nxt[b], b)
                )
                assert show.unload.plan_id == victim
                assert show.unload.slot == prev_slots.index(victim) + 1
                assert show.load.slot == show.unload.slot
            # 槽位守恒：撤下（若有）移除、上架填入，其余不变
            expect = list(prev_slots)
            if show.unload is not None:
                expect[show.unload.slot - 1] = None
            assert expect[show.load.slot - 1] is None
            expect[show.load.slot - 1] = ids[i]
            assert show.slots_after == expect
        prev_slots = show.slots_after


# ---------------------------------------------------------------- 确定性用例


def test_single_show_single_slot():
    result = plan_stand_changes(["a"], 1)
    assert result.total_loads == 1
    show = result.shows[0]
    assert show.show_index == 1
    assert show.plan_id == "a"
    assert (show.load.plan_id, show.load.slot) == ("a", 1)
    assert show.unload is None
    assert show.slots_after == ["a"]


def test_same_book_every_show_loads_once():
    result = plan_stand_changes(["a", "a", "a"], 1)
    assert result.total_loads == 1
    assert [s.load for s in result.shows][1:] == [None, None]
    assert all(s.slots_after == ["a"] for s in result.shows)


def test_empty_slots_filled_in_number_order():
    result = plan_stand_changes(["a", "b", "c"], 3)
    assert result.total_loads == 3
    assert [s.load.slot for s in result.shows] == [1, 2, 3]
    assert result.shows[-1].slots_after == ["a", "b", "c"]


def test_smallest_empty_slot_after_revisit():
    # 第 3 场回到 a 不动作；第 4 场 c 上架到当时唯一的空槽 3
    result = plan_stand_changes(["a", "b", "a", "c"], 3)
    assert result.total_loads == 3
    assert result.shows[2].load is None
    assert result.shows[3].load.slot == 3
    assert result.shows[3].slots_after == ["a", "b", "c"]


def test_evict_furthest_future_use():
    # 经典 Belady 情形：第 3 场架满，a 第 4 场还要用、b 永不再用，撤 b
    result = plan_stand_changes(["a", "b", "c", "a"], 2)
    assert result.total_loads == 3
    third = result.shows[2]
    assert (third.unload.plan_id, third.unload.slot) == ("b", 2)
    assert (third.load.plan_id, third.load.slot) == ("c", 2)
    assert result.shows[3].load is None  # a 仍在架上
    assert result.shows[3].slots_after == ["a", "c"]


def test_never_used_again_counts_as_latest():
    # 第 3 场架满：x 第 4 场再用（下标 3），y 永不再用 -> 撤 y
    result = plan_stand_changes(["x", "y", "z", "x"], 2)
    third = result.shows[2]
    assert third.unload.plan_id == "y"
    assert result.total_loads == 3


def test_eviction_tie_breaks_by_smallest_id():
    # 第 3 场架满：b、a 都永不再用，并列撤字典序最小的 a（槽 2）
    result = plan_stand_changes(["b", "a", "c"], 2)
    third = result.shows[2]
    assert (third.unload.plan_id, third.unload.slot) == ("a", 2)
    assert (third.load.plan_id, third.load.slot) == ("c", 2)
    assert third.slots_after == ["b", "c"]
    assert result.total_loads == 3


def test_tie_break_uses_string_order_not_slot():
    # p10 < p2（字符串字典序）：并列时撤 p10 而非槽位更靠前的 p2
    result = plan_stand_changes(["p2", "p10", "p1"], 2)
    third = result.shows[2]
    assert third.unload.plan_id == "p10"
    assert third.slots_after == ["p2", "p1"]


def test_readme_walkthrough():
    # README 示例：6 场 2 槽，覆盖空槽、命中、最晚再用撤下与并列撤下
    ids = ["id1", "id2", "id1", "id3", "id2", "id4"]
    result = plan_stand_changes(ids, 2)
    assert result.total_loads == 4
    assert [s.slots_after for s in result.shows] == [
        ["id1", None],
        ["id1", "id2"],
        ["id1", "id2"],
        ["id3", "id2"],
        ["id3", "id2"],
        ["id3", "id4"],
    ]
    assert result.shows[3].unload.plan_id == "id1"  # 永不再用
    assert result.shows[5].unload.plan_id == "id2"  # 并列取字典序最小


def test_single_slot_every_change_loads():
    result = plan_stand_changes(["a", "b", "a", "b"], 1)
    assert result.total_loads == 4
    assert [s.slots_after for s in result.shows] == [["a"], ["b"], ["a"], ["b"]]
    assert [s.unload.plan_id for s in result.shows[1:]] == ["a", "b", "a"]


def test_stand_stays_full_once_full():
    ids = ["a", "b", "c", "d", "a", "b", "c", "d"]
    result = plan_stand_changes(ids, 2)
    for show in result.shows[2:]:
        assert all(slot is not None for slot in show.slots_after)


def test_deterministic_repeated_runs():
    ids = ["a", "b", "a", "c", "b", "d", "a"]
    r1 = plan_stand_changes(ids, 2)
    r2 = plan_stand_changes(list(ids), 2)
    assert r1 == r2


# ---------------------------------------------------------------- 随机对照


@pytest.mark.parametrize("seed", range(300))
def test_random_matches_brute_force_min_loads(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 12)
    unique = rng.randint(1, 5)
    ids = [f"p{rng.randrange(unique)}" for _ in range(n)]
    k = rng.randint(MIN_SLOTS, 4)

    result = plan_stand_changes(ids, k)
    assert result.total_loads == _brute_min_loads(ids, k)
    _check_invariants(result, ids, k)


@pytest.mark.parametrize("seed", range(100))
def test_random_more_slots_than_books_never_evicts(seed):
    rng = random.Random(20_000 + seed)
    unique = rng.randint(1, 5)
    ids = [f"p{rng.randrange(unique)}" for _ in range(rng.randint(1, 15))]

    result = plan_stand_changes(ids, unique)
    # 槽位数不少于册种数：每册首次出现时上架一次，此后永不撤下
    assert result.total_loads == len(set(ids))
    assert all(s.unload is None for s in result.shows)
    _check_invariants(result, ids, unique)


def test_max_scale_performance_and_invariants():
    rng = random.Random(99)
    ids = [f"plan-{rng.randrange(60):03d}" for _ in range(MAX_SHOWS)]
    result = plan_stand_changes(ids, MAX_SLOTS)
    _check_invariants(result, ids, MAX_SLOTS)


@pytest.mark.parametrize("seed", range(20))
def test_medium_scale_matches_brute_force(seed):
    # 更大规模的最优性对照：状态数 C(7,≤5) 有界，暴力 DP 仍可在秒内完成
    rng = random.Random(30_000 + seed)
    ids = [f"p{rng.randrange(7)}" for _ in range(25)]
    k = rng.randint(MIN_SLOTS, 5)
    result = plan_stand_changes(ids, k)
    assert result.total_loads == _brute_min_loads(ids, k)
    _check_invariants(result, ids, k)


def test_boundary_single_show_max_slots():
    result = plan_stand_changes(["only"], MAX_SLOTS)
    assert result.total_loads == 1
    assert result.shows[0].slots_after == ["only"] + [None] * (MAX_SLOTS - 1)


# ---------------------------------------------------------------- 输入校验


def test_empty_show_list_rejected():
    with pytest.raises(StandChangeError, match="少于下限 1"):
        plan_stand_changes([], 1)


def test_too_many_shows_rejected():
    with pytest.raises(StandChangeError, match=f"超过上限 {MAX_SHOWS}"):
        plan_stand_changes(["a"] * (MAX_SHOWS + 1), 1)


@pytest.mark.parametrize("slot_count", [0, -1, MAX_SLOTS + 1, 100])
def test_slot_count_out_of_range_rejected(slot_count):
    with pytest.raises(StandChangeError, match="1~20"):
        plan_stand_changes(["a"], slot_count)


@pytest.mark.parametrize("slot_count", [1.0, 2.5, True, False, "2", None])
def test_non_integer_slot_count_rejected(slot_count):
    with pytest.raises(StandChangeError, match="整数"):
        plan_stand_changes(["a"], slot_count)


@pytest.mark.parametrize("plan_id", ["", 1, 1.0, True, None, b"x"])
def test_invalid_plan_id_rejected(plan_id):
    with pytest.raises(StandChangeError):
        plan_stand_changes(["a", plan_id], 2)


def test_plan_ids_not_a_sequence_rejected():
    with pytest.raises(StandChangeError):
        plan_stand_changes(iter(["a"]), 1)


def test_boundary_counts_accepted():
    assert plan_stand_changes(["a"], MIN_SLOTS).total_loads == 1
    many = plan_stand_changes([f"p{i}" for i in range(MAX_SHOWS)], MAX_SLOTS)
    assert many.total_loads == MAX_SHOWS
