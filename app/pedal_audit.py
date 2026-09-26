"""排练踏板日志校对核心。

背景
====
排练时乐手按翻页提示踩踏板换页，踏板会记录一份日志：按时间排列的若干次踏板
操作，每次给出操作发生的秒数与**操作后页码**。演奏结束后，把这份日志与服务按
排练参数（每小节拍数、速度、重复段）生成的**应翻页提示**逐条校对：

- 每条提示都对应一次"应翻"；
- 一次踏板操作与一条提示**目标页相同**（提示的 ``to_page``）且**时间差不超过
  给定的非负时间容差**时，允许二者一对一匹配（时间差取绝对值）；
- 匹配按提示与日志各自的时间顺序做**全局对齐**（单调序列对齐），一条提示至多
  匹配一次踏板操作，反之亦然；
- 未匹配上的提示记为**漏翻**（应翻未翻），未匹配上的踏板操作记为**多翻**
  （无对应提示的多余操作）。

精确时间轴（不使用浮点或 epsilon）
====================================
校对内部不沿用任何浮点时间轴：每小节拍数与速度（BPM）先按输入数值的**十进制
字面量**构造有理数（``Fraction(str(x))``），每小节时长 ``60 × 拍数 / 速度``
与按重复段展开后的各提示边界时刻、总时长都是精确有理数；踏板事件秒数与时间
容差同样按十进制字面量转换。由此：

- 匹配资格 ``|事件时刻 − 提示时刻| ≤ 容差`` 是精确有理数比较——容差为 0 时
  ``0.3000000005`` 不会误判匹配 ``0.3`` 的提示；
- 累计时间差之和是精确有理数求和，第二、三级目标的并列判定同样精确——
  提示 ``0.4`` 对同目标页事件 ``0.3`` 与 ``0.5`` 的两侧时间差都是精确的
  ``0.1``，等成本时取匹配事件序号序列字典序最小者（即 0.3 秒的事件）；
- 事件的非降序与 ``[0, 总时长)`` 校验也是精确比较——略早于终点可接受，
  略逆序即报错。

只有结果出口把有理数转回 float（响应仍为 JSON number），转换不参与任何判定。

优化目标（依次比较）
====================

1. **漏翻数与多翻数之和最少**（即尽量多地匹配上）；
2. 上一项相同时，**所有匹配对的时间差之和最小**；
3. 仍并列时，取**匹配到的踏板事件序号序列字典序最小**的对齐（序号序列按提示
   顺序排列，因单调对齐天然严格递增；先比第一个匹配的事件序号，再依次比后续）。

算法
====

设提示 m 条、日志事件 n 条（均 ≤ 500）。先做后缀标量 DP：``G[i][j]`` /
``S[i][j]`` 分别为把提示 ``[i:]`` 与事件 ``[j:]`` 对齐的最小（漏翻+多翻）数与
该数下的最小时间差之和。三种转移：

- 漏翻提示 i：``(G[i+1][j]+1, S[i+1][j])``；
- 多翻事件 j：``(G[i][j+1]+1, S[i][j+1])``；
- 当目标页相同且 |t_i − t_j| ≤ 容差时匹配：``(G[i+1][j+1], S[i+1][j+1]+|Δt|)``。

字典序目标只用于同 ``(G, S)`` 的重构选择，且只影响被匹配的事件序号序列，不影响
两项代价值，故不把变长元组放进 O(m·n) 的标量表，而在前向重构时用记忆化递归
``best(i, j)`` 求该后缀在最优代价下的字典序最小匹配序号元组——每个格至多计算
一次，元组总长度 O(m·n·min(m,n)) 在 500 上限下约 17MB，可接受。

``S`` 与时间差权重都是有理数，填充与重构处的比较均为精确的小于/相等判定，
不存在浮点误差：真实不同（哪怕仅差 1e-15）的两项代价必然分出高下，重构也
必然能取到最优动作。

事件越出演奏时长（含恰等于总时长）或页码范围（不在 1..page_count）直接报错。

本模块仅依赖标准库，便于脱离 Web 层直接单测。
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from .rehearsal import Repeat, expand_repeats

# 对齐两侧（应翻提示数 / 踏板事件数）的共同上限，限制 O(m·n) 的 DP 规模
MAX_EVENTS = 500


class PedalAuditError(ValueError):
    """踏板日志校对输入不合法：事件越界、时间非降序破坏、长度超限等。"""


@dataclass(frozen=True)
class PedalEvent:
    """一次踏板操作日志记录。

    :param seconds: 操作发生时刻（秒，以演奏开始为 0）
    :param page_after: 操作完成后所在的 1 基页码
    """

    seconds: float
    page_after: int


@dataclass(frozen=True)
class CueAudit:
    """一条应翻页提示的校对结果。

    :param cue_index: 该提示在应翻页提示序列中的 0 基序号
    :param expected_seconds: 提示的边界时刻（秒）
    :param to_page: 提示要求翻到的目标页（1 基，也是匹配的目标页）
    :param from_page: 提示的翻页前页码（1 基）
    :param matched: 是否匹配到一次踏板操作；False 即漏翻
    :param pedal_event_index: 匹配到的踏板事件 0 基序号；漏翻为 None
    :param pedal_seconds: 匹配踏板事件的操作时刻（秒）；漏翻为 None
    :param time_diff_seconds: 匹配对的时间差绝对值（秒）；漏翻为 None
    """

    cue_index: int
    expected_seconds: float
    to_page: int
    from_page: int
    matched: bool
    pedal_event_index: Optional[int]
    pedal_seconds: Optional[float]
    time_diff_seconds: Optional[float]


@dataclass(frozen=True)
class PedalAuditResult:
    """一次踏板日志校对的完整结果。"""

    total_duration: float  # 按重复段展开后的演奏总时长（秒）
    tolerance_seconds: float  # 本次校对采用的时间容差（秒，非负）
    cue_audits: List[CueAudit]  # 每条应翻提示的校对结果（按提示顺序）
    extra_turns: List[int]  # 未匹配踏板事件的 0 基序号（多翻，升序）
    missed_count: int  # 漏翻数（未匹配提示数）
    extra_count: int  # 多翻数（未匹配踏板事件数）
    total_error_count: int  # 漏翻与多翻总数（第一项代价，全局最小）
    total_time_diff_seconds: float  # 匹配时间差之和（第二项代价，全局最小）


# 精确重建的一条应翻提示：(边界时刻, 翻页前页, 目标页)
ExactCue = Tuple[Fraction, int, int]


def _decimal_fraction(value: float) -> Fraction:
    """按输入数值的十进制字面量构造精确有理数（调用方须已确保是有限数值）。"""
    return Fraction(str(value))


def _validate_rehearsal_params(
    measure_count: int,
    beats_per_measure: Sequence[float],
    tempo_bpm: float,
    page_starts: Sequence[int],
    page_count: int,
) -> Tuple[List[float], float, List[int]]:
    """校验排练参数与分页信息，返回 (拍数列表, 速度, 分页起始小节)。"""
    if (
        not isinstance(measure_count, int)
        or isinstance(measure_count, bool)
        or measure_count <= 0
    ):
        raise PedalAuditError("规划小节数必须为正整数")
    if len(beats_per_measure) != measure_count:
        raise PedalAuditError(
            f"beats_per_measure 数组长度（{len(beats_per_measure)}）"
            f"与规划小节数（{measure_count}）不符"
        )
    beats: List[float] = []
    for i, value in enumerate(beats_per_measure):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise PedalAuditError(f"第 {i + 1} 小节拍数必须是数值")
        beat = float(value)
        if beat != beat or beat in (float("inf"), float("-inf")):
            raise PedalAuditError(f"第 {i + 1} 小节拍数必须是有限数值")
        if beat <= 0:
            raise PedalAuditError(
                f"第 {i + 1} 小节拍数必须为正数（实际 {beat:g}）"
            )
        beats.append(beat)

    if not isinstance(tempo_bpm, (int, float)) or isinstance(tempo_bpm, bool):
        raise PedalAuditError("每分钟拍数必须是数值")
    tempo = float(tempo_bpm)
    if tempo != tempo or tempo in (float("inf"), float("-inf")):
        raise PedalAuditError("每分钟拍数必须是有限数值")
    if tempo <= 0:
        raise PedalAuditError(f"每分钟拍数必须为正数（实际 {tempo:g}）")

    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise PedalAuditError("规划页数必须为正整数")
    starts = list(page_starts)
    if not starts or starts[0] != 0:
        raise PedalAuditError("已存方案分页信息不合法：首页必须从第 0 小节开始")
    if len(starts) != page_count:
        raise PedalAuditError(
            "已存方案分页信息不合法：分页起始小节数量与规划页数不符"
        )
    prev = -1
    for s in starts:
        if (
            not isinstance(s, int)
            or isinstance(s, bool)
            or s <= prev
            or s >= measure_count
        ):
            raise PedalAuditError(
                "已存方案分页信息不合法：分页起始小节必须严格递增"
                "且落在规划小节范围内"
            )
        prev = s
    return beats, tempo, starts


def _build_exact_cues(
    beats: Sequence[float],
    tempo: float,
    repeats: Sequence[Repeat],
    page_starts: Sequence[int],
) -> Tuple[List[ExactCue], Fraction]:
    """按十进制字面量重建精确时间轴，返回 (应翻提示列表, 总时长)。

    每小节时长 = 60 × 拍数 / 速度（精确有理数）；按重复段展开实际演奏序列，
    相邻两次发声所属页码不同的边界即一条应翻提示，其时刻为精确前缀和
    （含重复段回跳造成的回翻）；演奏结束不生成提示。
    """
    tempo_exact = _decimal_fraction(tempo)
    durations = [_decimal_fraction(b) * 60 / tempo_exact for b in beats]

    cues: List[ExactCue] = []
    elapsed = Fraction(0)
    prev_page: Optional[int] = None
    for played in expand_repeats(len(beats), repeats):
        page = bisect_right(page_starts, played.measure)
        if prev_page is not None and page != prev_page:
            cues.append((elapsed, prev_page, page))
        elapsed += durations[played.measure]
        prev_page = page
    return cues, elapsed


def audit_pedal_log(
    measure_count: int,
    beats_per_measure: Sequence[float],
    tempo_bpm: float,
    repeats: Sequence[Repeat],
    page_starts: Sequence[int],
    page_count: int,
    events: Sequence[PedalEvent],
    tolerance_seconds: float,
) -> PedalAuditResult:
    """把按时间排列的踏板日志与精确重建的应翻页提示做全局对齐校对。

    每小节拍数、速度、事件秒数与时间容差都按输入数值的十进制字面量构造
    有理数；提示时刻按重复段展开做精确前缀和。匹配资格、累计时间差、事件
    非降序与 ``[0, 总时长)`` 校验均为精确有理数比较，不使用浮点或 epsilon。

    :param measure_count: 已存规划的原谱小节数
    :param beats_per_measure: 每小节拍数，长度必须等于 ``measure_count``、元素为正
    :param tempo_bpm: 每分钟拍数，必须为正
    :param repeats: 重复段序列（起止 0 基含端点、演奏 2~4 遍，互不相交不嵌套）
    :param page_starts: 已存方案每页起始小节（0 基严格递增，首个为 0）
    :param page_count: 已存方案页数，事件操作后页码须在 ``1..page_count``
    :param events: 按时间**非降序**排列的踏板事件（秒数、操作后页码）
    :param tolerance_seconds: 非负时间容差（秒，有限数值），仅当时间差不超过
        该容差且目标页相同时允许匹配
    :return: 每条提示的匹配/漏翻结果、多翻事件序号与两项代价（数值为 float）
    :raises PedalAuditError: 任一输入约束不满足
    :raises RehearsalError: 重复段不合法（重叠、嵌套、遍数越界等）
    """
    n = len(events)
    if n > MAX_EVENTS:
        raise PedalAuditError(
            f"踏板事件数（{n}）超过上限 {MAX_EVENTS}，无法校对"
        )

    beats, tempo, starts = _validate_rehearsal_params(
        measure_count, beats_per_measure, tempo_bpm, page_starts, page_count
    )
    cues, total_exact = _build_exact_cues(beats, tempo, repeats, starts)
    m = len(cues)
    if m > MAX_EVENTS:
        raise PedalAuditError(
            f"应翻页提示数（{m}）超过上限 {MAX_EVENTS}，无法校对"
        )

    if not isinstance(tolerance_seconds, (int, float)) or isinstance(
        tolerance_seconds, bool
    ):
        raise PedalAuditError("时间容差必须是数值")
    tol = float(tolerance_seconds)
    if tol != tol or tol in (float("inf"), float("-inf")):
        raise PedalAuditError("时间容差必须是有限数值（不接受 NaN 或 Infinity）")
    if tol < 0:
        raise PedalAuditError(f"时间容差不能为负数（实际 {tol:g} 秒）")
    tol_exact = _decimal_fraction(tol)

    checked: List[PedalEvent] = []
    event_times: List[Fraction] = []
    prev_exact: Optional[Fraction] = None
    for idx, event in enumerate(events):
        if not isinstance(event, PedalEvent):
            raise PedalAuditError(f"第 {idx + 1} 个踏板事件格式不合法")
        seconds = event.seconds
        page = event.page_after
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            raise PedalAuditError(f"第 {idx + 1} 个踏板事件秒数必须是数值")
        seconds = float(seconds)
        if seconds != seconds or seconds in (float("inf"), float("-inf")):
            raise PedalAuditError(
                f"第 {idx + 1} 个踏板事件秒数必须是有限数值"
                "（不接受 NaN 或 Infinity）"
            )
        sec_exact = _decimal_fraction(seconds)
        if sec_exact < 0:
            raise PedalAuditError(
                f"第 {idx + 1} 个踏板事件：操作时刻 {seconds:g} 秒不能为负数"
                "（以演奏开始为 0）"
            )
        if sec_exact >= total_exact:
            raise PedalAuditError(
                f"第 {idx + 1} 个踏板事件：操作时刻 {seconds:g} 秒越出演奏时长"
                f" {float(total_exact):g} 秒（须在 [0, 总时长) 内，"
                "恰等于总时长视为演奏结束之后）"
            )
        if not isinstance(page, int) or isinstance(page, bool):
            raise PedalAuditError(
                f"第 {idx + 1} 个踏板事件的操作后页码必须为整数（收到 {page!r}）"
            )
        if not 1 <= page <= page_count:
            raise PedalAuditError(
                f"第 {idx + 1} 个踏板事件：操作后页码 {page} 越出页码范围"
                f" 1..{page_count}"
            )
        if prev_exact is not None and sec_exact < prev_exact:
            raise PedalAuditError(
                f"第 {idx + 1} 个踏板事件：操作时刻 {seconds:g} 秒早于上一条"
                f" {float(prev_exact):g} 秒，踏板事件必须按时间非降序排列"
            )
        checked.append(PedalEvent(seconds, page))
        event_times.append(sec_exact)
        prev_exact = sec_exact

    if m == 0:
        # 整篇只有一页：没有任何应翻提示，所有踏板事件都是多翻
        return PedalAuditResult(
            total_duration=float(total_exact),
            tolerance_seconds=tol,
            cue_audits=[],
            extra_turns=list(range(n)),
            missed_count=0,
            extra_count=n,
            total_error_count=n,
            total_time_diff_seconds=0.0,
        )

    return _align(
        cues=cues,
        events=checked,
        event_times=event_times,
        total_exact=total_exact,
        tol_exact=tol_exact,
        tol=tol,
    )


def _align(
    cues: List[ExactCue],
    events: List[PedalEvent],
    event_times: List[Fraction],
    total_exact: Fraction,
    tol_exact: Fraction,
    tol: float,
) -> PedalAuditResult:
    """后缀标量 DP + 记忆化字典序重构（输入均已校验、提示非空，全程有理数）。"""
    m, n = len(cues), len(events)

    # compatible[i][j]：提示 i 与事件 j 目标页相同且时间差不超过容差（精确比较）；
    # weight[i][j] 为匹配对的时间差绝对值（精确有理数）。
    compatible: List[bytearray] = [bytearray(n) for _ in range(m)]
    weight: List[List[Fraction]] = [[Fraction(0)] * n for _ in range(m)]
    for i in range(m):
        cue_time, _, to_page = cues[i]
        row_c, row_w = compatible[i], weight[i]
        for j in range(n):
            if events[j].page_after == to_page:
                diff = abs(event_times[j] - cue_time)
                if diff <= tol_exact:
                    row_c[j] = 1
                    row_w[j] = diff

    # 时间差之和一律用有理数的精确比较（< / ==）。每个 S 值都是由同一张 weight
    # 表逐次相加得到，重构时用与填充完全相同的表达式（S[后继] + weight[i][j]）
    # 复算，有理数运算确定且精确，故同一动作在填充与重构处必然一致——既不会
    # 把真实不同（哪怕仅差 1e-15）的两项代价误判为并列，也不会让重构漏掉最优动作。
    def _same_cost(g: int, s: Fraction, g_ref: int, s_ref: Fraction) -> bool:
        return g == g_ref and s == s_ref

    # 后缀标量 DP：G = 最小（漏翻+多翻）数，S = 该数下的最小时间差之和。
    gaps = [[0] * (n + 1) for _ in range(m + 1)]
    sums: List[List[Fraction]] = [
        [Fraction(0)] * (n + 1) for _ in range(m + 1)
    ]
    for i in range(m):
        gaps[i][n] = m - i  # 事件耗尽：剩余提示全部漏翻
    for j in range(n):
        gaps[m][j] = n - j  # 提示耗尽：剩余事件全部多翻
    for i in range(m - 1, -1, -1):
        gi, gi_next = gaps[i], gaps[i + 1]
        si, si_next = sums[i], sums[i + 1]
        row_c, row_w = compatible[i], weight[i]
        for j in range(n - 1, -1, -1):
            # 候选 1：漏翻提示 i
            best_g, best_s = gi_next[j] + 1, si_next[j]
            # 候选 2：事件 j 多翻
            g2, s2 = gi[j + 1] + 1, si[j + 1]
            if g2 < best_g or (g2 == best_g and s2 < best_s):
                best_g, best_s = g2, s2
            # 候选 3：匹配
            if row_c[j]:
                g3, s3 = gi_next[j + 1], si_next[j + 1] + row_w[j]
                if g3 < best_g or (g3 == best_g and s3 < best_s):
                    best_g, best_s = g3, s3
            gi[j], si[j] = best_g, best_s

    # best(i, j)：后缀 [i:]/[j:] 在保持全局最优 (G, S) 前提下的字典序最小
    # 匹配事件序号元组。同一格至多计算一次；记忆化在下方 lru_cache 中完成。
    @lru_cache(maxsize=None)
    def _best(i: int, j: int) -> Tuple[int, ...]:
        if i == m or j == n:
            return ()
        g_ref, s_ref = gaps[i][j], sums[i][j]
        candidates: List[Tuple[int, ...]] = []
        # 三种动作中凡能保持最优代价者，收集其后续的最优元组后取字典序最小。
        if _same_cost(gaps[i + 1][j] + 1, sums[i + 1][j], g_ref, s_ref):
            candidates.append(_best(i + 1, j))
        if _same_cost(gaps[i][j + 1] + 1, sums[i][j + 1], g_ref, s_ref):
            candidates.append(_best(i, j + 1))
        if compatible[i][j] and _same_cost(
            gaps[i + 1][j + 1],
            sums[i + 1][j + 1] + weight[i][j],
            g_ref,
            s_ref,
        ):
            candidates.append((j,) + _best(i + 1, j + 1))
        return min(candidates) if candidates else ()

    # 前向重构：沿 _best 指示的最优路径逐格确定动作。
    matched_by_cue: List[Optional[int]] = [None] * m
    used_events: set[int] = set()
    i = j = 0
    while i < m or j < n:
        if i == m:
            j += 1  # 剩余事件均为多翻
            continue
        if j == n:
            i += 1  # 剩余提示均为漏翻
            continue
        g_ref, s_ref = gaps[i][j], sums[i][j]
        if compatible[i][j] and _same_cost(
            gaps[i + 1][j + 1],
            sums[i + 1][j + 1] + weight[i][j],
            g_ref,
            s_ref,
        ):
            match_tuple = (j,) + _best(i + 1, j + 1)
            if match_tuple == _best(i, j):
                matched_by_cue[i] = j
                used_events.add(j)
                i += 1
                j += 1
                continue
        if _same_cost(
            gaps[i + 1][j] + 1, sums[i + 1][j], g_ref, s_ref
        ) and _best(i + 1, j) == _best(i, j):
            i += 1  # 提示 i 漏翻
        else:
            j += 1  # 事件 j 多翻

    cue_audits: List[CueAudit] = []
    missed_count = 0
    for i in range(m):
        cue_time, from_page, to_page = cues[i]
        j = matched_by_cue[i]
        if j is None:
            missed_count += 1
            cue_audits.append(
                CueAudit(
                    cue_index=i,
                    expected_seconds=float(cue_time),
                    to_page=to_page,
                    from_page=from_page,
                    matched=False,
                    pedal_event_index=None,
                    pedal_seconds=None,
                    time_diff_seconds=None,
                )
            )
        else:
            cue_audits.append(
                CueAudit(
                    cue_index=i,
                    expected_seconds=float(cue_time),
                    to_page=to_page,
                    from_page=from_page,
                    matched=True,
                    pedal_event_index=j,
                    pedal_seconds=events[j].seconds,
                    time_diff_seconds=float(weight[i][j]),
                )
            )

    extra_turns = [j for j in range(n) if j not in used_events]
    return PedalAuditResult(
        total_duration=float(total_exact),
        tolerance_seconds=tol,
        cue_audits=cue_audits,
        extra_turns=extra_turns,
        missed_count=missed_count,
        extra_count=len(extra_turns),
        total_error_count=gaps[0][0],
        total_time_diff_seconds=float(sums[0][0]),
    )


# _best 的记忆化递归深度最多为 m + n（≤ 2·MAX_EVENTS），放宽默认上限。
sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
