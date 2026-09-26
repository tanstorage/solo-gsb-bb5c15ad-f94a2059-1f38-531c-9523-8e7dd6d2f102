"""纸质乐谱小册子拼版核心的单元测试。"""

from __future__ import annotations

import itertools
import random

import pytest

from app.booklet import (
    MAX_PAGES,
    ImpositionError,
    impose_booklet,
)


def _brute_force(page_count, ends, max_sheets):
    """枚举全部 2^(k-1) 种分帖，按 (总空白, 帖数, 末页序列) 取最优。

    返回 (总空白页, 帖数, [(起始页, 末页), ...])；无合法分帖返回 None。
    """
    capacity = 4 * max_sheets
    boundaries = [0] + ends
    k = len(ends)
    best = None
    for mask in range(1 << (k - 1)):
        cuts = [0]
        for i in range(k - 1):
            if mask & (1 << i):
                cuts.append(i + 1)
        cuts.append(k)
        sigs = []
        blanks = 0
        feasible = True
        for a, b in zip(cuts, cuts[1:]):
            start = boundaries[a] + 1
            end = boundaries[b]
            content = end - start + 1
            if content + (-content) % 4 > capacity:
                feasible = False
                break
            blanks += (-content) % 4
            sigs.append((start, end))
        if not feasible:
            continue
        end_tuple = tuple(e for _, e in sigs)
        cost = (blanks, len(sigs), end_tuple)
        if best is None or cost < best[0]:
            best = (cost, sigs)
    return best


def _check_invariants(result, page_count, ends, max_sheets):
    """通用结构与拼版不变量检查。"""
    # 各帖连续覆盖 1..page_count，帖间无重叠/缺口
    assert result.page_count == page_count
    assert result.segment_end_pages == list(ends)
    assert result.max_sheets_per_signature == max_sheets
    covered = []
    total_blanks = 0
    prev_end = 0
    for idx, sig in enumerate(result.signatures, start=1):
        assert sig.signature_index == idx
        assert sig.start_page == prev_end + 1
        assert sig.end_page >= sig.start_page
        assert 0 <= sig.blank_pages <= 3
        assert sig.content_pages == sig.end_page - sig.start_page + 1
        assert (sig.content_pages + sig.blank_pages) % 4 == 0
        assert sig.content_pages + sig.blank_pages <= 4 * max_sheets
        assert len(sig.sheets) == (sig.content_pages + sig.blank_pages) // 4
        total_blanks += sig.blank_pages
        # 骑马订每个印张的四个位置公式
        total_pages = sig.content_pages + sig.blank_pages
        base = sig.start_page - 1
        for t, sheet in enumerate(sig.sheets):
            assert sheet.sheet_index == t + 1
            expected = [
                total_pages - 2 * t,
                1 + 2 * t,
                2 + 2 * t,
                total_pages - 1 - 2 * t,
            ]
            actual = [
                sheet.front_left,
                sheet.front_right,
                sheet.back_left,
                sheet.back_right,
            ]
            for local, value in zip(expected, actual):
                if local <= sig.content_pages:
                    assert value == base + local
                else:
                    assert value is None
        prev_end = sig.end_page
        covered.extend(range(sig.start_page, sig.end_page + 1))
    assert prev_end == page_count
    assert covered == list(range(1, page_count + 1))
    # 全册所有非空页码恰好是 1..page_count 各一次
    printed = []
    for sig in result.signatures:
        for sheet in sig.sheets:
            printed.extend(
                v
                for v in (
                    sheet.front_left,
                    sheet.front_right,
                    sheet.back_left,
                    sheet.back_right,
                )
                if v is not None
            )
    assert sorted(printed) == list(range(1, page_count + 1))
    assert result.total_blank_pages == total_blanks
    assert result.signature_count == len(result.signatures)
    # 各帖末页必为曲段末页（曲段不跨帖），且按序与某个分帖一致
    sig_ends = [sig.end_page for sig in result.signatures]
    assert sig_ends[-1] == ends[-1]
    assert set(sig_ends) <= set(ends)


def test_eight_page_single_signature_classic_layout():
    result = impose_booklet(8, [8], 2)
    assert result.signature_count == 1
    assert result.total_blank_pages == 0
    sig = result.signatures[0]
    assert (sig.start_page, sig.end_page, sig.content_pages) == (1, 8, 8)
    assert len(sig.sheets) == 2
    outer, inner = sig.sheets
    # 外层正面为末页/首页，背面为次页/倒数第二页
    assert (outer.front_left, outer.front_right) == (8, 1)
    assert (outer.back_left, outer.back_right) == (2, 7)
    assert (inner.front_left, inner.front_right) == (6, 3)
    assert (inner.back_left, inner.back_right) == (4, 5)


def test_padding_places_blanks_at_signature_tail():
    # 一帖含原谱 1..2（1 印张容量），帖尾补 2 页空白：
    # 局部版面 [4,1,2,3] 中局部 3、4 为空白 -> (null,1,2,null)
    result = impose_booklet(2, [2], 1)
    sig = result.signatures[0]
    assert sig.blank_pages == 2
    sheet = sig.sheets[0]
    assert sheet.front_left is None
    assert sheet.front_right == 1
    assert sheet.back_left == 2
    assert sheet.back_right is None


def test_global_page_numbers_across_multiple_signatures():
    # 6 页三段，每帖 1 印张：[1..2]+2空 | [3..6]
    result = impose_booklet(6, [2, 4, 6], 1)
    assert result.signature_count == 2
    first, second = result.signatures
    assert (first.start_page, first.end_page) == (1, 2)
    assert first.blank_pages == 2
    assert (
        first.sheets[0].front_left,
        first.sheets[0].front_right,
        first.sheets[0].back_left,
        first.sheets[0].back_right,
    ) == (None, 1, 2, None)
    assert (second.start_page, second.end_page) == (3, 6)
    assert second.blank_pages == 0
    assert (
        second.sheets[0].front_left,
        second.sheets[0].front_right,
        second.sheets[0].back_left,
        second.sheets[0].back_right,
    ) == (6, 3, 4, 5)


def test_lexicographic_tie_break_prefers_earlier_first_end_page():
    # 9 页三段 [3,6,9]，帖容量 8：
    # [3|6,9] 与 [3,6|9] 均为 3 空白、2 帖，末页序列 (3,9) vs (6,9)，取前者
    result = impose_booklet(9, [3, 6, 9], 2)
    assert result.total_blank_pages == 3
    assert result.signature_count == 2
    assert [s.end_page for s in result.signatures] == [3, 9]
    assert result.signatures[0].blank_pages == 1
    assert result.signatures[1].blank_pages == 2


def test_fewer_signatures_breaks_blank_tie():
    # 两段 [4,8]，容量 8：分两帖各 0 空白（共 0），合一帖也是 0 空白；
    # 空白相同取帖数少 -> 单帖
    result = impose_booklet(8, [4, 8], 2)
    assert result.total_blank_pages == 0
    assert result.signature_count == 1
    assert [s.end_page for s in result.signatures] == [8]


def test_blank_minimization_dominates_signature_count():
    # 6 页两段 [2,6]，容量 8：分两帖 2+2=4 空白，合一帖只补 2 空白 -> 单帖
    result = impose_booklet(6, [2, 6], 2)
    assert result.total_blank_pages == 2
    assert result.signature_count == 1


def test_three_sheets_outer_to_inner_ordering():
    # 12 页一帖、3 印张
    result = impose_booklet(12, [12], 3)
    sig = result.signatures[0]
    assert len(sig.sheets) == 3
    assert (sig.sheets[0].front_left, sig.sheets[0].front_right) == (12, 1)
    assert (sig.sheets[2].front_left, sig.sheets[2].front_right) == (8, 5)


@pytest.mark.parametrize("seed", range(300))
def test_random_partitions_match_brute_force(seed):
    rng = random.Random(seed)
    page_count = rng.randint(1, 14)
    # 合法曲段划分 = 在 1..page_count-1 中任选切点，末页恒为 page_count
    cuts = sorted(
        rng.sample(range(1, page_count), rng.randint(0, page_count - 1))
    )
    ends = cuts + [page_count]
    max_sheets = rng.randint(1, 4)

    # 若存在超容量单段，核心应报错
    boundaries = [0] + ends
    longest_segment = max(
        boundaries[i] - boundaries[i - 1] for i in range(1, len(boundaries))
    )
    if longest_segment > 4 * max_sheets:
        with pytest.raises(ImpositionError, match="超过每帖容量"):
            impose_booklet(page_count, ends, max_sheets)
        return

    result = impose_booklet(page_count, ends, max_sheets)
    ref = _brute_force(page_count, ends, max_sheets)
    assert ref is not None
    (ref_blanks, ref_sig_count, ref_ends), ref_sigs = ref
    assert result.total_blank_pages == ref_blanks
    assert result.signature_count == ref_sig_count
    assert [s.end_page for s in result.signatures] == list(ref_ends)
    assert [
        (s.start_page, s.end_page) for s in result.signatures
    ] == ref_sigs
    _check_invariants(result, page_count, ends, max_sheets)


def test_single_page_booklet():
    result = impose_booklet(1, [1], 1)
    assert result.signature_count == 1
    sig = result.signatures[0]
    assert sig.content_pages == 1
    assert sig.blank_pages == 3
    sheet = sig.sheets[0]
    # 局部 [4,1,2,3]：仅局部 1 是原谱
    assert (sheet.front_left, sheet.front_right) == (None, 1)
    assert (sheet.back_left, sheet.back_right) == (None, None)


def test_each_signature_holds_at_least_one_segment():
    # 两段 [4,8]、容量 4：只能各自一帖（每帖至少一段，不存在空帖）
    result = impose_booklet(8, [4, 8], 1)
    assert result.signature_count == 2
    assert [s.end_page for s in result.signatures] == [4, 8]


def test_page_count_at_limit_accepted():
    ends = list(range(1, MAX_PAGES + 1))
    result = impose_booklet(MAX_PAGES, ends, 1)
    assert result.signatures[-1].end_page == MAX_PAGES
    _check_invariants(result, MAX_PAGES, ends, 1)


def test_page_count_over_limit_rejected():
    with pytest.raises(ImpositionError, match="超过纸质拼版上限"):
        impose_booklet(MAX_PAGES + 1, [MAX_PAGES + 1], 200)


def test_500_pages_large_capacity_performance():
    # O(k²) 在 500 段下应快速完成
    ends = list(range(1, 501))
    result = impose_booklet(500, ends, 125)
    assert result.total_blank_pages == 0
    assert result.signature_count == 1


# ---------------------------------------------------------------- 输入校验


def test_empty_segments_rejected():
    with pytest.raises(ImpositionError, match="不能为空"):
        impose_booklet(4, [], 1)


@pytest.mark.parametrize(
    "ends,fragment",
    [
        ([2, 2], "严格递增"),
        ([2, 1], "严格递增"),
        ([3, 5], "末项"),
        ([0, 4], "正整数"),
        ([-1, 4], "正整数"),
        ([2.0, 4], "整数"),
        ([2, True], "整数"),
        ([2, False, 4], "整数"),
        (["2", 4], "整数"),
        ([None, 4], "整数"),
    ],
)
def test_invalid_end_pages(ends, fragment):
    with pytest.raises(ImpositionError) as exc:
        impose_booklet(4, ends, 1)
    assert fragment in str(exc.value)


@pytest.mark.parametrize("max_sheets", [0, -1])
def test_non_positive_max_sheets_rejected(max_sheets):
    with pytest.raises(ImpositionError, match="正整数"):
        impose_booklet(4, [4], max_sheets)


@pytest.mark.parametrize("max_sheets", [1.0, 1.5, True, False, "2", None])
def test_non_integer_max_sheets_rejected(max_sheets):
    with pytest.raises(ImpositionError, match="整数"):
        impose_booklet(4, [4], max_sheets)


def test_single_oversized_segment_rejected():
    with pytest.raises(ImpositionError, match="超过每帖容量") as exc:
        impose_booklet(10, [10], 1)
    assert "曲段不可跨帖" in str(exc.value)


def test_oversized_middle_segment_rejected():
    # 三段 [1..2] 2 页、[3..10] 8 页、[11] 1 页，容量 2 印张=8 页：
    # 第 2 段恰好 8 页可容纳；改用容量 1 印张使其超容量
    with pytest.raises(ImpositionError, match="第 2 个曲段"):
        impose_booklet(11, [2, 10, 11], 1)


def test_segment_count_exceeding_pages_rejected():
    # 3 个严格递增正整数末页至少占 3 页，方案只有 2 页
    with pytest.raises(ImpositionError, match="不能超过方案页数"):
        impose_booklet(2, [1, 2, 3], 1)


def test_invalid_page_count():
    with pytest.raises(ImpositionError):
        impose_booklet(0, [0], 1)
