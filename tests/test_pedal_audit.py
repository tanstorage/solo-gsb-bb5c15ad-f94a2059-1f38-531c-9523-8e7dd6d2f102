"""排练踏板日志校对核心的单元测试。运行：python -m pytest -q"""

from __future__ import annotations

from bisect import bisect_right
from fractions import Fraction

import pytest

from app.pedal_audit import (
    MAX_EVENTS,
    PedalAuditError,
    PedalEvent,
    audit_pedal_log,
)
from app.rehearsal import RehearsalError, expand_repeats


def _events(pairs):
    """pairs: [(秒数, 操作后页码), ...]"""
    return [PedalEvent(seconds=t, page_after=p) for t, p in pairs]


def _audit(beats, pages, event_tp, tol, repeats=(), tempo=60.0):
    """构造方案并校对（默认 60 BPM，1 拍 = 1 秒，拍数数值即秒数）。

    beats: 每小节拍数；pages: 每个原谱小节所在页（1 基、非降序、从 1 连续）；
    repeats: [(起始, 结束, 遍数), ...]（0 基含端点）。
    """
    page_starts = [i for i in range(len(pages)) if i == 0 or pages[i] != pages[i - 1]]
    return audit_pedal_log(
        measure_count=len(beats),
        beats_per_measure=beats,
        tempo_bpm=tempo,
        repeats=list(repeats),
        page_starts=page_starts,
        page_count=max(pages),
        events=_events(event_tp),
        tolerance_seconds=tol,
    )


def _matched_indices(result):
    return tuple(
        a.pedal_event_index for a in result.cue_audits if a.matched
    )


# 经典三提示场景：页 1 = 小节 0..1、页 2 = 小节 2..3，小节 1..2 重复两遍；
# 拍数 [2, 2, 2, 90]（60 BPM 下即秒数）→ 演奏序列 0,1,2,1,2,3，
# 应翻提示：4 秒（1→2）、6 秒（2→1 回翻）、8 秒（1→2），总时长 100 秒
BACKWARD_BEATS = [2.0, 2.0, 2.0, 90.0]
BACKWARD_PAGES = [1, 1, 2, 2]
BACKWARD_REPEATS = [(1, 2, 2)]


def _backward_audit(event_tp, tol):
    return _audit(
        BACKWARD_BEATS, BACKWARD_PAGES, event_tp, tol, repeats=BACKWARD_REPEATS
    )


def test_perfect_matching():
    r = _backward_audit([(4.1, 2), (6.0, 1), (8.2, 2)], 0.5)
    assert r.total_duration == 100.0
    assert r.total_error_count == 0
    assert r.missed_count == 0
    assert r.extra_count == 0
    assert r.extra_turns == []
    assert _matched_indices(r) == (0, 1, 2)
    assert r.total_time_diff_seconds == pytest.approx(0.3)
    assert all(a.matched for a in r.cue_audits)
    # 回显提示时刻与前后页（含重复段回跳造成的回翻提示）
    assert [a.expected_seconds for a in r.cue_audits] == [4.0, 6.0, 8.0]
    assert [a.to_page for a in r.cue_audits] == [2, 1, 2]
    assert [a.from_page for a in r.cue_audits] == [1, 2, 1]


def test_missed_and_extra_counts():
    # 三条提示；事件 0 目标页只与 6 秒回翻提示相同但时间差 2 秒超容差，
    # 事件 1 与 8 秒提示差 1 秒也超容差：3 漏翻 + 2 多翻
    r = _backward_audit([(4.0, 1), (9.0, 2)], 0.5)
    assert r.total_error_count == 5
    assert r.missed_count == 3
    assert r.extra_count == 2
    assert r.extra_turns == [0, 1]
    assert all(not a.matched for a in r.cue_audits)
    assert r.total_time_diff_seconds == 0.0


def test_page_must_match_even_if_time_close():
    # 时间恰好一致但目标页不同，不得匹配
    r = _audit([4.0, 96.0], [1, 2], [(4.0, 1)], 1.0)
    assert r.total_error_count == 2
    assert not r.cue_audits[0].matched


def test_tolerance_boundary_inclusive():
    # 时间差恰好等于容差允许匹配
    r = _audit([4.0, 96.0], [1, 2], [(4.5, 2)], 0.5)
    assert r.total_error_count == 0
    assert r.cue_audits[0].matched
    assert r.cue_audits[0].time_diff_seconds == pytest.approx(0.5)
    # 略超容差不匹配
    r2 = _audit([4.0, 96.0], [1, 2], [(4.5 + 1e-6, 2)], 0.5)
    assert r2.total_error_count == 2
    # 哪怕只超 5e-10 也不匹配（精确比较，无 epsilon 放行）
    r3 = _audit([4.0, 96.0], [1, 2], [(4.5000000005, 2)], 0.5)
    assert r3.total_error_count == 2


def test_one_to_one_global_alignment():
    # 两条提示、三个候选事件；一对一全局对齐，中间事件为多翻
    # 提示 10→2、20→3；事件 10→2、11→2、20→3，容差 1
    r = _audit(
        [10.0, 10.0, 80.0],
        [1, 2, 3],
        [(10.0, 2), (11.0, 2), (20.0, 3)],
        1.0,
    )
    assert r.total_error_count == 1  # 两次匹配 + 一次多翻
    assert _matched_indices(r) == (0, 2)
    assert r.extra_turns == [1]


def test_primary_goal_maximizes_matches_over_time_sum():
    # 全局优先匹配数最多：提示 10→2、12→3；事件 10→2、11.9→3，容差 2
    # 两条都匹配（时间差和 0.1）优于只匹配第一条（时间差 0）
    r = _audit([10.0, 2.0, 88.0], [1, 2, 3], [(10.0, 2), (11.9, 3)], 2.0)
    assert r.total_error_count == 0
    assert _matched_indices(r) == (0, 1)
    assert r.total_time_diff_seconds == pytest.approx(0.1)


def test_secondary_goal_minimizes_time_diff_sum():
    # 同样匹配数下取时间差之和更小的对齐
    # 提示 10→2、20→3；事件 9.0 与 9.9 都只在提示 10 的容差内，
    # 最多匹配 1 条：取差 0.1 的事件 1，提示 20 漏翻、事件 0 多翻
    r = _audit([10.0, 10.0, 80.0], [1, 2, 3], [(9.0, 2), (9.9, 2)], 1.5)
    assert r.total_error_count == 2
    assert _matched_indices(r) == (1,)
    assert r.total_time_diff_seconds == pytest.approx(0.1)
    assert r.extra_turns == [0]


def test_tertiary_goal_lexicographic_min_event_indices():
    # 漏翻+多翻与时间差之和都相同，取匹配事件序号序列字典序最小
    # 提示 10→2、20→3；同刻两条 10→2 事件、一条 20→3，容差 0
    # 提示 10 可匹配事件 0 或 1（时间差均 0），字典序取事件 0
    r = _audit(
        [10.0, 10.0, 80.0],
        [1, 2, 3],
        [(10.0, 2), (10.0, 2), (20.0, 3)],
        0.0,
    )
    assert r.total_error_count == 1
    assert _matched_indices(r) == (0, 2)
    assert r.extra_turns == [1]


def test_zero_tolerance_uses_exact_decimal_comparison():
    # 提示时刻由 0.1 + 0.2 拍精确重建为 0.3 秒（不经浮点累加）；
    # 容差 0 时事件 0.3000000005 不得误匹配（无 epsilon 放行）
    r = _audit([0.1, 0.2, 0.7], [1, 1, 2], [(0.3000000005, 2)], 0.0)
    assert r.total_error_count == 2
    assert not r.cue_audits[0].matched
    assert r.extra_turns == [0]
    # 恰好 0.3 才匹配，时间差精确为 0
    r2 = _audit([0.1, 0.2, 0.7], [1, 1, 2], [(0.3, 2)], 0.0)
    assert r2.total_error_count == 0
    assert r2.cue_audits[0].matched
    assert r2.cue_audits[0].expected_seconds == 0.3
    assert r2.cue_audits[0].time_diff_seconds == 0.0


def test_equal_time_diff_tie_picks_earliest_event():
    # 提示 0.4 秒，同目标页事件 0.3 与 0.5：两侧时间差精确相等（均为 0.1），
    # 等成本取事件序号序列字典序最小 → 匹配事件 0（0.3 秒），事件 1 为多翻
    r = _audit([0.4, 0.6], [1, 2], [(0.3, 2), (0.5, 2)], 0.1)
    assert r.total_error_count == 1
    assert r.cue_audits[0].matched
    assert r.cue_audits[0].pedal_event_index == 0
    assert r.cue_audits[0].time_diff_seconds == 0.1
    assert r.extra_turns == [1]
    assert r.total_time_diff_seconds == 0.1


def test_empty_events_all_cues_missed():
    r = _audit([4.0, 2.0, 94.0], [1, 2, 3], [], 1.0)
    assert r.total_error_count == 2
    assert r.missed_count == 2
    assert r.extra_count == 0
    assert all(not a.matched for a in r.cue_audits)


def test_empty_cues_all_events_extra():
    r = _audit([1.0, 1.0], [1, 1], [(0.5, 1), (1.5, 1)], 1.0)
    assert r.cue_audits == []
    assert r.extra_turns == [0, 1]
    assert r.extra_count == 2
    assert r.total_error_count == 2
    assert r.total_time_diff_seconds == 0.0


def test_events_non_descending_equal_times_allowed():
    # 时间非降序：相等允许；两条同刻事件只有一条能匹配，字典序取事件 0
    r = _audit([10.0, 90.0], [1, 2], [(5.0, 2), (5.0, 2)], 10.0)
    assert _matched_indices(r) == (0,)
    assert r.extra_turns == [1]


def test_event_at_total_duration_rejected():
    with pytest.raises(PedalAuditError, match="越出演奏时长"):
        _audit([12.0], [1], [(12.0, 1)], 0.0)


def test_event_beyond_total_duration_rejected():
    with pytest.raises(PedalAuditError, match="越出演奏时长"):
        _audit([12.0], [1], [(12.5, 1)], 0.0)


def test_event_slightly_before_total_duration_accepted():
    # 总时长精确 0.3 秒；事件 0.2999999999 略早于终点 → 接受（无 epsilon 误杀）
    r = _audit([0.3], [1], [(0.2999999999, 1)], 0.0)
    assert r.extra_turns == [0]
    assert r.total_error_count == 1


def test_negative_seconds_rejected():
    with pytest.raises(PedalAuditError, match="不能为负数"):
        _audit([12.0], [1], [(-0.1, 1)], 0.0)


def test_page_out_of_range_rejected():
    with pytest.raises(PedalAuditError, match="越出页码范围"):
        _audit([1.0], [1], [(0.5, 2)], 0.0)
    with pytest.raises(PedalAuditError, match="越出页码范围"):
        _audit([1.0], [1], [(0.5, 0)], 0.0)


def test_descending_events_rejected():
    with pytest.raises(PedalAuditError, match="非降序"):
        _audit([10.0], [1], [(5.0, 1), (4.0, 1)], 0.0)


def test_slightly_descending_events_rejected():
    # 略逆序（1e-10）同样拒绝：非降序按精确十进制比较，无 epsilon 放行
    with pytest.raises(PedalAuditError, match="非降序"):
        _audit([10.0], [1], [(4.0000000001, 1), (4.0, 1)], 0.0)


def test_negative_and_non_finite_tolerance_rejected():
    with pytest.raises(PedalAuditError, match="不能为负数"):
        _audit([1.0], [1], [], -0.1)
    with pytest.raises(PedalAuditError, match="有限数值"):
        _audit([1.0], [1], [], float("inf"))
    with pytest.raises(PedalAuditError, match="有限数值"):
        _audit([1.0], [1], [], float("nan"))


def test_non_finite_and_bool_event_fields_rejected():
    with pytest.raises(PedalAuditError, match="有限数值"):
        _audit([1.0], [1], [(float("nan"), 1)], 0.0)
    with pytest.raises(PedalAuditError, match="整数"):
        audit_pedal_log(
            measure_count=1,
            beats_per_measure=[1.0],
            tempo_bpm=60.0,
            repeats=[],
            page_starts=[0],
            page_count=1,
            events=[PedalEvent(0.5, True)],  # type: ignore[arg-type]
            tolerance_seconds=0.0,
        )


def test_rehearsal_params_validated():
    # 拍数长度与规划小节数不符
    with pytest.raises(PedalAuditError, match="数组长度"):
        audit_pedal_log(
            measure_count=2,
            beats_per_measure=[1.0],
            tempo_bpm=60.0,
            repeats=[],
            page_starts=[0],
            page_count=1,
            events=[],
            tolerance_seconds=0.0,
        )
    # 拍数必须为正
    with pytest.raises(PedalAuditError, match="必须为正数"):
        _audit([0.0], [1], [], 0.0)
    # 速度必须为正
    with pytest.raises(PedalAuditError, match="必须为正数"):
        _audit([1.0], [1], [], 0.0, tempo=0.0)
    # 重复段重叠（沿用排练展开的校验）
    with pytest.raises(RehearsalError, match="重叠或嵌套"):
        _audit(
            [1.0, 1.0, 1.0, 1.0],
            [1, 1, 1, 1],
            [],
            0.0,
            repeats=[(0, 2, 2), (1, 3, 2)],
        )


def test_too_many_events_rejected():
    events = _events([(float(i % 50), 1) for i in range(MAX_EVENTS + 1)])
    with pytest.raises(PedalAuditError, match=f"超过上限 {MAX_EVENTS}"):
        audit_pedal_log(
            measure_count=1,
            beats_per_measure=[1.0],
            tempo_bpm=60.0,
            repeats=[],
            page_starts=[0],
            page_count=1,
            events=events,
            tolerance_seconds=0.0,
        )


def test_result_is_deterministic():
    events = [(4.1, 2), (6.0, 1), (8.2, 2)]
    r1 = _backward_audit(events, 0.5)
    r2 = _backward_audit(events, 0.5)
    assert _matched_indices(r1) == _matched_indices(r2)
    assert r1.total_time_diff_seconds == r2.total_time_diff_seconds


def test_backward_cue_target_page_drives_match():
    # 重复段回跳造成的回翻提示目标页较小（to_page=1）；
    # 只有操作后页码=1 的事件能匹配
    r = _backward_audit([(6.05, 1)], 0.5)
    # 仅中间回翻提示匹配唯一一条事件（事件序号 0），其余漏翻
    assert _matched_indices(r) == (0,)
    mid = r.cue_audits[1]
    assert mid.from_page == 2 and mid.to_page == 1
    assert mid.pedal_event_index == 0
    assert mid.pedal_seconds == pytest.approx(6.05)


# ---------------------------------------------------------------- 随机暴力对照


def _exact_cues(beats, tempo, repeats, page_starts):
    """测试侧独立重建精确提示（只用公共积木，不调用被测模块内部函数）。"""
    tempo_exact = Fraction(str(tempo))
    durations = [Fraction(str(b)) * 60 / tempo_exact for b in beats]
    cues = []
    elapsed = Fraction(0)
    prev_page = None
    for played in expand_repeats(len(beats), repeats):
        page = bisect_right(page_starts, played.measure)
        if prev_page is not None and page != prev_page:
            cues.append((elapsed, prev_page, page))
        elapsed += durations[played.measure]
        prev_page = page
    return cues, elapsed


def _brute_force(cues, events, tol):
    """全元组后缀 DP：(漏翻+多翻, 时间差和, 匹配事件序号元组) 整体字典序。

    cues: [(精确时刻, 翻页前页, 目标页)]；events: [(精确时刻, 操作后页码)]；
    全部有理数精确比较。
    """
    m, n = len(cues), len(events)
    zero = Fraction(0)
    dp = [[None] * (n + 1) for _ in range(m + 1)]
    dp[m][n] = (0, zero, ())
    for i in range(m):
        dp[i][n] = (m - i, zero, ())
    for j in range(n + 1):
        dp[m][j] = (n - j, zero, ())
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            candidates = [
                (dp[i + 1][j][0] + 1, dp[i + 1][j][1], dp[i + 1][j][2]),
                (dp[i][j + 1][0] + 1, dp[i][j + 1][1], dp[i][j + 1][2]),
            ]
            ct, _, tp = cues[i]
            et, ep = events[j]
            if tp == ep and abs(ct - et) <= tol:
                g, s, t = dp[i + 1][j + 1]
                candidates.append((g, s + abs(ct - et), (j,) + t))
            dp[i][j] = min(candidates, key=lambda x: (x[0], x[1], x[2]))
    return dp[0][0]


@pytest.mark.parametrize("seed", range(300))
def test_random_alignment_matches_brute_force(seed):
    import random

    rng = random.Random(seed)
    measure_count = rng.randint(1, 6)
    beats = [round(rng.uniform(0.5, 8.0), 2) for _ in range(measure_count)]
    tempo = rng.choice([60.0, 90.0, 120.0])
    # 随机分页：从 1..measure_count-1 中选若干断页位置
    breaks = sorted(
        rng.sample(range(1, measure_count), rng.randint(0, measure_count - 1))
    )
    page_starts = [0] + breaks
    page_count = len(page_starts)
    repeats = []
    if rng.random() < 0.5:
        a = rng.randrange(measure_count)
        b = min(measure_count - 1, a + rng.randint(0, 2))
        repeats = [(a, b, rng.randint(2, 3))]

    cues_exact, total_exact = _exact_cues(beats, tempo, repeats, page_starts)

    n = rng.randint(0, 8)
    total_f = float(total_exact)
    event_tp = []
    for _ in range(n):
        t = round(rng.uniform(0, total_f * 0.999), 2)
        if Fraction(str(t)) < total_exact:
            event_tp.append((t, rng.randint(1, page_count)))
    event_tp.sort(key=lambda e: e[0])
    tol = rng.choice([0.0, 0.5, 1.0, 3.0, 40.0])

    r = audit_pedal_log(
        measure_count=measure_count,
        beats_per_measure=beats,
        tempo_bpm=tempo,
        repeats=repeats,
        page_starts=page_starts,
        page_count=page_count,
        events=_events(event_tp),
        tolerance_seconds=tol,
    )
    expected = _brute_force(
        cues_exact,
        [(Fraction(str(t)), p) for t, p in event_tp],
        Fraction(str(tol)),
    )

    m = len(cues_exact)
    assert r.total_error_count == expected[0]
    assert r.total_time_diff_seconds == float(expected[1])
    assert _matched_indices(r) == expected[2]
    # 不变量：匹配数 + 漏翻数 = 提示数；匹配数 + 多翻数 = 事件数
    matched = sum(1 for a in r.cue_audits if a.matched)
    assert matched + r.missed_count == m
    assert matched + r.extra_count == len(event_tp)
    assert sorted(a.pedal_event_index for a in r.cue_audits if a.matched) == sorted(
        set(range(len(event_tp))) - set(r.extra_turns)
    )


def test_large_scale_performance():
    """500 提示 × 500 事件的最坏情形应在合理时间内完成。"""
    import time

    # 501 个小节各占一页 → 500 条跨页提示（时刻 1..500 秒，目标页 2..501）
    beats = [1.0] * 501
    pages = list(range(1, 502))
    events = [(i + 1.01, i + 2) for i in range(500)]
    start = time.time()
    r = _audit(beats, pages, events, 0.5)
    elapsed = time.time() - start
    assert r.total_error_count == 0
    assert _matched_indices(r) == tuple(range(500))
    assert elapsed < 10.0
