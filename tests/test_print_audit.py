"""纸质备用谱页序核对核心的单元测试。"""

from __future__ import annotations

import random
from functools import lru_cache

import pytest

from app.print_audit import (
    INSERT,
    KEEP,
    REMOVE,
    REPLACE,
    MAX_PAGES,
    PrintAuditError,
    audit_print_pages,
)


def _reference_min_ops(source, target):
    """独立的自顶向下记忆化 Levenshtein 距离（保留计 0、其余各计 1）。"""

    @lru_cache(maxsize=None)
    def go(i: int, j: int) -> int:
        if i == len(source):
            return len(target) - j
        if j == len(target):
            return len(source) - i
        best = 1 + min(go(i + 1, j + 1), go(i + 1, j), go(i, j + 1))
        if source[i] == target[j]:
            best = min(best, go(i + 1, j + 1))
        return best

    return go(0, 0)


def _apply_steps(source, steps):
    """按“原输入位置 + 同位置补页按序成批”的语义重放步骤，返回最终序列。

    同时校验：保留/替换/移除的原下标按严格递增次序各消费一个实物页，
    补入位置不越过当前消费位置，非保留步数即操作数。
    """
    consumed = 0
    out = []
    non_keep = 0
    for step in steps:
        if step.action == INSERT:
            assert step.observed_page is None
            assert step.position == consumed
            out.append(step.target_page)
            non_keep += 1
        else:
            assert step.position == consumed
            assert step.observed_page == source[consumed]
            consumed += 1
            if step.action == KEEP:
                assert step.target_page == step.observed_page
                out.append(step.observed_page)
            elif step.action == REPLACE:
                out.append(step.target_page)
                non_keep += 1
            else:  # REMOVE
                assert step.target_page is None
                non_keep += 1
    assert consumed == len(source)
    return out, non_keep


def _check(page_count, scanned):
    result = audit_print_pages(page_count, scanned)
    target = list(range(1, page_count + 1))
    final, non_keep = _apply_steps(list(scanned), result.steps)
    assert final == target
    assert non_keep == result.operation_count
    assert result.operation_count == _reference_min_ops(
        tuple(scanned), tuple(target)
    )
    return result


def test_perfect_order_all_keeps_zero_ops():
    result = _check(5, [1, 2, 3, 4, 5])
    assert result.operation_count == 0
    assert [s.action for s in result.steps] == [KEEP] * 5
    assert [s.position for s in result.steps] == [0, 1, 2, 3, 4]
    assert [(s.observed_page, s.target_page) for s in result.steps] == [
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (5, 5),
    ]


def test_empty_physical_stack_inserts_all_at_position_zero():
    result = _check(4, [])
    assert result.operation_count == 4
    assert [s.action for s in result.steps] == [INSERT] * 4
    # 全为空栈：所有补页位置都是 0（末尾补页时位置才等于数组长度）
    assert [s.position for s in result.steps] == [0, 0, 0, 0]
    assert [s.target_page for s in result.steps] == [1, 2, 3, 4]
    assert all(s.observed_page is None for s in result.steps)


def test_missing_front_page_insert_before_position_zero():
    result = _check(4, [2, 3, 4])
    actions = [s.action for s in result.steps]
    assert actions == [INSERT, KEEP, KEEP, KEEP]
    assert result.operation_count == 1
    first = result.steps[0]
    assert first.position == 0 and first.target_page == 1


def test_missing_back_page_insert_at_length_position():
    result = _check(4, [1, 2, 3])
    actions = [s.action for s in result.steps]
    assert actions == [KEEP, KEEP, KEEP, INSERT]
    insert_step = result.steps[-1]
    # 补在末尾：位置等于原数组长度 3
    assert insert_step.position == 3 and insert_step.target_page == 4


def test_missing_middle_insert_before_that_position():
    result = _check(5, [1, 2, 4, 5])
    actions = [s.action for s in result.steps]
    assert actions == [KEEP, KEEP, INSERT, KEEP, KEEP]
    insert_step = result.steps[2]
    # 原输入下标 2 处是页码 4：缺页 3 插在它之前
    assert insert_step.position == 2 and insert_step.target_page == 3


def test_duplicate_extra_page_removed_at_its_index():
    result = _check(3, [1, 1, 2, 3])
    assert result.operation_count == 1
    assert [s.action for s in result.steps] == [KEEP, REMOVE, KEEP, KEEP]
    removed = result.steps[1]
    assert removed.position == 1 and removed.observed_page == 1
    assert removed.target_page is None


def test_wrong_page_replaced_in_place():
    result = _check(3, [1, 9, 3])
    assert result.operation_count == 1
    assert [s.action for s in result.steps] == [KEEP, REPLACE, KEEP]
    replaced = result.steps[1]
    assert replaced.position == 1
    assert replaced.observed_page == 9 and replaced.target_page == 2


def test_all_wrong_with_extra_replaces_then_removes():
    # 错页优先就地替换，多余页才移除
    result = _check(3, [7, 8, 9, 10])
    assert result.operation_count == 4
    assert [s.action for s in result.steps] == [
        REPLACE,
        REPLACE,
        REPLACE,
        REMOVE,
    ]
    assert result.steps[0].observed_page == 7 and result.steps[0].target_page == 1


def test_tie_keep_preferred_over_insert():
    # [1] -> [1,2]：保留 1 再末尾补 2，与补 2 再…同成本，优先保留
    result = _check(2, [1])
    assert [s.action for s in result.steps] == [KEEP, INSERT]
    assert result.steps[-1].position == 1


def test_tie_replace_preferred_over_remove_and_insert():
    # [2,1] -> [1,2]：双替换与移除+补入同为 2 步，替换优先
    result = _check(2, [2, 1])
    assert [s.action for s in result.steps] == [REPLACE, REPLACE]
    assert result.steps[0].observed_page == 2 and result.steps[0].target_page == 1
    assert result.steps[1].observed_page == 1 and result.steps[1].target_page == 2


def test_tie_remove_preferred_over_insert():
    # [2,3,1,2] -> [1,2,3]：首步移除与补入同为最优（成本 3），移除优先
    result = _check(3, [2, 3, 1, 2])
    assert result.operation_count == 3
    assert [s.action for s in result.steps] == [
        REMOVE,
        REMOVE,
        KEEP,
        KEEP,
        INSERT,
    ]
    assert result.steps[0].observed_page == 2 and result.steps[0].position == 0


def test_duplicate_kept_page_at_end_removed():
    result = _check(5, [1, 2, 3, 4, 5, 5])
    assert result.operation_count == 1
    assert [s.action for s in result.steps] == [KEEP] * 5 + [REMOVE]
    assert result.steps[-1].position == 5


@pytest.mark.parametrize(
    "page_count,scanned,fragment",
    [
        (0, [1], "规划页数"),
        (-1, [1], "规划页数"),
        (3, [0], "正整数"),
        (3, [-2], "正整数"),
        (3, [1, True], "布尔"),
        (3, [1, False], "布尔"),
        (3, [1.5], "正整数"),
        (3, [1, 2.0], "正整数"),
        (3, ["1"], "正整数"),
        (3, [None], "正整数"),
        (MAX_PAGES + 1, [1], "规划页数"),
        (3, [1] * (MAX_PAGES + 1), "实物页数"),
    ],
)
def test_invalid_inputs_raise(page_count, scanned, fragment):
    with pytest.raises(PrintAuditError) as exc:
        audit_print_pages(page_count, scanned)
    assert fragment in str(exc.value)


def test_page_count_boundary_500_accepted():
    scanned = list(range(1, 501))
    result = audit_print_pages(500, scanned)
    assert result.operation_count == 0
    assert len(result.steps) == 500


def test_scanned_length_boundary_500_accepted():
    # 500 个实物页、规划 1 页：保留首页后移除 499 张多页
    result = audit_print_pages(1, [1] * 500)
    assert result.operation_count == 499
    assert result.steps[0].action == KEEP
    assert all(s.action == REMOVE for s in result.steps[1:])


def test_large_scale_performance():
    scanned = list(range(2, 501))  # 缺首页、共 499 页
    result = audit_print_pages(500, scanned)
    assert result.operation_count == 1
    assert result.steps[0].action == INSERT and result.steps[0].position == 0


@pytest.mark.parametrize("seed", range(60))
def test_random_cases_match_independent_reference(seed):
    rng = random.Random(seed)
    page_count = rng.randint(1, 6)
    m = rng.randint(0, 7)
    # 允许重复、允许超过规划页数的错页页码
    scanned = [rng.randint(1, page_count + 2) for _ in range(m)]
    _check(page_count, scanned)


_RANK = {KEEP: 0, REPLACE: 1, REMOVE: 2, INSERT: 3}


def _brute_force_lex_min(source, target):
    """穷举所有最优对齐脚本，返回按 保留<替换<移除<补入 字典序最小的一条。"""

    @lru_cache(maxsize=None)
    def dist(i: int, j: int) -> int:
        if i == len(source):
            return len(target) - j
        if j == len(target):
            return len(source) - i
        best = 1 + min(dist(i + 1, j + 1), dist(i + 1, j), dist(i, j + 1))
        if source[i] == target[j]:
            best = min(best, dist(i + 1, j + 1))
        return best

    scripts = []

    def dfs(i, j, script):
        if i == len(source) and j == len(target):
            scripts.append(tuple(script))
            return
        d = dist(i, j)
        if i < len(source) and j < len(target):
            if source[i] == target[j] and dist(i + 1, j + 1) == d:
                dfs(i + 1, j + 1, script + [(KEEP, i, source[i], target[j])])
            if 1 + dist(i + 1, j + 1) == d:
                dfs(i + 1, j + 1, script + [(REPLACE, i, source[i], target[j])])
        if i < len(source) and 1 + dist(i + 1, j) == d:
            dfs(i + 1, j, script + [(REMOVE, i, source[i], None)])
        if j < len(target) and 1 + dist(i, j + 1) == d:
            dfs(i, j + 1, script + [(INSERT, i, None, target[j])])

    dfs(0, 0, [])
    return min(scripts, key=lambda s: tuple(_RANK[a] for a, *_ in s))


@pytest.mark.parametrize("seed", range(30))
def test_steps_are_lexicographically_minimal_among_optimal(seed):
    """实现的确定性序列与穷举出的字典序最小最优脚本完全一致。"""
    rng = random.Random(1000 + seed)
    page_count = rng.randint(1, 5)
    m = rng.randint(0, 6)
    scanned = [rng.randint(1, page_count + 2) for _ in range(m)]
    result = audit_print_pages(page_count, scanned)
    got = tuple(
        (s.action, s.position, s.observed_page, s.target_page)
        for s in result.steps
    )
    assert got == _brute_force_lex_min(
        tuple(scanned), tuple(range(1, page_count + 1))
    )
