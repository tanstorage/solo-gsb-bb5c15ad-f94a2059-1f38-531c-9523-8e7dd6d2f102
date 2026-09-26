"""排练录音时间戳定位核心的单元测试。运行：python -m pytest -q"""

from __future__ import annotations

import pytest

from app.rehearsal import (
    PlayedMeasure,
    RehearsalError,
    expand_repeats,
    prepare_rehearsal,
)


def _ids(events):
    return [(e.measure, e.playthrough) for e in events]


def test_expand_without_repeats():
    events = expand_repeats(4, [])
    assert _ids(events) == [(0, 1), (1, 1), (2, 1), (3, 1)]


def test_expand_single_repeated_segment():
    # n=4，重复小节 1..2 演奏 2 遍：0，1，2，1，2，3
    events = expand_repeats(4, [(1, 2, 2)])
    assert _ids(events) == [
        (0, 1),
        (1, 1), (2, 1),
        (1, 2), (2, 2),
        (3, 1),
    ]


def test_expand_repeats_in_score_order_even_if_submitted_unsorted():
    # n=5：小节 0 单独重复 2 遍；小节 2..3 重复 3 遍
    events = expand_repeats(5, [(2, 3, 3), (0, 0, 2)])
    assert _ids(events) == [
        (0, 1), (0, 2),
        (1, 1),
        (2, 1), (3, 1),
        (2, 2), (3, 2),
        (2, 3), (3, 3),
        (4, 1),
    ]


def test_expand_four_playthroughs():
    events = expand_repeats(1, [(0, 0, 4)])
    assert _ids(events) == [(0, 1), (0, 2), (0, 3), (0, 4)]


def test_repeat_play_count_out_of_range():
    for times in (1, 5):
        with pytest.raises(RehearsalError, match="演奏次数"):
            expand_repeats(3, [(0, 1, times)])


def test_repeat_measures_out_of_range():
    with pytest.raises(RehearsalError, match="不合法"):
        expand_repeats(3, [(0, 3, 2)])
    with pytest.raises(RehearsalError, match="不合法"):
        expand_repeats(3, [(-1, 1, 2)])
    with pytest.raises(RehearsalError, match="起始小节"):
        expand_repeats(3, [(2, 1, 2)])


def test_overlapping_and_nested_repeats_rejected():
    with pytest.raises(RehearsalError, match="重叠或嵌套"):
        expand_repeats(6, [(0, 2, 2), (1, 3, 2)])  # 部分重叠
    with pytest.raises(RehearsalError, match="重叠或嵌套"):
        expand_repeats(6, [(0, 4, 2), (1, 2, 3)])  # 嵌套
    with pytest.raises(RehearsalError, match="重叠或嵌套"):
        expand_repeats(6, [(0, 2, 2), (2, 3, 2)])  # 共享小节 2
    with pytest.raises(RehearsalError, match="重叠或嵌套"):
        expand_repeats(6, [(1, 1, 2), (1, 1, 2)])  # 完全相同


def test_adjacent_repeats_are_allowed():
    # 首尾相邻（前段末小节 + 1 = 后段首小节）允许
    events = expand_repeats(4, [(0, 1, 2), (2, 3, 2)])
    assert _ids(events) == [
        (0, 1), (1, 1), (0, 2), (1, 2),
        (2, 1), (3, 1), (2, 2), (3, 2),
    ]


def test_beats_length_mismatch():
    with pytest.raises(RehearsalError, match="数组长度"):
        prepare_rehearsal(4, [4, 4, 4], 120, [], [0])


def test_invalid_beats_and_tempo():
    with pytest.raises(RehearsalError, match="拍数必须为正数"):
        prepare_rehearsal(2, [4, 0], 120, [], [0])
    with pytest.raises(RehearsalError, match="每分钟拍数必须为正数"):
        prepare_rehearsal(2, [4, 4], 0, [], [0])
    with pytest.raises(RehearsalError, match="有限数值"):
        prepare_rehearsal(2, [4, float("nan")], 120, [], [0])


def _rehearsal(repeats=((1, 2, 2),), page_starts=(0, 3)):
    # n=4，每小节 4 拍，120 BPM -> 每小节 2 秒
    return prepare_rehearsal(
        measure_count=4,
        beats_per_measure=[4, 4, 4, 4],
        tempo_bpm=120,
        repeats=list(repeats),
        page_starts=page_starts,
    )


def test_total_duration_counts_every_playthrough():
    r = _rehearsal()
    # 原谱 4 小节 + 重复段内 2 小节各多奏 1 遍 = 6 次发声 × 2 秒
    assert r.total_duration == pytest.approx(12.0)
    assert r.start_times == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]


def test_locate_basic_mapping():
    r = _rehearsal(page_starts=(0, 2))  # 页 1：小节 0..1，页 2：小节 2..3
    cases = {
        0.0: (0, 1, 0.0, 1),
        1.0: (0, 1, 2.0, 1),       # 1 秒 = 2 拍偏移
        2.0: (1, 1, 0.0, 1),       # 恰在边界归下一小节
        3.5: (1, 1, 3.0, 1),
        4.0: (2, 1, 0.0, 2),
        5.999: (2, 1, pytest.approx(3.998), 2),
        6.0: (1, 2, 0.0, 1),       # 第二遍开始，回到小节 1
        7.0: (1, 2, 2.0, 1),
        8.0: (2, 2, 0.0, 2),
        9.5: (2, 2, 3.0, 2),
        10.0: (3, 1, 0.0, 2),
        11.5: (3, 1, 3.0, 2),
    }
    for ts, (m, play, beat, page) in cases.items():
        loc = r.locate(ts)
        assert (loc.measure, loc.playthrough, loc.beat_offset, loc.page_index) == (
            m, play, beat, page
        ), f"ts={ts}"


def test_locate_no_repeats_playthrough_is_one():
    r = _rehearsal(repeats=[], page_starts=(0,))
    assert r.total_duration == pytest.approx(8.0)
    for ts, measure in [(0.0, 0), (2.0, 1), (4.0, 2), (6.0, 3)]:
        loc = r.locate(ts)
        assert loc.measure == measure
        assert loc.playthrough == 1


def test_locate_beat_offset_with_mixed_beats_and_tempo():
    # n=2：小节 0 为 3 拍、小节 1 为 4 拍，60 BPM -> 每拍 1 秒
    r = prepare_rehearsal(2, [3, 4], 60, [], [0])
    assert r.total_duration == pytest.approx(7.0)
    assert r.locate(2.5).beat_offset == pytest.approx(2.5)
    loc = r.locate(4.0)  # 进入小节 1 整 1 拍
    assert loc.measure == 1
    assert loc.beat_offset == pytest.approx(1.0)
    assert r.locate(6.25).beat_offset == pytest.approx(3.25)


def test_locate_boundary_timestamp_belongs_to_next_measure():
    r = _rehearsal()
    # 8.0 = 第二遍小节 2 的起点（而不是小节 1 的尾音）
    loc = r.locate(8.0)
    assert (loc.measure, loc.playthrough) == (2, 2)
    assert loc.beat_offset == 0.0


def test_locate_at_or_beyond_total_duration_rejected():
    r = _rehearsal()
    with pytest.raises(RehearsalError, match="超出演奏总时长"):
        r.locate(12.0)
    with pytest.raises(RehearsalError, match="超出演奏总时长"):
        r.locate(12.5)
    with pytest.raises(RehearsalError, match="不能为负数"):
        r.locate(-0.5)


def test_locate_float_total_duration_boundary():
    # 180/70 为非整小数；恰好等于计算出的总时长仍不可定位
    r = prepare_rehearsal(1, [3], 70, [], [0])
    total = 3 * 60.0 / 70.0
    assert r.total_duration == pytest.approx(total)
    with pytest.raises(RehearsalError, match="超出演奏总时长"):
        r.locate(total)
    loc = r.locate(total - 1e-7)
    assert loc.measure == 0
    assert loc.beat_offset > 0


def test_locate_all_preserves_order_and_reports_index():
    r = _rehearsal()
    locs = r.locate_all([0.0, 2.0, 10.0])
    assert [loc.measure for loc in locs] == [0, 1, 3]

    with pytest.raises(RehearsalError, match="第 2 个时间戳"):
        r.locate_all([0.0, 99.0])
    with pytest.raises(RehearsalError, match="第 1 个时间戳"):
        r.locate_all([-1.0])


def test_empty_timestamps_allowed():
    r = _rehearsal()
    assert r.locate_all([]) == []


def test_page_mapping_across_pages():
    # 三页：0..1 / 2..3 / 4
    r = prepare_rehearsal(5, [4] * 5, 120, [], [0, 2, 4])
    assert [r.locate(ts).page_index for ts in (0.0, 2.0, 4.0, 6.0, 8.0)] == [
        1, 1, 2, 2, 3
    ]
