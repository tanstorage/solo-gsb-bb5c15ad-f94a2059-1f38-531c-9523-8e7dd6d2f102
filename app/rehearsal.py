"""排练录音时间戳定位核心。

已保存的翻页规划描述的是原谱排版；排练录音则按实际演奏顺序进行：重复段会被
完整演奏若干遍。本模块负责：

1. 校验每小节拍数、速度与重复段（不相交、不嵌套、起止含端点、演奏 2~4 遍）；
2. 按谱面顺序展开重复段，得到实际演奏序列——每次完整演奏该段后再进入后续小节；
3. 以演奏开始为 0，按 ``每小节时长 = 60 × 拍数 / 速度`` 求前缀时间；
4. 用左闭右开区间（恰在边界归下一小节）把时间戳定位到：
   原谱 0 基小节号、重复段内第几遍（非重复段为 1）、小节内拍数偏移，
   并借助已存方案的分页结果给出 1 基页码；
5. 在演奏序列上生成翻页提示与翻页空档：仅对相邻发声跨页的边界，在前一个
   演奏小节内按左右手忙碌区间求连续空闲窗，挑选能容纳完整翻页动作的最晚
   开始时刻（并列时优先左手）。

精确时间轴（不使用浮点累加或 epsilon）
========================================
每小节拍数与速度（BPM）先按已校验数值的**十进制字面量**构造有理数
（``Fraction(str(x))``），每小节时长 ``60 × 拍数 / 速度`` 与按重复段展开后
各次发声的开始时刻、总时长都是精确有理数；定位时时间戳同样按十进制字面量
转换。累计时长、区间归属与拍数偏移均据此精确计算：

- 时间区间左闭右开，**只有恰等于边界才进入下一次发声**——两小节各 1 拍、
  60 BPM（每小节恰 1 秒）时，``0.9999999995`` 严格小于边界 1，仍归第一小节
  且拍偏移为精确的 ``0.9999999995``，不会被容差提前推入第二小节而产生
  负拍偏移；
- **严格早于总时长即可定位，等于或超过总时长才报错**——``1.9999999995``
  严格小于总时长 2，正常定位到第二小节，不会被容差误判为越界。

只有结果出口把有理数转回 float（响应仍为 JSON number），转换不参与任何判定。

本模块仅依赖标准库，便于脱离 Web 层直接单测。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import List, Optional, Sequence, Tuple


def _decimal_fraction(value: float) -> Fraction:
    """按已校验数值的十进制字面量构造精确有理数（调用方须已确保是有限数值）。"""
    return Fraction(str(value))


def _eps(magnitude: float) -> float:
    """随数量级缩放的浮点容差，仅用于翻页空档的空闲窗容量判定。

    时间轴本身（发声开始时刻、总时长、区间归属、拍偏移）一律走精确有理数，
    不使用本容差；用户直接输入的静默秒数与最低翻页时间比较走
    :func:`_silence_sufficient` 的十进制精确口径，与规划核心一致。
    """
    return 1e-9 * max(1.0, abs(magnitude))


def _silence_sufficient(silence: float, min_turn_time: float) -> bool:
    """静默是否达标：按十进制字面量精确比较，恰好等于视为充足。

    与 planner 的翻页点合法性同口径——略低于最低翻页时间（如
    0.9999999999 对 1）不得视为充足，不使用任何浮点容差。
    """
    return Decimal(str(silence)) >= Decimal(str(min_turn_time))


class RehearsalError(ValueError):
    """排练定位输入不合法：数组长度不符、重复段重叠、时间戳越界等。"""


@dataclass(frozen=True)
class PlayedMeasure:
    """演奏序列中的一次小节发声。

    :param measure: 原谱小节号（0 基）
    :param playthrough: 所属重复段内的第几遍（1 基）；非重复段固定为 1
    """

    measure: int
    playthrough: int


@dataclass(frozen=True)
class Location:
    """单个时间戳的定位结果。"""

    measure: int  # 原谱 0 基小节号
    playthrough: int  # 重复段内第几遍；非重复段为 1
    beat_offset: float  # 该小节内的拍数偏移（自小节起点起）
    page_index: int  # 已存方案中的 1 基页码


@dataclass(frozen=True)
class TurnCue:
    """一次排练翻页提示。

    仅当演奏序列中相邻两次发声所属页码不同时生成；演奏结束不生成提示。

    :param boundary_seconds: 两次发声之间的边界时刻（秒，以演奏开始为 0），
        即前一小节结束、后一小节开始的瞬间
    :param from_page: 翻页前的页码（前一小节所在页，1 基）
    :param to_page: 翻页后的页码（后一小节所在页，1 基）
    :param from_measure: 前一小节的原谱 0 基小节号
    :param to_measure: 后一小节的原谱 0 基小节号
    :param from_playthrough: 前一小节所属重复段内第几遍（1 基）
    :param to_playthrough: 后一小节所属重复段内第几遍（1 基）
    :param direction: ``"forward"`` 前进翻页（页码增大）或
        ``"backward"`` 回翻（页码减小，通常由重复段回跳引起）
    :param silence_seconds: 前一小节之后的静默秒数（取自已存规划）
    :param sufficient: 该静默是否不低于方案的最低翻页时间
    """

    boundary_seconds: float
    from_page: int
    to_page: int
    from_measure: int
    to_measure: int
    from_playthrough: int
    to_playthrough: int
    direction: str
    silence_seconds: float
    sufficient: bool


@dataclass(frozen=True)
class TurnSlot:
    """一个跨页边界上的翻页动作空档。

    仅当演奏序列中相邻两次发声所属页码不同时计算；翻页动作必须完整落在
    前一次发声（前一个演奏小节）内某只手的连续空闲窗中。找不到能容纳
    完整动作的空闲窗时，边界保留、``feasible`` 为 False，其余动作字段
    为 None。

    :param boundary_seconds: 两次发声之间的边界时刻（秒，以演奏开始为 0），
        即前一小节结束、后一小节开始的瞬间
    :param from_page: 翻页前的页码（前一小节所在页，1 基）
    :param to_page: 翻页后的页码（后一小节所在页，1 基）
    :param from_measure: 前一小节的原谱 0 基小节号
    :param to_measure: 后一小节的原谱 0 基小节号
    :param from_playthrough: 前一小节所属重复段内第几遍（1 基）
    :param to_playthrough: 后一小节所属重复段内第几遍（1 基）
    :param feasible: 是否找到能容纳完整翻页动作的连续空闲窗
    :param hand: 执行翻页的手（``"left"`` 或 ``"right"``）；不可行为 None
    :param action_start_seconds: 翻页动作开始时刻（秒，以演奏开始为 0）；
        不可行为 None
    :param action_end_seconds: 翻页动作结束时刻（秒，以演奏开始为 0）；
        不可行为 None
    """

    boundary_seconds: float
    from_page: int
    to_page: int
    from_measure: int
    to_measure: int
    from_playthrough: int
    to_playthrough: int
    feasible: bool
    hand: Optional[str]
    action_start_seconds: Optional[float]
    action_end_seconds: Optional[float]


# (起始小节, 结束小节, 演奏次数)，小节均为 0 基、两端皆含
Repeat = Tuple[int, int, int]


def _normalize_repeats(
    n: int, repeats: Sequence[Repeat]
) -> List[Tuple[int, int, int]]:
    """校验重复段并按起始小节排序。"""
    checked: List[Tuple[int, int, int]] = []
    for idx, (start, end, times) in enumerate(repeats):
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or not isinstance(times, int)
            or isinstance(start, bool)
            or isinstance(end, bool)
            or isinstance(times, bool)
        ):
            raise RehearsalError(f"第 {idx + 1} 个重复段的字段必须是整数")
        if start < 0 or end < 0 or start > n - 1 or end > n - 1:
            raise RehearsalError(
                f"第 {idx + 1} 个重复段不合法：起止小节必须落在 0..{n - 1}"
                f" 内（实际 {start}..{end}）"
            )
        if start > end:
            raise RehearsalError(
                f"第 {idx + 1} 个重复段起始小节（{start}）不能晚于"
                f"结束小节（{end}）"
            )
        if not 2 <= times <= 4:
            raise RehearsalError(
                f"第 {idx + 1} 个重复段（小节 {start}..{end}）的演奏次数"
                f"必须在 2~4 之间（实际 {times}）"
            )
        checked.append((start, end, times))

    checked.sort(key=lambda r: r[0])
    for prev, nxt in zip(checked, checked[1:]):
        # 端点均含：下一段起点落入上一段区间即为相交（含嵌套/首尾相接重叠）
        if nxt[0] <= prev[1]:
            raise RehearsalError(
                f"重复段 [{prev[0]}..{prev[1]}] 与 [{nxt[0]}..{nxt[1]}] "
                "重叠或嵌套：重复段必须互不相交且不嵌套（首尾相邻允许）"
            )
    return checked


def expand_repeats(
    n: int, repeats: Sequence[Repeat]
) -> List[PlayedMeasure]:
    """按谱面顺序展开重复段，返回实际演奏的小节序列。

    遇到重复段时完整演奏该段 ``times`` 遍（每遍都覆盖起止间的全部小节，
    遍号 1..times），随后再进入该段之后的小节；非重复段小节遍号为 1。
    """
    ordered = _normalize_repeats(n, repeats)
    by_start = {start: (end, times) for start, end, times in ordered}

    events: List[PlayedMeasure] = []
    i = 0
    while i < n:
        if i in by_start:
            end, times = by_start[i]
            for playthrough in range(1, times + 1):
                for measure in range(i, end + 1):
                    events.append(PlayedMeasure(measure, playthrough))
            i = end + 1
        else:
            events.append(PlayedMeasure(i, 1))
            i += 1
    return events


# (起拍, 止拍)，左闭右开，单位：拍
BusyInterval = Tuple[float, float]
# 每小节的双手忙碌区间：(左手区间序列, 右手区间序列)
MeasureBusy = Tuple[Sequence[BusyInterval], Sequence[BusyInterval]]


def _normalize_busy_intervals(
    measure_index: int,
    hand_name: str,
    intervals: Sequence[BusyInterval],
    beats: float,
) -> List[BusyInterval]:
    """校验单只手在一个小节内的忙碌区间，返回浮点 (起拍, 止拍) 列表。"""
    checked: List[BusyInterval] = []
    for j, interval in enumerate(intervals):
        try:
            start, end = interval
        except (TypeError, ValueError) as exc:
            raise RehearsalError(
                f"第 {measure_index + 1} 小节{hand_name}第 {j + 1} 个忙碌区间"
                "必须是 (起拍, 止拍) 二元组"
            ) from exc
        if (
            not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or isinstance(start, bool)
            or isinstance(end, bool)
        ):
            raise RehearsalError(
                f"第 {measure_index + 1} 小节{hand_name}第 {j + 1} 个忙碌区间"
                "的端点必须是数值"
            )
        start_f, end_f = float(start), float(end)
        for value in (start_f, end_f):
            if value != value or value in (float("inf"), float("-inf")):
                raise RehearsalError(
                    f"第 {measure_index + 1} 小节{hand_name}第 {j + 1} 个忙碌区间"
                    "的端点必须是有限数值"
                )
        if not 0 <= start_f < end_f:
            raise RehearsalError(
                f"第 {measure_index + 1} 小节{hand_name}第 {j + 1} 个忙碌区间"
                f" [{start_f:g}, {end_f:g}) 必须满足 0 ≤ 起拍 < 止拍"
            )
        if end_f > beats:
            raise RehearsalError(
                f"第 {measure_index + 1} 小节{hand_name}第 {j + 1} 个忙碌区间"
                f"止拍 {end_f:g} 超过该小节拍数 {beats:g}"
            )
        checked.append((start_f, end_f))
    return checked


def _normalize_hands_busy(
    measure_count: int,
    beats_per_measure: Sequence[float],
    hands_busy: Sequence[MeasureBusy],
) -> List[Tuple[List[BusyInterval], List[BusyInterval]]]:
    """校验每小节双手忙碌区间，整理成 (左手区间, 右手区间) 列表。

    区间允许乱序与重叠（合并在 _free_windows 中进行）；端点须为有限数值
    且满足 0 ≤ 起拍 < 止拍 ≤ 该小节拍数。
    """
    if len(hands_busy) != measure_count:
        raise RehearsalError(
            f"hands_busy 数组长度（{len(hands_busy)}）"
            f"与规划小节数（{measure_count}）不符"
        )
    normalized: List[Tuple[List[BusyInterval], List[BusyInterval]]] = []
    for i, per_measure in enumerate(hands_busy):
        try:
            left, right = per_measure
        except (TypeError, ValueError) as exc:
            raise RehearsalError(
                f"第 {i + 1} 小节的 hands_busy 必须是 (左手区间, 右手区间) 二元组"
            ) from exc
        normalized.append(
            (
                _normalize_busy_intervals(i, "左手", left, beats_per_measure[i]),
                _normalize_busy_intervals(i, "右手", right, beats_per_measure[i]),
            )
        )
    return normalized


def _free_windows(
    busy: Sequence[BusyInterval], beats: float
) -> List[BusyInterval]:
    """忙碌区间（允许乱序、重叠）合并后取补集，返回 [0, beats] 内的空闲窗。"""
    merged: List[List[float]] = []
    for start, end in sorted(busy):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    windows: List[BusyInterval] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            windows.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < beats:
        windows.append((cursor, beats))
    return windows


def _latest_slot_start(
    busy: Sequence[BusyInterval],
    beats: float,
    seconds_per_beat: float,
    turn_seconds: float,
) -> Optional[float]:
    """单手在该小节内能容纳完整翻页动作的最晚开始时刻（相对小节起点，秒）。

    空闲窗 [ws, we)（拍）能容纳完整动作当且仅当其时长不少于 turn_seconds；
    窗内最晚开始即动作恰在窗尾结束：we 对应时刻减 turn_seconds。遍历所有
    空闲窗取最大者；无可用窗返回 None。
    """
    latest: Optional[float] = None
    for w_start, w_end in _free_windows(busy, beats):
        if (w_end - w_start) * seconds_per_beat + _eps(turn_seconds) < turn_seconds:
            continue
        start = w_end * seconds_per_beat - turn_seconds
        # 容差放行时动作起点可能略低于窗起点，钳回窗内
        start = max(start, w_start * seconds_per_beat)
        if latest is None or start > latest:
            latest = start
    return latest


@dataclass(frozen=True)
class Rehearsal:
    """一次排练定位的已校验上下文：演奏序列、精确时间轴与分页映射。

    ``start_times`` / ``total_duration`` 是精确有理数时间轴的 float 视图，
    仅供响应出口使用；一切区间归属与边界判定只在 ``start_times_exact`` /
    ``total_duration_exact`` / ``seconds_per_beat_exact`` 上进行。
    """

    measure_count: int
    tempo_bpm: float
    beats_per_measure: Tuple[float, ...]  # 每个原谱小节的拍数（已校验为正）
    events: List[PlayedMeasure]
    start_times: List[float]  # start_times[k]：第 k 个演奏事件开始时刻的 float 视图
    total_duration: float  # 精确总时长的 float 视图
    page_starts: Tuple[int, ...]  # 每页起始原谱小节（0 基，升序，首个为 0）
    # 精确有理数时间轴：按已校验数值的十进制字面量构造，判定只用它
    start_times_exact: Tuple[Fraction, ...]  # 第 k 个演奏事件的精确开始时刻
    total_duration_exact: Fraction  # 重复段展开后的精确累计总时长
    seconds_per_beat_exact: Fraction  # 每拍秒数 60 / 速度（精确）

    def page_of(self, measure: int) -> int:
        """原谱小节号 -> 已存方案的 1 基页码。"""
        return bisect_right(self.page_starts, measure)

    def locate(self, timestamp: float) -> Location:
        """定位单个时间戳；为负、达到或超过总时长时抛 RehearsalError。

        全部判定在精确有理数时间轴上进行：时间戳按十进制字面量转换为有理数，
        区间左闭右开——恰等于某次发声的起点才归该次发声，严格小于总时长
        即可定位；拍数偏移同样按精确差值计算，仅在出口转回 float。
        """
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            raise RehearsalError("时间戳必须是数值")
        ts = float(timestamp)
        if ts != ts or ts in (float("inf"), float("-inf")):
            raise RehearsalError("时间戳必须是有限数值（不接受 NaN 或 Infinity）")
        t = _decimal_fraction(ts)
        if t < 0:
            raise RehearsalError(
                f"时间戳 {ts} 秒不能为负数（时间戳以演奏开始为 0）"
            )
        total = self.total_duration_exact
        # 左闭右开：总时长处是最后一次发声的右端点，不属于任何小节；
        # 严格小于总时长都可定位，等于或超过才越界
        if t >= total:
            raise RehearsalError(
                f"时间戳 {ts} 秒达到或超出演奏总时长 {float(total)} 秒"
                "（定位采用左闭右开区间，端点总时长不可定位）"
            )

        # 恰在边界归下一次发声：精确二分，无任何容差
        idx = bisect_right(self.start_times_exact, t) - 1
        event = self.events[idx]
        offset_exact = (t - self.start_times_exact[idx]) / self.seconds_per_beat_exact
        # 精确差值非负，不会产生 -0.0；仅在出口转换为 float
        beat_offset = float(offset_exact)
        return Location(
            measure=event.measure,
            playthrough=event.playthrough,
            beat_offset=beat_offset,
            page_index=self.page_of(event.measure),
        )

    def locate_all(self, timestamps: Sequence[float]) -> List[Location]:
        """批量定位；错误信息附带序号，便于定位具体时间戳。"""
        results: List[Location] = []
        for idx, ts in enumerate(timestamps):
            try:
                results.append(self.locate(ts))
            except RehearsalError as exc:
                raise RehearsalError(f"第 {idx + 1} 个时间戳：{exc}") from exc
        return results

    def turn_cues(
        self,
        silences: Sequence[float],
        min_turn_time: float,
    ) -> List[TurnCue]:
        """按实际演奏序列生成排练翻页提示。

        逐对考察相邻的两次发声：仅当二者所属页码不同时生成一条提示，
        因此重复段回跳是否翻页也按回跳两端实际相邻小节判定，与原谱分页
        位置无关。演奏结束（最后一次发声之后）不生成提示。

        :param silences: 已存规划中每个原谱小节之后的静默秒数，长度必须
            等于规划小节数
        :param min_turn_time: 方案的最低翻页时间（秒），用于判断每次提示
            前的静默是否充足
        """
        if len(silences) != self.measure_count:
            raise RehearsalError(
                f"静默秒数数组长度（{len(silences)}）"
                f"与规划小节数（{self.measure_count}）不符"
            )

        cues: List[TurnCue] = []
        for k in range(len(self.events) - 1):
            prev, nxt = self.events[k], self.events[k + 1]
            from_page = self.page_of(prev.measure)
            to_page = self.page_of(nxt.measure)
            if from_page == to_page:
                continue
            silence = float(silences[prev.measure])
            cues.append(
                TurnCue(
                    boundary_seconds=self.start_times[k + 1],
                    from_page=from_page,
                    to_page=to_page,
                    from_measure=prev.measure,
                    to_measure=nxt.measure,
                    from_playthrough=prev.playthrough,
                    to_playthrough=nxt.playthrough,
                    direction="forward" if to_page > from_page else "backward",
                    silence_seconds=silence,
                    sufficient=_silence_sufficient(silence, min_turn_time),
                )
            )
        return cues

    def turn_slots(
        self,
        hands_busy: Sequence[MeasureBusy],
        turn_seconds: float,
    ) -> List[TurnSlot]:
        """按实际演奏序列为每个跨页边界挑选翻页动作空档。

        逐对考察相邻的两次发声：仅当二者所属页码不同时处理该边界。翻页
        动作必须完整落在前一次发声（前一个演奏小节）内某只手的连续空闲
        窗中；在能容纳完整动作（时长 turn_seconds）的空闲窗里选最晚的
        开始时刻，左右手的最晚开始并列时优先左手。找不到可用窗的边界
        保留在结果中并标记为不可行。

        :param hands_busy: 每个原谱小节的 (左手忙碌区间, 右手忙碌区间)，
            区间为 (起拍, 止拍)、左闭右开，允许乱序与重叠；长度必须等于
            规划小节数
        :param turn_seconds: 完成一次完整翻页动作所需的秒数，必须为正的
            有限数值
        """
        if not isinstance(turn_seconds, (int, float)) or isinstance(
            turn_seconds, bool
        ):
            raise RehearsalError("turn_seconds 必须是数值")
        turn = float(turn_seconds)
        if turn != turn or turn in (float("inf"), float("-inf")):
            raise RehearsalError("turn_seconds 必须是有限数值")
        if turn <= 0:
            raise RehearsalError(
                f"turn_seconds（完整翻页动作所需秒数）必须为正数（实际 {turn:g}）"
            )
        normalized = _normalize_hands_busy(
            self.measure_count, self.beats_per_measure, hands_busy
        )

        seconds_per_beat = 60.0 / self.tempo_bpm
        slots: List[TurnSlot] = []
        for k in range(len(self.events) - 1):
            prev, nxt = self.events[k], self.events[k + 1]
            from_page = self.page_of(prev.measure)
            to_page = self.page_of(nxt.measure)
            if from_page == to_page:
                continue
            beats = self.beats_per_measure[prev.measure]
            left_busy, right_busy = normalized[prev.measure]
            left_start = _latest_slot_start(
                left_busy, beats, seconds_per_beat, turn
            )
            right_start = _latest_slot_start(
                right_busy, beats, seconds_per_beat, turn
            )

            # 最晚开始时刻并列时优先左手
            hand: Optional[str] = None
            start_rel: Optional[float] = None
            if left_start is not None and (
                right_start is None or left_start >= right_start
            ):
                hand, start_rel = "left", left_start
            elif right_start is not None:
                hand, start_rel = "right", right_start

            action_start: Optional[float] = None
            action_end: Optional[float] = None
            if start_rel is not None:
                action_start = self.start_times[k] + start_rel
                action_end = action_start + turn
            slots.append(
                TurnSlot(
                    boundary_seconds=self.start_times[k + 1],
                    from_page=from_page,
                    to_page=to_page,
                    from_measure=prev.measure,
                    to_measure=nxt.measure,
                    from_playthrough=prev.playthrough,
                    to_playthrough=nxt.playthrough,
                    feasible=hand is not None,
                    hand=hand,
                    action_start_seconds=action_start,
                    action_end_seconds=action_end,
                )
            )
        return slots


def prepare_rehearsal(
    measure_count: int,
    beats_per_measure: Sequence[float],
    tempo_bpm: float,
    repeats: Sequence[Repeat],
    page_starts: Sequence[int],
) -> Rehearsal:
    """校验输入并构建排练定位上下文。

    时间轴按已校验数值的十进制字面量构造精确有理数：重复段展开后的累计
    时长、各次发声开始时刻与每拍秒数均为精确值，定位判定不使用浮点容差。

    :param measure_count: 已存规划的原谱小节数
    :param beats_per_measure: 每小节拍数，长度必须等于 ``measure_count``、元素为正
    :param tempo_bpm: 每分钟拍数，必须为正
    :param repeats: 重复段序列，彼此不相交、不嵌套，起止含、演奏 2~4 遍
    :param page_starts: 已存方案每页起始小节（0 基升序，首个为 0）
    :raises RehearsalError: 任一语义约束不满足
    """
    if measure_count <= 0:
        raise RehearsalError("规划小节数必须为正数")
    if len(beats_per_measure) != measure_count:
        raise RehearsalError(
            f"beats_per_measure 数组长度（{len(beats_per_measure)}）"
            f"与规划小节数（{measure_count}）不符"
        )
    for i, beats in enumerate(beats_per_measure):
        if not isinstance(beats, (int, float)) or isinstance(beats, bool):
            raise RehearsalError(f"第 {i + 1} 小节拍数必须是数值")
        beats_f = float(beats)
        if beats_f != beats_f or beats_f in (float("inf"), float("-inf")):
            raise RehearsalError(f"第 {i + 1} 小节拍数必须是有限数值")
        if beats_f <= 0:
            raise RehearsalError(
                f"第 {i + 1} 小节拍数必须为正数（实际 {beats_f:g}）"
            )
    if not isinstance(tempo_bpm, (int, float)) or isinstance(tempo_bpm, bool):
        raise RehearsalError("每分钟拍数必须是数值")
    tempo = float(tempo_bpm)
    if tempo != tempo or tempo in (float("inf"), float("-inf")):
        raise RehearsalError("每分钟拍数必须是有限数值")
    if tempo <= 0:
        raise RehearsalError(f"每分钟拍数必须为正数（实际 {tempo:g}）")

    page_tuple = tuple(sorted(page_starts))
    if not page_tuple or page_tuple[0] != 0:
        raise RehearsalError("已存方案分页信息不合法：首页必须从第 0 小节开始")

    # _normalize_repeats 在 expand_repeats 内完成；此处提前调用以先报错
    events = expand_repeats(measure_count, repeats)

    # 精确有理数时间轴：拍数与速度按已校验数值的十进制字面量构造有理数，
    # 每小节时长 = 60 × 拍数 / 速度，前缀和即各次发声的精确开始时刻；
    # 重复段展开后的累计总时长同样精确，不做任何浮点累加
    spb_exact = 60 / _decimal_fraction(tempo)
    durations_exact = [
        _decimal_fraction(float(b)) * spb_exact for b in beats_per_measure
    ]
    starts_exact: List[Fraction] = []
    elapsed = Fraction(0)
    for event in events:
        starts_exact.append(elapsed)
        elapsed += durations_exact[event.measure]

    return Rehearsal(
        measure_count=measure_count,
        tempo_bpm=tempo,
        beats_per_measure=tuple(float(b) for b in beats_per_measure),
        events=events,
        # float 视图仅供响应出口使用；判定一律走下面的精确时间轴
        start_times=[float(s) for s in starts_exact],
        total_duration=float(elapsed),
        page_starts=page_tuple,
        start_times_exact=tuple(starts_exact),
        total_duration_exact=elapsed,
        seconds_per_beat_exact=spb_exact,
    )
