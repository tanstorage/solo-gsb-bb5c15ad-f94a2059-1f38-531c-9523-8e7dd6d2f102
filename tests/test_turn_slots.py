"""排练翻页空档核心的单元测试。运行：python -m pytest -q"""

from __future__ import annotations

import pytest

from app.rehearsal import RehearsalError, prepare_rehearsal


def _two_page_rehearsal(repeats=((1, 2, 2),)):
    # n=4，每小节 4 拍，120 BPM -> 每小节 2 秒
    # 页 1：小节 0..1，页 2：小节 2..3
    return prepare_rehearsal(
        measure_count=4,
        beats_per_measure=[4, 4, 4, 4],
        tempo_bpm=120,
        repeats=list(repeats),
        page_starts=(0, 2),
    )


def _single_boundary_rehearsal():
    # n=2，每小节 4 拍，120 BPM -> 每小节 2 秒；页 1：小节 0，页 2：小节 1
    return prepare_rehearsal(
        measure_count=2,
        beats_per_measure=[4, 4],
        tempo_bpm=120,
        repeats=[],
        page_starts=(0, 1),
    )


def test_slots_through_repeated_segment():
    # 演奏序列：0(页1) 1(页1) 2(页2) 1遍2(页1) 2遍2(页2) 3(页2)
    # 跨页边界：4.0（前一小节 1 遍1）、6.0（前一小节 2 遍1）、8.0（前一小节 1 遍2）
    r = _two_page_rehearsal()
    slots = r.turn_slots(
        hands_busy=[
            ([], []),                      # 小节 0：不参与任何边界
            ([[0, 3]], [[1, 2]]),          # 小节 1：左手闲 [3,4)，右手闲 [0,1)、[2,4)
            ([], [[0, 4]]),                # 小节 2：左手全闲，右手全忙
            ([], []),                      # 小节 3：不参与任何边界
        ],
        turn_seconds=1.0,
    )
    assert [s.boundary_seconds for s in slots] == [4.0, 6.0, 8.0]

    first, backward, second_forward = slots

    # 小节 1：左手空闲仅 0.5 秒容不下 1 秒动作；右手 [2,4) 恰容下，最晚开始 1.0
    assert (first.from_measure, first.from_playthrough) == (1, 1)
    assert (first.to_measure, first.to_playthrough) == (2, 1)
    assert (first.from_page, first.to_page) == (1, 2)
    assert first.feasible is True
    assert first.hand == "right"
    assert first.action_start_seconds == pytest.approx(3.0)
    assert first.action_end_seconds == pytest.approx(4.0)

    # 回跳边界：前一小节 2 遍1 左手全闲，最晚开始 1.0（动作 [5.0, 6.0]）
    assert (backward.from_measure, backward.from_playthrough) == (2, 1)
    assert (backward.to_measure, backward.to_playthrough) == (1, 2)
    assert (backward.from_page, backward.to_page) == (2, 1)
    assert backward.feasible is True
    assert backward.hand == "left"
    assert backward.action_start_seconds == pytest.approx(5.0)
    assert backward.action_end_seconds == pytest.approx(6.0)

    # 第二遍的小节 1 复用同一小节的忙碌区间
    assert (second_forward.from_measure, second_forward.from_playthrough) == (1, 2)
    assert second_forward.hand == "right"
    assert second_forward.action_start_seconds == pytest.approx(7.0)
    assert second_forward.action_end_seconds == pytest.approx(8.0)


def test_action_stays_within_preceding_measure():
    r = _two_page_rehearsal()
    slots = r.turn_slots(
        hands_busy=[([], []), ([], []), ([], []), ([], [])],
        turn_seconds=1.5,
    )
    assert len(slots) == 3
    for slot in slots:
        assert slot.feasible is True
        assert slot.action_end_seconds <= slot.boundary_seconds + 1e-9


def test_latest_window_wins():
    # 左手忙碌 [2,3)：空闲 [0,2)（1 秒）与 [3,4)（0.5 秒），0.5 秒动作选后者
    r = _single_boundary_rehearsal()
    (slot,) = r.turn_slots(
        hands_busy=[([[2, 3]], []), ([], [])],
        turn_seconds=0.5,
    )
    assert slot.hand == "left"
    assert slot.action_start_seconds == pytest.approx(1.5)
    assert slot.action_end_seconds == pytest.approx(2.0)


def test_left_preferred_on_tie():
    # 双手同样全闲：最晚开始时刻相同，优先左手
    r = _single_boundary_rehearsal()
    (slot,) = r.turn_slots(hands_busy=[([], []), ([], [])], turn_seconds=1.0)
    assert slot.feasible is True
    assert slot.hand == "left"
    assert slot.action_start_seconds == pytest.approx(1.0)
    assert slot.action_end_seconds == pytest.approx(2.0)


def test_right_chosen_when_strictly_later():
    # 左手空闲 [0,2)（最晚开始 0.0），右手全闲（最晚开始 1.0）：选右手
    r = _single_boundary_rehearsal()
    (slot,) = r.turn_slots(
        hands_busy=[([[2, 4]], []), ([], [])],
        turn_seconds=1.0,
    )
    assert slot.hand == "right"
    assert slot.action_start_seconds == pytest.approx(1.0)


def test_unsorted_and_overlapping_intervals_are_merged():
    # 乱序 + 重叠：[3,4)、[0,1)、[0.5,2) 合并为 [0,2)、[3,4)，空闲 [2,3)
    r = _single_boundary_rehearsal()
    (slot,) = r.turn_slots(
        hands_busy=[([[3, 4], [0, 1], [0.5, 2]], [[0, 4]]), ([], [])],
        turn_seconds=0.5,
    )
    assert slot.hand == "left"
    assert slot.action_start_seconds == pytest.approx(1.0)
    assert slot.action_end_seconds == pytest.approx(1.5)


def test_window_exactly_turn_seconds_fits():
    # 空闲窗时长恰等于 turn_seconds：可容纳，动作占满整窗
    r = _single_boundary_rehearsal()
    (slot,) = r.turn_slots(
        hands_busy=[([[0, 2]], []), ([], [])],  # 左手闲 [2,4) = 1 秒
        turn_seconds=1.0,
    )
    assert slot.feasible is True
    assert slot.hand == "left"
    assert slot.action_start_seconds == pytest.approx(1.0)
    assert slot.action_end_seconds == pytest.approx(2.0)


def test_infeasible_boundary_is_kept_and_marked():
    # 双手全忙：边界保留，标记不可行，动作字段为 None
    r = _single_boundary_rehearsal()
    (slot,) = r.turn_slots(
        hands_busy=[([[0, 4]], [[0, 4]]), ([], [])],
        turn_seconds=1.0,
    )
    assert slot.boundary_seconds == pytest.approx(2.0)
    assert slot.feasible is False
    assert slot.hand is None
    assert slot.action_start_seconds is None
    assert slot.action_end_seconds is None


def test_turn_longer_than_measure_is_infeasible():
    r = _single_boundary_rehearsal()
    (slot,) = r.turn_slots(hands_busy=[([], []), ([], [])], turn_seconds=2.5)
    assert slot.feasible is False
    assert slot.hand is None


def test_single_page_generates_no_slots():
    r = prepare_rehearsal(4, [4] * 4, 120, [(0, 3, 2)], [0])
    assert r.turn_slots([([], [])] * 4, 1.0) == []


def test_no_repeats_single_boundary():
    r = _two_page_rehearsal(repeats=[])
    slots = r.turn_slots([([], []), ([], [[0, 4]]), ([], []), ([], [])], 1.0)
    assert len(slots) == 1
    slot = slots[0]
    assert slot.boundary_seconds == pytest.approx(4.0)
    assert (slot.from_measure, slot.to_measure) == (1, 2)
    assert (slot.from_playthrough, slot.to_playthrough) == (1, 1)
    assert slot.hand == "left"
    assert slot.action_start_seconds == pytest.approx(3.0)


def test_slots_follow_tempo_and_beats():
    # 60 BPM、每小节 3 拍 -> 每拍 1 秒、每小节 3 秒；页 1：小节 0，页 2：小节 1
    r = prepare_rehearsal(2, [3, 3], 60, [], [0, 1])
    (slot,) = r.turn_slots(
        hands_busy=[([[0, 1]], []), ([], [])],  # 左手闲 [1,3) = 2 秒
        turn_seconds=1.5,
    )
    assert slot.boundary_seconds == pytest.approx(3.0)
    assert slot.hand == "left"
    assert slot.action_start_seconds == pytest.approx(1.5)
    assert slot.action_end_seconds == pytest.approx(3.0)


def test_hands_busy_length_mismatch():
    r = _single_boundary_rehearsal()
    with pytest.raises(RehearsalError, match="hands_busy 数组长度"):
        r.turn_slots([([], [])], 1.0)


def test_invalid_turn_seconds():
    r = _single_boundary_rehearsal()
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(RehearsalError, match="turn_seconds"):
            r.turn_slots([([], []), ([], [])], bad)


def test_invalid_intervals():
    r = _single_boundary_rehearsal()
    # 起拍为负
    with pytest.raises(RehearsalError, match="0 ≤ 起拍 < 止拍"):
        r.turn_slots([([[-0.5, 2]], []), ([], [])], 1.0)
    # 起拍不小于止拍
    with pytest.raises(RehearsalError, match="0 ≤ 起拍 < 止拍"):
        r.turn_slots([([[2, 2]], []), ([], [])], 1.0)
    with pytest.raises(RehearsalError, match="0 ≤ 起拍 < 止拍"):
        r.turn_slots([([], [[3, 1]]), ([], [])], 1.0)
    # 止拍超过该小节拍数
    with pytest.raises(RehearsalError, match="超过该小节拍数"):
        r.turn_slots([([[0, 5]], []), ([], [])], 1.0)
    # 端点非有限数值
    with pytest.raises(RehearsalError, match="有限数值"):
        r.turn_slots([([[0, float("nan")]], []), ([], [])], 1.0)
    with pytest.raises(RehearsalError, match="有限数值"):
        r.turn_slots([([], [[float("inf"), 4]]), ([], [])], 1.0)
