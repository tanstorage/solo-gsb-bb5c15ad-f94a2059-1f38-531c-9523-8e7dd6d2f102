"""排练翻页提示核心的单元测试。运行：python -m pytest -q"""

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


def test_forward_backward_forward_through_repeated_segment():
    # 演奏序列：0(页1) 1(页1) 2(页2) 1遍2(页1) 2遍2(页2) 3(页2)
    # 提示出现在 1->2、2->1回跳、1遍2->2遍2 三处；结尾 2->3 同页无提示
    r = _two_page_rehearsal()
    cues = r.turn_cues(silences=[2, 5, 1, 0], min_turn_time=2)
    assert [(c.boundary_seconds, c.direction) for c in cues] == [
        (4.0, "forward"),
        (6.0, "backward"),
        (8.0, "forward"),
    ]

    first, backward, second_forward = cues

    assert first.from_page == 1 and first.to_page == 2
    assert (first.from_measure, first.from_playthrough) == (1, 1)
    assert (first.to_measure, first.to_playthrough) == (2, 1)
    assert first.silence_seconds == 5.0
    assert first.sufficient is True  # 5 >= 2

    # 回翻由重复段回跳（小节 2 遍1 -> 小节 1 遍2）引起，静默取小节 2 之后
    assert backward.from_page == 2 and backward.to_page == 1
    assert (backward.from_measure, backward.from_playthrough) == (2, 1)
    assert (backward.to_measure, backward.to_playthrough) == (1, 2)
    assert backward.silence_seconds == 1.0
    assert backward.sufficient is False  # 1 < 2

    assert second_forward.from_page == 1 and second_forward.to_page == 2
    assert (second_forward.from_measure, second_forward.from_playthrough) == (1, 2)
    assert (second_forward.to_measure, second_forward.to_playthrough) == (2, 2)
    assert second_forward.sufficient is True


def test_no_repeats_single_forward_cue_and_none_at_end():
    r = _two_page_rehearsal(repeats=[])
    cues = r.turn_cues([2, 5, 1, 0], 2)
    assert len(cues) == 1
    cue = cues[0]
    # 边界 = 小节 2 的开始时刻 = 2 小节 × 2 秒
    assert cue.boundary_seconds == pytest.approx(4.0)
    assert (cue.from_measure, cue.to_measure) == (1, 2)
    assert (cue.from_playthrough, cue.to_playthrough) == (1, 1)
    assert cue.direction == "forward"


def test_single_page_generates_no_cues():
    r = prepare_rehearsal(4, [4] * 4, 120, [(0, 3, 2)], [0])
    assert r.turn_cues([2, 5, 1, 0], 2) == []


def test_full_score_repeat_turns_back_at_score_end():
    # 重复段覆盖整篇：... 小节 3(页2) -> 小节 0 遍2(页1) 必须回翻
    r = prepare_rehearsal(4, [4] * 4, 120, [(0, 3, 2)], [0, 2])
    cues = r.turn_cues([2, 5, 1, 0], 2)
    assert [(c.boundary_seconds, c.from_page, c.to_page, c.direction) for c in cues] == [
        (4.0, 1, 2, "forward"),
        (8.0, 2, 1, "backward"),  # 3 遍1 -> 0 遍2
        (12.0, 1, 2, "forward"),
    ]
    jump = cues[1]
    assert (jump.from_measure, jump.from_playthrough) == (3, 1)
    assert (jump.to_measure, jump.to_playthrough) == (0, 2)


def test_boundary_seconds_follow_tempo_and_beats():
    # 60 BPM、每小节 3 拍 -> 每小节 3 秒；页 1：小节 0，页 2：小节 1..2
    r = prepare_rehearsal(3, [3, 3, 3], 60, [], [0, 1])
    cues = r.turn_cues([0.5, 9, 0], 1)
    assert len(cues) == 1
    assert cues[0].boundary_seconds == pytest.approx(3.0)
    # 静默 0.5 < 1：不充足
    assert cues[0].silence_seconds == 0.5
    assert cues[0].sufficient is False


def test_sufficiency_boundary_equality_is_sufficient():
    r = prepare_rehearsal(2, [4, 4], 120, [], [0, 1])
    cues = r.turn_cues([2.0, 0], 2.0)
    assert len(cues) == 1
    assert cues[0].silence_seconds == 2.0
    assert cues[0].sufficient is True  # 恰好等于最低翻页时间


def test_min_turn_time_zero_makes_any_silence_sufficient():
    r = _two_page_rehearsal()
    cues = r.turn_cues([2, 5, 1, 0], 0)
    assert all(c.sufficient for c in cues)


def test_silence_length_mismatch_raises():
    r = _two_page_rehearsal()
    with pytest.raises(RehearsalError, match="静默秒数数组长度"):
        r.turn_cues([2, 5, 1], 2)
