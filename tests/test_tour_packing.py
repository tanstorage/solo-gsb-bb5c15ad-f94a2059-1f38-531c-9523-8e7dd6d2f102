"""巡演装运乐谱册装箱核心的单元测试。"""

from __future__ import annotations

import random

import pytest

from app.tour_packing import (
    MAX_BOOKS,
    MIN_BOOKS,
    TourPackingError,
    pack_tour_books,
)


def _brute_force(ids, weights, capacity):
    """枚举全部受限增长字符串形式的集合分箱，按三级目标取最优。

    返回 (箱数, 最重箱装载页数, 规范箱列表)；单册超容量返回 None。
    """
    if any(w > capacity for w in weights):
        return None
    books = sorted(zip(ids, weights), key=lambda item: item[0])
    labels = sorted({bid for bid, _ in books})
    n = len(books)

    best = None

    def canonical(boxes):
        return tuple(sorted(tuple(sorted(bid for bid, _ in box)) for box in boxes))

    def rec(i, boxes):
        nonlocal best
        if i == n:
            if not boxes:
                return
            canon = canonical(boxes)
            max_load = max(sum(w for _, w in box) for box in boxes)
            cost = (len(boxes), max_load, canon)
            if best is None or cost < best[0]:
                best = (cost, canon)
            return
        bid, w = books[i]
        # 放入已有箱
        for b in range(len(boxes)):
            load = sum(x[1] for x in boxes[b])
            if load + w <= capacity:
                boxes[b].append((bid, w))
                rec(i + 1, boxes)
                boxes[b].pop()
        # 开新箱
        boxes.append([(bid, w)])
        rec(i + 1, boxes)
        boxes.pop()

    rec(0, [])
    assert best is not None
    return best[0]


def _packed_id_tuples(result):
    return [list(box.plan_ids) for box in result.boxes]


def _check_invariants(result, ids, weights, capacity):
    """结构与装箱不变量检查。"""
    books = sorted(zip(ids, weights), key=lambda item: item[0])
    assert result.capacity == capacity
    assert result.plan_ids == [bid for bid, _ in books]
    assert result.book_pages == [w for _, w in books]
    assert result.total_pages == sum(weights)
    assert len(result.boxes) == result.box_count
    assert result.max_loaded_pages == max(b.loaded_pages for b in result.boxes)

    seen = []
    for idx, box in enumerate(result.boxes, start=1):
        assert box.box_index == idx
        assert len(box.plan_ids) >= 1  # 每箱至少一册
        assert box.plan_ids == sorted(box.plan_ids)  # 箱内 ID 升序
        page_of = dict(books)
        assert box.loaded_pages == sum(page_of[pid] for pid in box.plan_ids)
        assert box.loaded_pages <= capacity
        assert box.remaining_capacity == capacity - box.loaded_pages
        seen.extend(box.plan_ids)

    # 每册恰好出现一次
    assert sorted(seen) == sorted(ids)
    # 箱列表按 ID 元组字典序排序
    tuples = _packed_id_tuples(result)
    assert tuples == sorted(tuples)


@pytest.mark.parametrize("seed", range(400))
def test_random_packing_matches_brute_force(seed):
    rng = random.Random(seed)
    # 暴力枚举集合分箱（Bell(n) 增长极快），仅在 n ≤ 8 时穷举对照
    n = rng.randint(MIN_BOOKS, 8)
    ids = [f"p{i:02d}" for i in range(n)]
    weights = [rng.randint(1, 6) for _ in range(n)]
    capacity = rng.randint(max(weights), 12)

    if any(w > capacity for w in weights):
        with pytest.raises(TourPackingError, match="超过箱体页容量"):
            pack_tour_books(list(zip(ids, weights)), capacity)
        return

    result = pack_tour_books(list(zip(ids, weights)), capacity)
    ref_bins, ref_max, ref_tuples = _brute_force(ids, weights, capacity)
    assert result.box_count == ref_bins
    assert result.max_loaded_pages == ref_max
    assert _packed_id_tuples(result) == [list(t) for t in ref_tuples]
    _check_invariants(result, ids, weights, capacity)


@pytest.mark.parametrize("seed", range(200))
def test_random_packing_large_n_invariants(seed):
    """n 到 12 时不穷举，校验三级最优的结构性质与可再装判定。"""
    rng = random.Random(10_000 + seed)
    n = rng.randint(9, MAX_BOOKS)
    ids = [f"p{i:02d}" for i in range(n)]
    weights = [rng.randint(1, 8) for _ in range(n)]
    capacity = rng.randint(max(weights), 16)
    result = pack_tour_books(list(zip(ids, weights)), capacity)
    _check_invariants(result, ids, weights, capacity)

    # 箱数不可再少：删去任一箱后无法把其册塞入其余箱（以最优最重页数为上界）
    page_of = dict(zip(ids, weights))
    boxes = result.boxes
    for drop in range(len(boxes)):
        other_loads = [
            boxes[b].loaded_pages for b in range(len(boxes)) if b != drop
        ]
        dropped = [page_of[pid] for pid in boxes[drop].plan_ids]

        def fit(pos, loads):
            if pos == len(dropped):
                return True
            w = dropped[pos]
            seen_loads = set()
            for i, load in enumerate(loads):
                if load in seen_loads or load + w > result.max_loaded_pages:
                    continue
                seen_loads.add(load)
                loads[i] += w
                if fit(pos + 1, loads):
                    return True
                loads[i] -= w
            return False

        assert not fit(0, list(other_loads))

    # 最重箱页数不可再降（用一个等价的装箱可装性必要检查：下界 ceil(总/C)）
    assert result.box_count >= (
        result.total_pages + capacity - 1
    ) // capacity


def test_input_order_does_not_matter():
    ids = ["a", "b", "c", "d"]
    weights = [2, 3, 4, 5]
    shuffled = list(zip(ids, weights))
    random.Random(7).shuffle(shuffled)
    r1 = pack_tour_books(shuffled, 6)
    r2 = pack_tour_books(list(zip(ids, weights)), 6)
    assert _packed_id_tuples(r1) == _packed_id_tuples(r2)
    assert r1.box_count == r2.box_count
    assert r1.max_loaded_pages == r2.max_loaded_pages


def test_two_books_one_box():
    result = pack_tour_books([("x", 3), ("y", 4)], 10)
    assert result.box_count == 1
    assert result.max_loaded_pages == 7
    box = result.boxes[0]
    assert box.plan_ids == ["x", "y"]
    assert box.loaded_pages == 7
    assert box.remaining_capacity == 3
    assert result.total_pages == 7


def test_min_boxes_then_balanced_max_load():
    # [2,3,4,5]、容量 6：最少 3 箱（总 14）；最重可压到 5：
    # (2,3)=5、4、5 而非 (2,4)=6、3、5
    result = pack_tour_books(
        [("a", 2), ("b", 3), ("c", 4), ("d", 5)], 6
    )
    assert result.box_count == 3
    assert result.max_loaded_pages == 5
    assert _packed_id_tuples(result) == [["a", "b"], ["c"], ["d"]]


def test_lexicographic_tie_break_equal_thickness():
    # 四册各 2 页、容量 4：2 箱，任一两两分组最重都是 4；
    # 规范分箱字典序最小为 (a,b)|(c,d)
    result = pack_tour_books(
        [("a", 2), ("b", 2), ("c", 2), ("d", 2)], 4
    )
    assert result.box_count == 2
    assert result.max_loaded_pages == 4
    assert _packed_id_tuples(result) == [["a", "b"], ["c", "d"]]


def test_lexicographic_tie_break_second_box():
    # 五册各 1 页、容量 2：2 箱必有一箱装 3 不可行，故 3 箱（2+2+1）。
    # 规范分箱字典序比较中，更短的前缀元组更小，故单册箱落在最小 ID 上：
    # (p00)|(p01,p02)|(p03,p04)
    result = pack_tour_books(
        [(f"p{i:02d}", 1) for i in range(5)], 2
    )
    assert result.box_count == 3
    assert result.max_loaded_pages == 2
    assert _packed_id_tuples(result) == [
        ["p00"],
        ["p01", "p02"],
        ["p03", "p04"],
    ]


def test_ids_sorted_lexicographically_not_numerically():
    # 字符串 ID 按字典序：p10 < p2
    result = pack_tour_books(
        [("p2", 1), ("p10", 1), ("p1", 1)], 2
    )
    assert result.plan_ids == ["p1", "p10", "p2"]
    # 2 箱为一对 + 一单；单册元组 (p1) 是 (p1,…) 的前缀而更小：
    # (p1)|(p10,p2)
    assert _packed_id_tuples(result) == [["p1"], ["p10", "p2"]]


def test_each_book_its_own_box_when_capacity_tight():
    result = pack_tour_books(
        [("a", 3), ("b", 3), ("c", 3)], 3
    )
    assert result.box_count == 3
    assert result.max_loaded_pages == 3
    assert _packed_id_tuples(result) == [["a"], ["b"], ["c"]]
    for box in result.boxes:
        assert box.remaining_capacity == 0


def test_single_book_oversize_rejected():
    with pytest.raises(TourPackingError, match="超过箱体页容量") as exc:
        pack_tour_books([("a", 7), ("b", 2)], 6)
    assert "整本" in str(exc.value)


def test_multiple_oversize_reports_smallest_id_first():
    with pytest.raises(TourPackingError) as exc:
        pack_tour_books([("z", 9), ("a", 8), ("b", 1)], 7)
    assert "a" in str(exc.value)
    assert "8" in str(exc.value)


# ---------------------------------------------------------------- 输入校验


@pytest.mark.parametrize("n", [0, 1])
def test_too_few_books_rejected(n):
    with pytest.raises(TourPackingError, match="下限 2"):
        pack_tour_books([("a", 1), ("b", 1)][:n], 5)


def test_too_many_books_rejected():
    books = [(f"p{i:02d}", 1) for i in range(MAX_BOOKS + 1)]
    with pytest.raises(TourPackingError, match=f"上限 {MAX_BOOKS}"):
        pack_tour_books(books, 5)


def test_boundary_book_counts_accepted():
    for n in (MIN_BOOKS, MAX_BOOKS):
        result = pack_tour_books([(f"p{i:02d}", 1) for i in range(n)], 1000)
        assert result.box_count == 1


def test_duplicate_id_rejected():
    with pytest.raises(TourPackingError, match="重复"):
        pack_tour_books([("a", 1), ("a", 2)], 5)


@pytest.mark.parametrize("plan_id", ["", 1, 1.0, True, None, b"x"])
def test_invalid_id_rejected(plan_id):
    with pytest.raises(TourPackingError):
        pack_tour_books([(plan_id, 1), ("b", 1)], 5)


@pytest.mark.parametrize("pages", [0, -1, 1.0, 1.5, True, "2", None])
def test_invalid_pages_rejected(pages):
    with pytest.raises(TourPackingError):
        pack_tour_books([("a", pages), ("b", 1)], 5)


@pytest.mark.parametrize("capacity", [0, -3])
def test_non_positive_capacity_rejected(capacity):
    with pytest.raises(TourPackingError, match="正整数"):
        pack_tour_books([("a", 1), ("b", 1)], capacity)


@pytest.mark.parametrize("capacity", [1.0, 1.5, True, False, "5", None])
def test_non_integer_capacity_rejected(capacity):
    with pytest.raises(TourPackingError, match="整数"):
        pack_tour_books([("a", 1), ("b", 1)], capacity)


def test_books_not_a_sequence_rejected():
    with pytest.raises(TourPackingError):
        pack_tour_books(iter([("a", 1), ("b", 1)]), 5)


def test_twelve_books_performance():
    books = [(f"p{i:02d}", 1) for i in range(12)]
    result = pack_tour_books(books, 2)
    assert result.box_count == 6
    assert result.max_loaded_pages == 2
