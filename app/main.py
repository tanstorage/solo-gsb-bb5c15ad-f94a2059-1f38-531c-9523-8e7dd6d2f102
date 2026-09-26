"""FastAPI 入口：提交翻页规划、按 ID 查询结果、排练录音时间戳定位、翻页提示与翻页空档。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .booklet import ImpositionError, impose_booklet
from .pedal_audit import PedalAuditError, PedalEvent, audit_pedal_log
from .planner import Plan, PlanningError, plan_pages
from .practice_route import PracticeRouteError, plan_practice_route
from .practice_tabs import PracticeTabError, plan_practice_tabs
from .print_audit import PrintAuditError, audit_print_pages
from .rehearsal import (
    Location,
    RehearsalError,
    TurnCue,
    TurnSlot,
    prepare_rehearsal,
)
from .storage import PlanStore
from .tour_packing import TourPackingError, pack_tour_books
from .tour_stands import StandAction, StandChangeError, plan_stand_changes

DATA_DIR = os.environ.get("DATA_DIR", "./data")
DB_PATH = os.path.join(DATA_DIR, "plans.db")

store: Optional[PlanStore] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    os.makedirs(DATA_DIR, exist_ok=True)
    store = PlanStore(DB_PATH)  # 启动时完成建表初始化
    yield


app = FastAPI(
    title="演出乐谱翻页点规划 API",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------- 数据模型


class PlanRequest(BaseModel):
    """提交规划的请求体。"""

    model_config = ConfigDict(extra="forbid")

    measure_heights: List[float] = Field(
        ..., description="依次排列的每小节高度（与页面可用高度同单位）"
    )
    silence_seconds: List[float] = Field(
        ..., description="每小节之后可供翻页的静默秒数"
    )
    page_height: float = Field(..., description="页面可用高度")
    min_turn_time: float = Field(..., description="最低翻页时间（秒）")
    required_breaks: List[int] = Field(
        default_factory=list,
        description="必翻位置：新页起始小节的 0 基序号（合法范围 1..小节数-1），"
        "这些位置必须断页；省略或为空表示无必翻约束",
    )
    forbidden_breaks: List[int] = Field(
        default_factory=list,
        description="禁翻位置：新页起始小节的 0 基序号（合法范围 1..小节数-1），"
        "这些位置不得断页；省略或为空表示无禁翻约束",
    )

    @field_validator("required_breaks", "forbidden_breaks", mode="before")
    @classmethod
    def _reject_non_integer_elements(cls, value: object) -> object:
        """序号必须是严格整数：拒绝 1.5 之类浮点（pydantic 默认会强转）与布尔。"""
        if isinstance(value, list):
            for b in value:
                if not isinstance(b, int) or isinstance(b, bool):
                    raise ValueError(f"断页序号必须为整数（收到 {b!r}）")
        return value

    @model_validator(mode="after")
    def _validate(self) -> "PlanRequest":
        if not self.measure_heights:
            raise ValueError("measure_heights 不能为空")
        if len(self.silence_seconds) != len(self.measure_heights):
            raise ValueError(
                "silence_seconds 长度必须与 measure_heights 一致（每小节一个静默值）"
            )
        if any(h <= 0 for h in self.measure_heights):
            raise ValueError("measure_heights 中每个小节高度必须为正数")
        if any(s < 0 for s in self.silence_seconds):
            raise ValueError("silence_seconds 中静默秒数不能为负数")
        if self.page_height <= 0:
            raise ValueError("page_height 必须为正数")
        if self.min_turn_time < 0:
            raise ValueError("min_turn_time 不能为负数")

        n = len(self.measure_heights)

        def _check_breaks(name: str, values: List[int]) -> set[int]:
            checked: set[int] = set()
            for b in values:
                if b < 1 or b > n - 1:
                    raise ValueError(
                        f"{name} 含越界序号 {b}：合法范围为 1..{n - 1}"
                        "（0 是首页起点、末尾序号代表文档结束，均不能作为断页位置）"
                    )
                if b in checked:
                    raise ValueError(f"{name} 含重复序号 {b}")
                checked.add(b)
            return checked

        required = _check_breaks("required_breaks", self.required_breaks)
        forbidden = _check_breaks("forbidden_breaks", self.forbidden_breaks)
        clash = required & forbidden
        if clash:
            b = sorted(clash)[0]
            raise ValueError(
                f"序号 {b} 同时出现在 required_breaks 与 forbidden_breaks 中，"
                "必翻与禁翻约束不能交叉"
            )
        return self


class PageOut(BaseModel):
    page_index: int = Field(..., description="页码（从 1 开始）")
    start_measure: int = Field(..., description="起始小节序号（0 基）")
    end_measure: int = Field(..., description="结束小节序号（0 基，闭区间）")
    measure_count: int
    used_height: float
    turn_silence: Optional[float] = Field(
        None, description="本页之后翻页点的静默秒数；最后一页为 null"
    )


class PlanResult(BaseModel):
    page_count: int
    shortest_turn_silence: Optional[float] = Field(
        None, description="所有翻页点中最短的静默秒数；单页时为 null"
    )
    breaks: List[int] = Field(..., description="断页序号序列（新页起始小节，0 基）")
    pages: List[PageOut]


class PlanResponse(BaseModel):
    id: str
    created_at: str
    request: PlanRequest
    result: PlanResult


def _plan_to_result(plan: Plan) -> PlanResult:
    return PlanResult(
        page_count=plan.page_count,
        shortest_turn_silence=plan.shortest_turn_silence,
        breaks=plan.breaks,
        pages=[PageOut(**vars(p)) for p in plan.pages],
    )


# ---------------------------------------- 排练录音时间戳定位


class RepeatSectionIn(BaseModel):
    """一个重复段：起止小节均含（0 基），演奏 2~4 遍。"""

    model_config = ConfigDict(extra="forbid")

    start_measure: int = Field(..., description="重复段起始小节（0 基，含）")
    end_measure: int = Field(..., description="重复段结束小节（0 基，含）")
    play_count: int = Field(..., description="整段演奏次数（2~4，首尾两遍均完整演奏）")

    @model_validator(mode="after")
    def _validate(self) -> "RepeatSectionIn":
        if self.start_measure < 0 or self.end_measure < 0:
            raise ValueError("重复段起止小节不能为负数")
        if self.start_measure > self.end_measure:
            raise ValueError(
                f"重复段起始小节（{self.start_measure}）不能晚于结束小节"
                f"（{self.end_measure}）"
            )
        if not 2 <= self.play_count <= 4:
            raise ValueError(
                f"重复段（小节 {self.start_measure}..{self.end_measure}）演奏次数"
                f"必须在 2~4 之间（实际 {self.play_count}）"
            )
        return self


class RehearsalParams(BaseModel):
    """排练请求共用参数：每小节拍数、速度与互不相交的重复段。"""

    model_config = ConfigDict(extra="forbid")

    beats_per_measure: List[float] = Field(
        ...,
        description="与原小节等长的每小节拍数，长度必须与规划小节数一致、元素为正",
    )
    tempo_bpm: float = Field(..., description="每分钟拍数，必须为正")
    repeats: List[RepeatSectionIn] = Field(
        default_factory=list,
        description="若干不相交且不嵌套的重复段（起止小节均含、演奏次数 2~4）",
    )

    @model_validator(mode="after")
    def _validate(self) -> "RehearsalParams":
        if any(b != b or b in (float("inf"), float("-inf")) for b in self.beats_per_measure):
            raise ValueError("beats_per_measure 中每个拍数必须是有限数值")
        if any(b <= 0 for b in self.beats_per_measure):
            raise ValueError("beats_per_measure 中每个小节拍数必须为正数")
        if self.tempo_bpm != self.tempo_bpm or self.tempo_bpm in (
            float("inf"),
            float("-inf"),
        ):
            raise ValueError("tempo_bpm 必须是有限数值")
        if self.tempo_bpm <= 0:
            raise ValueError("tempo_bpm（每分钟拍数）必须为正数")
        return self


class RehearsalLocateRequest(RehearsalParams):
    """排练录音时间戳定位请求体。"""

    timestamps: List[float] = Field(
        ..., description="排练录音时间戳数组（秒，以演奏开始为 0）"
    )

    @model_validator(mode="after")
    def _validate_timestamps(self) -> "RehearsalLocateRequest":
        if any(t != t or t in (float("inf"), float("-inf")) for t in self.timestamps):
            raise ValueError("时间戳必须是有限数值（不接受 NaN 或 Infinity）")
        if any(t < 0 for t in self.timestamps):
            raise ValueError("时间戳不能为负数（以演奏开始为 0）")
        return self


class LocationOut(BaseModel):
    measure: int = Field(..., description="原谱小节号（0 基）")
    playthrough: int = Field(
        ..., description="所属重复段内第几遍（1 基）；非重复段小节恒为 1"
    )
    beat_offset: float = Field(..., description="该小节内的拍数偏移（自小节起点起）")
    page_index: int = Field(..., description="已存方案的页码（1 基）")


class RehearsalLocateResponse(BaseModel):
    id: str
    request: RehearsalLocateRequest
    total_duration: float = Field(
        ..., description="按重复段展开后的演奏总时长（秒）"
    )
    locations: List[LocationOut] = Field(
        ..., description="与 timestamps 一一对应的定位结果"
    )


def _location_to_out(loc: Location) -> LocationOut:
    return LocationOut(
        measure=loc.measure,
        playthrough=loc.playthrough,
        beat_offset=loc.beat_offset,
        page_index=loc.page_index,
    )


# ---------------------------------------- 排练翻页提示


class RehearsalCuesRequest(RehearsalParams):
    """排练翻页提示请求体（与定位接口共用拍数、速度、重复段参数）。"""


class TurnCueOut(BaseModel):
    """一次翻页提示：相邻演奏小节所属页码变化时生成。"""

    boundary_seconds: float = Field(
        ..., description="翻页边界时刻（秒，以演奏开始为 0）：前一小节结束处"
    )
    from_page: int = Field(..., description="翻页前页码（前一小节所在页，1 基）")
    to_page: int = Field(..., description="翻页后页码（后一小节所在页，1 基）")
    from_measure: int = Field(..., description="前一小节的原谱小节号（0 基）")
    to_measure: int = Field(..., description="后一小节的原谱小节号（0 基）")
    from_playthrough: int = Field(
        ..., description="前一小节所属重复段内第几遍（1 基）；非重复段为 1"
    )
    to_playthrough: int = Field(
        ..., description="后一小节所属重复段内第几遍（1 基）；非重复段为 1"
    )
    direction: str = Field(
        ..., description="翻页方向：forward 前进翻页，backward 回翻"
    )
    silence_seconds: float = Field(
        ..., description="前一小节之后的静默秒数（取自已存规划）"
    )
    sufficient: bool = Field(
        ..., description="该静默是否不低于方案的最低翻页时间"
    )


class RehearsalCuesResponse(BaseModel):
    id: str
    request: RehearsalCuesRequest
    min_turn_time: float = Field(
        ..., description="已存方案的最低翻页时间（秒），sufficient 的比较基准"
    )
    total_duration: float = Field(
        ..., description="按重复段展开后的演奏总时长（秒）"
    )
    cues: List[TurnCueOut] = Field(..., description="按边界时刻升序的翻页提示")


def _cue_to_out(cue: TurnCue) -> TurnCueOut:
    return TurnCueOut(
        boundary_seconds=cue.boundary_seconds,
        from_page=cue.from_page,
        to_page=cue.to_page,
        from_measure=cue.from_measure,
        to_measure=cue.to_measure,
        from_playthrough=cue.from_playthrough,
        to_playthrough=cue.to_playthrough,
        direction=cue.direction,
        silence_seconds=cue.silence_seconds,
        sufficient=cue.sufficient,
    )


# ---------------------------------------- 排练翻页空档


class MeasureBusyIn(BaseModel):
    """一个原谱小节内左右手的忙碌区间（拍，左闭右开）。"""

    model_config = ConfigDict(extra="forbid")

    left: List[Tuple[float, float]] = Field(
        default_factory=list,
        description="左手忙碌区间 [起拍, 止拍)，允许乱序与重叠；省略表示该手无忙碌",
    )
    right: List[Tuple[float, float]] = Field(
        default_factory=list,
        description="右手忙碌区间 [起拍, 止拍)，允许乱序与重叠；省略表示该手无忙碌",
    )


class RehearsalTurnSlotsRequest(RehearsalParams):
    """排练翻页空档请求体（与定位/提示接口共用拍数、速度、重复段参数）。"""

    turn_seconds: float = Field(
        ..., description="完成一次完整翻页动作所需的秒数，必须为正的有限数值"
    )
    hands_busy: List[MeasureBusyIn] = Field(
        ...,
        description="与原谱小节等长的每小节双手忙碌区间（拍，左闭右开，允许乱序与重叠）",
    )

    @model_validator(mode="after")
    def _validate_turn_slots(self) -> "RehearsalTurnSlotsRequest":
        if self.turn_seconds != self.turn_seconds or self.turn_seconds in (
            float("inf"),
            float("-inf"),
        ):
            raise ValueError("turn_seconds 必须是有限数值")
        if self.turn_seconds <= 0:
            raise ValueError("turn_seconds（完整翻页动作所需秒数）必须为正数")
        for i, per_measure in enumerate(self.hands_busy):
            # 止拍与小节拍数的比较依赖 beats_per_measure；其长度与规划小节数
            # 的一致性由核心逻辑校验，此处仅在可比时比较
            limit = (
                self.beats_per_measure[i]
                if i < len(self.beats_per_measure)
                else None
            )
            for hand_name, intervals in (
                ("left", per_measure.left),
                ("right", per_measure.right),
            ):
                for start, end in intervals:
                    if any(
                        v != v or v in (float("inf"), float("-inf"))
                        for v in (start, end)
                    ):
                        raise ValueError(
                            f"hands_busy 第 {i + 1} 小节 {hand_name} 的忙碌区间"
                            "端点必须是有限数值"
                        )
                    if not 0 <= start < end:
                        raise ValueError(
                            f"hands_busy 第 {i + 1} 小节 {hand_name} 的忙碌区间"
                            f" [{start}, {end}) 必须满足 0 ≤ 起拍 < 止拍"
                        )
                    if limit is not None and end > limit:
                        raise ValueError(
                            f"hands_busy 第 {i + 1} 小节 {hand_name} 的忙碌区间"
                            f"止拍 {end} 超过该小节拍数 {limit}"
                        )
        return self


class TurnSlotOut(BaseModel):
    """一个跨页边界上的翻页动作空档；无可用窗时保留边界并标记不可行。"""

    boundary_seconds: float = Field(
        ..., description="跨页边界时刻（秒，以演奏开始为 0）：前一小节结束处"
    )
    from_page: int = Field(..., description="翻页前页码（前一小节所在页，1 基）")
    to_page: int = Field(..., description="翻页后页码（后一小节所在页，1 基）")
    from_measure: int = Field(..., description="前一小节的原谱小节号（0 基）")
    to_measure: int = Field(..., description="后一小节的原谱小节号（0 基）")
    from_playthrough: int = Field(
        ..., description="前一小节所属重复段内第几遍（1 基）；非重复段为 1"
    )
    to_playthrough: int = Field(
        ..., description="后一小节所属重复段内第几遍（1 基）；非重复段为 1"
    )
    feasible: bool = Field(
        ..., description="是否找到能容纳完整翻页动作的连续空闲窗"
    )
    hand: Optional[str] = Field(
        None, description="执行翻页的手：left 或 right；不可行为 null"
    )
    action_start_seconds: Optional[float] = Field(
        None, description="翻页动作开始时刻（秒，以演奏开始为 0）；不可行为 null"
    )
    action_end_seconds: Optional[float] = Field(
        None, description="翻页动作结束时刻（秒，以演奏开始为 0）；不可行为 null"
    )


class RehearsalTurnSlotsResponse(BaseModel):
    id: str
    request: RehearsalTurnSlotsRequest
    total_duration: float = Field(
        ..., description="按重复段展开后的演奏总时长（秒）"
    )
    slots: List[TurnSlotOut] = Field(
        ..., description="按边界时刻升序的跨页翻页空档（含不可行边界）"
    )


def _slot_to_out(slot: TurnSlot) -> TurnSlotOut:
    return TurnSlotOut(
        boundary_seconds=slot.boundary_seconds,
        from_page=slot.from_page,
        to_page=slot.to_page,
        from_measure=slot.from_measure,
        to_measure=slot.to_measure,
        from_playthrough=slot.from_playthrough,
        to_playthrough=slot.to_playthrough,
        feasible=slot.feasible,
        hand=slot.hand,
        action_start_seconds=slot.action_start_seconds,
        action_end_seconds=slot.action_end_seconds,
    )


# ---------------------------------------- 排练踏板日志校对


class PedalEventIn(BaseModel):
    """一条踏板操作日志：操作发生秒数与操作后页码。"""

    model_config = ConfigDict(extra="forbid")

    seconds: float = Field(
        ..., description="踏板操作发生时刻（秒，以演奏开始为 0），非负有限数值"
    )
    page_after: int = Field(
        ...,
        description="操作完成后所在的 1 基页码（须在 1..规划页数内）；与提示"
        "的目标页相同才可能匹配",
    )

    @field_validator("page_after", mode="before")
    @classmethod
    def _reject_non_integer_page(cls, value: object) -> object:
        """操作后页码必须是严格整数：拒绝浮点与布尔；范围由核心逻辑校验。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"操作后页码必须为整数（收到 {value!r}）")
        return value


class RehearsalPedalAuditRequest(RehearsalParams):
    """排练踏板日志校对请求体（与定位/提示接口共用拍数、速度、重复段参数）。"""

    pedal_events: List[PedalEventIn] = Field(
        ...,
        description="按时间非降序排列的踏板事件（秒数、操作后页码），可为空数组，"
        "长度上限 500；秒数须落在 [0, 演奏总时长) 内",
    )
    tolerance_seconds: float = Field(
        ..., description="非负时间容差（秒，有限数值）；时间差不超过容差才可能匹配"
    )

    @model_validator(mode="after")
    def _validate_pedal_audit(self) -> "RehearsalPedalAuditRequest":
        tol = self.tolerance_seconds
        if tol != tol or tol in (float("inf"), float("-inf")):
            raise ValueError("tolerance_seconds 必须是有限数值（不接受 NaN 或 Infinity）")
        if tol < 0:
            raise ValueError("tolerance_seconds（时间容差）不能为负数")
        for i, event in enumerate(self.pedal_events):
            seconds = event.seconds
            if seconds != seconds or seconds in (float("inf"), float("-inf")):
                raise ValueError(
                    f"第 {i + 1} 个踏板事件秒数必须是有限数值"
                    "（不接受 NaN 或 Infinity）"
                )
            if seconds < 0:
                raise ValueError(
                    f"第 {i + 1} 个踏板事件秒数不能为负数（实际 {seconds:g}）"
                )
        prev: Optional[float] = None
        for i, event in enumerate(self.pedal_events):
            # 非降序预检（严格小于即违规）；权威判定由校对核心按十进制精确比较
            if prev is not None and event.seconds < prev:
                raise ValueError(
                    f"第 {i + 1} 个踏板事件秒数 {event.seconds:g} 早于上一条"
                    f" {prev:g}：踏板事件必须按时间非降序排列"
                )
            prev = event.seconds
        if len(self.pedal_events) > 500:
            raise ValueError(
                f"踏板事件数（{len(self.pedal_events)}）超过上限 500，无法校对"
            )
        return self


class CueAuditOut(BaseModel):
    """一条应翻页提示的校对结果。"""

    cue_index: int = Field(..., description="应翻页提示在提示序列中的 0 基序号")
    expected_seconds: float = Field(..., description="提示的边界时刻（秒）")
    from_page: int = Field(..., description="提示的翻页前页码（1 基）")
    to_page: int = Field(..., description="提示要求翻到的目标页（1 基）")
    matched: bool = Field(..., description="是否匹配到一次踏板操作；false 即漏翻")
    pedal_event_index: Optional[int] = Field(
        ..., description="匹配到的踏板事件 0 基序号；漏翻为 null"
    )
    pedal_seconds: Optional[float] = Field(
        ..., description="匹配踏板事件的操作时刻（秒）；漏翻为 null"
    )
    time_diff_seconds: Optional[float] = Field(
        ..., description="匹配对的时间差绝对值（秒）；漏翻为 null"
    )


class RehearsalPedalAuditResponse(BaseModel):
    id: str
    request: RehearsalPedalAuditRequest
    total_duration: float = Field(
        ..., description="按重复段展开后的演奏总时长（秒）"
    )
    cue_audits: List[CueAuditOut] = Field(
        ..., description="每条应翻页提示的匹配或漏翻结果（按提示顺序）"
    )
    extra_turns: List[int] = Field(
        ..., description="未匹配踏板事件的 0 基序号（多翻，升序）"
    )
    missed_count: int = Field(..., description="漏翻数（未匹配提示数）")
    extra_count: int = Field(..., description="多翻数（未匹配踏板事件数）")
    total_error_count: int = Field(
        ..., description="漏翻与多翻总数（第一项代价，全局最小）"
    )
    total_time_diff_seconds: float = Field(
        ..., description="匹配时间差之和（第二项代价，在漏翻+多翻最少下全局最小）"
    )


# ---------------------------------------- 纸质备用谱页序核对


class PrintAuditRequest(BaseModel):
    """纸质谱页序核对请求体。"""

    model_config = ConfigDict(extra="forbid")

    scanned_pages: List[int] = Field(
        ...,
        description="按实物顺序抄录的每页页码（正整数，允许为空、允许重复，"
        "也允许出现超过规划页数的错页页码），长度上限 500",
    )

    @field_validator("scanned_pages", mode="before")
    @classmethod
    def _reject_non_integer_pages(cls, value: object) -> object:
        """页码必须是严格正整数：拒绝 1.5 之类浮点（pydantic 默认会强转）与布尔。"""
        if isinstance(value, list):
            for page in value:
                if not isinstance(page, int) or isinstance(page, bool):
                    raise ValueError(f"抄录页码必须为正整数（收到 {page!r}）")
        return value


class AuditStepOut(BaseModel):
    """一步修正动作。"""

    action: str = Field(
        ...,
        description="动作：keep 保留、replace 替换错页、remove 移除多页、"
        "insert 补入缺页",
    )
    position: int = Field(
        ...,
        description="原输入 scanned_pages 中的 0 基位置；保留/替换/移除为被处理"
        "实物页下标，补入表示插在该位置之前，等于数组长度即末尾",
    )
    observed_page: Optional[int] = Field(
        ..., description="该位置抄录到的页码；补入无实物页，为 null"
    )
    target_page: Optional[int] = Field(
        ..., description="该位置应有的页码；移除不产生目标页，为 null"
    )


class PrintAuditResponse(BaseModel):
    id: str
    request: PrintAuditRequest
    page_count: int = Field(..., description="规划页数（正确序列 1..page_count）")
    operation_count: int = Field(
        ..., description="最少操作数（替换/移除/补入步数，不含保留）"
    )
    steps: List[AuditStepOut] = Field(
        ..., description="从前到后的确定性修正步骤序列（含保留步）"
    )


# ---------------------------------------- 纸质谱练习页签规划


class PracticeMeasureIn(BaseModel):
    """一个常练小节及其正整数查找权重。"""

    model_config = ConfigDict(extra="forbid")

    measure: int = Field(..., description="常练小节序号（原谱 0 基）")
    weight: int = Field(..., description="查找权重（正整数；越常练权重越大）")

    @field_validator("measure", mode="before")
    @classmethod
    def _reject_non_integer_measure(cls, value: object) -> object:
        """小节序号必须是严格整数：拒绝 1.5 之类浮点（pydantic 默认会强转）
        与布尔；负数/越界等语义由核心逻辑统一报中文错误。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"小节序号必须为整数（收到 {value!r}）")
        return value

    @field_validator("weight", mode="before")
    @classmethod
    def _reject_non_integer_weight(cls, value: object) -> object:
        """权重必须是严格整数：拒绝 1.5 之类浮点与布尔；非正由核心逻辑报错。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"查找权重必须为整数（收到 {value!r}）")
        return value


class PracticeTabsRequest(BaseModel):
    """纸质谱练习页签规划请求体。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    practice_measures: List[PracticeMeasureIn] = Field(
        ...,
        description="常练小节及各自正整数查找权重（小节序号 0 基、互不重复，"
        "长度非空）；同页多小节的权重按方案合并到该页",
    )
    tab_count: int = Field(
        ...,
        alias="K",
        description="页签数 K，必须为 1 到目标页数（合并去重后的有练习目标页数）"
        "之间的整数；页签只能贴在有练习目标的页",
    )

    @field_validator("tab_count", mode="before")
    @classmethod
    def _reject_non_integer_k(cls, value: object) -> object:
        """K 必须是严格整数：拒绝 1.5 之类浮点与布尔；范围由核心逻辑校验。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"页签数必须为整数（收到 {value!r}）")
        return value


class PracticeTargetOut(BaseModel):
    """一个目标页到最近页签的归属。"""

    page: int = Field(..., description="目标页码（1 基，有练习目标的页）")
    weight: int = Field(..., description="该页合并后的查找权重")
    nearest_tab: int = Field(
        ..., description="最近页签页码（1 基）；距离并列取较小页码"
    )
    distance: int = Field(..., description="到最近页签的页码距离（非负）")


class PracticeTabsResponse(BaseModel):
    id: str
    request: PracticeTabsRequest
    page_count: int = Field(..., description="已存方案的规划页数")
    target_page_count: int = Field(
        ..., description="合并去重后的有练习目标页数（页签数的上界）"
    )
    tab_pages: List[int] = Field(
        ..., description="恰好 K 个页签所在页码（1 基，升序）"
    )
    total_cost: int = Field(
        ..., description="全局最小总代价：Σ 目标页权重 × 到最近页签距离"
    )
    targets: List[PracticeTargetOut] = Field(
        ..., description="每个目标页的最近页签归属（按页码升序）"
    )


# ---------------------------------------- 已保存方案的练习路线


class PracticeRouteRequest(BaseModel):
    """练习路线规划请求体。"""

    model_config = ConfigDict(extra="forbid")

    practice_measures: List[int] = Field(
        ..., description="常练小节序号列表（原谱 0 基、互异，1~15 个）"
    )
    start_page: int = Field(..., description="起始页码（1 基）")
    dependencies: List[Tuple[int, int]] = Field(
        default_factory=list,
        description="先后依赖二元组 [先练, 后练]（均为小节序号，两端都必须是"
        "常练小节）；省略或为空表示无依赖",
    )

    @field_validator("practice_measures", mode="before")
    @classmethod
    def _reject_non_integer_measures(cls, value: object) -> object:
        """小节序号必须是严格整数：拒绝 1.5 之类浮点（pydantic 默认会强转）
        与布尔；越界/重复/个数等语义由核心逻辑统一报中文错误。"""
        if isinstance(value, list):
            for m in value:
                if not isinstance(m, int) or isinstance(m, bool):
                    raise ValueError(f"小节序号必须为整数（收到 {m!r}）")
        return value

    @field_validator("start_page", mode="before")
    @classmethod
    def _reject_non_integer_start_page(cls, value: object) -> object:
        """起始页码必须是严格整数：拒绝浮点与布尔；范围由核心逻辑校验。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"起始页码必须为整数（收到 {value!r}）")
        return value

    @field_validator("dependencies", mode="before")
    @classmethod
    def _reject_non_integer_dependencies(cls, value: object) -> object:
        """依赖必须是严格整数小节序号的二元组：拒绝浮点、布尔与长度非 2。"""
        if isinstance(value, list):
            for pair in value:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    raise ValueError(
                        f"依赖必须是小节序号二元组（收到 {pair!r}）"
                    )
                for endpoint in pair:
                    if not isinstance(endpoint, int) or isinstance(endpoint, bool):
                        raise ValueError(
                            f"依赖的小节序号必须为整数（收到 {endpoint!r}）"
                        )
        return value


class PracticeRouteResponse(BaseModel):
    id: str
    request: PracticeRouteRequest
    page_count: int = Field(..., description="已存方案的规划页数")
    order: List[int] = Field(
        ..., description="满足全部依赖的练习顺序（原谱 0 基小节序号）"
    )
    order_pages: List[int] = Field(
        ..., description="练习顺序中各小节所在页码（1 基，与 order 一一对应）"
    )
    segment_distances: List[int] = Field(
        ..., description="各段翻页距离：起始页到首项，及相邻项之间的页码距离"
        "（长度与 order 一致）"
    )
    total_distance: int = Field(
        ..., description="总翻页距离（各段距离之和，全局最小）"
    )


# ---------------------------------------- 纸质乐谱小册子拼版


class BookletImpositionRequest(BaseModel):
    """纸质乐谱小册子骑马订拼版请求体。"""

    model_config = ConfigDict(extra="forbid")

    segment_end_pages: List[int] = Field(
        ...,
        description="严格递增的曲段末页页码（1 基），末项必须等于方案页数；"
        "曲段边界即相邻末页之间（首页从 1 起），曲段不可跨帖",
    )
    max_sheets_per_signature: int = Field(
        ...,
        description="每帖最多印张数（严格正整数），帖容量为 4×该值 页"
        "（含帖尾按 4 页倍数补入的空白页）",
    )

    @field_validator("segment_end_pages", mode="before")
    @classmethod
    def _reject_non_integer_end_pages(cls, value: object) -> object:
        """曲段末页必须是严格整数：拒绝 1.5 之类浮点（pydantic 默认会强转）
        与布尔；严格递增/末项等于方案页数等语义由核心逻辑统一报中文错误。"""
        if isinstance(value, list):
            for end in value:
                if not isinstance(end, int) or isinstance(end, bool):
                    raise ValueError(f"曲段末页页码必须为整数（收到 {end!r}）")
        return value

    @field_validator("max_sheets_per_signature", mode="before")
    @classmethod
    def _reject_non_integer_max_sheets(cls, value: object) -> object:
        """每帖最多印张数必须是严格整数：拒绝浮点与布尔；非正由核心逻辑报错。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"每帖最多印张数必须为整数（收到 {value!r}）")
        return value


class SheetOut(BaseModel):
    """一个印张正反面四个页码位（骑马订排列，空白页为 null）。"""

    sheet_index: int = Field(..., description="印张序号（1 基，按从外到内顺序）")
    front_left: Optional[int] = Field(
        ..., description="外层正面左页页码（最外层为全帖末页）；空白为 null"
    )
    front_right: Optional[int] = Field(
        ..., description="外层正面右页页码（最外层为全帖首页）；空白为 null"
    )
    back_left: Optional[int] = Field(
        ..., description="翻面后背面左页页码（最外层为次页）；空白为 null"
    )
    back_right: Optional[int] = Field(
        ..., description="翻面后背面右页页码（最外层为倒数第二页）；空白为 null"
    )


class SignatureOut(BaseModel):
    """一帖的原谱页范围、补白页数与从外到内印张页码。"""

    signature_index: int = Field(..., description="帖序号（1 基，按原谱先后）")
    start_page: int = Field(..., description="本帖原谱首页页码（1 基，含）")
    end_page: int = Field(..., description="本帖原谱末页页码（1 基，含）")
    content_pages: int = Field(..., description="本帖实际原谱页数")
    blank_pages: int = Field(
        ..., description="帖尾按 4 页倍数补入的空白页数（0~3）"
    )
    sheets: List[SheetOut] = Field(
        ..., description="从外到内各印张正反面左右页码（空白为 null）"
    )


class BookletImpositionResponse(BaseModel):
    id: str
    request: BookletImpositionRequest
    page_count: int = Field(..., description="已存方案的规划页数")
    signature_count: int = Field(..., description="总帖数")
    total_blank_pages: int = Field(..., description="各帖补白页数之和（全局最少）")
    signatures: List[SignatureOut] = Field(
        ..., description="按原谱先后的各帖（其末页序列即分帖决策结果）"
    )


# ---------------------------------------- 巡演装运乐谱册装箱


class TourPackingRequest(BaseModel):
    """巡演装运乐谱册装箱请求体。"""

    model_config = ConfigDict(extra="forbid")

    plan_ids: List[str] = Field(
        ...,
        description="参与装运的已保存方案 ID（2~12 个、互异）；每份方案对应"
        "一册，册厚即该方案结果的页数",
    )
    box_page_capacity: int = Field(
        ..., description="单个运输箱的页容量（正整数），每箱装载页数不得超过"
    )

    @field_validator("plan_ids", mode="before")
    @classmethod
    def _reject_non_string_ids(cls, value: object) -> object:
        """方案 ID 必须是字符串：拒绝数字/布尔/null 等非字符串元素。"""
        if isinstance(value, list):
            for plan_id in value:
                if not isinstance(plan_id, str):
                    raise ValueError(f"方案 ID 必须为字符串（收到 {plan_id!r}）")
        return value

    @field_validator("box_page_capacity", mode="before")
    @classmethod
    def _reject_non_integer_capacity(cls, value: object) -> object:
        """箱体页容量必须是严格整数：拒绝 1.5 之类浮点（pydantic 默认会
        强转）与布尔；非正由核心逻辑统一报中文错误。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"箱体页容量必须为整数（收到 {value!r}）")
        return value

    @model_validator(mode="after")
    def _validate(self) -> "TourPackingRequest":
        if len(self.plan_ids) < 2:
            raise ValueError(
                f"方案数（{len(self.plan_ids)}）少于下限 2：一次装运必须提交"
                " 2~12 个方案"
            )
        if len(self.plan_ids) > 12:
            raise ValueError(
                f"方案数（{len(self.plan_ids)}）超过上限 12：一次装运只能提交"
                " 2~12 个方案"
            )
        duplicates = {pid for pid in self.plan_ids if self.plan_ids.count(pid) > 1}
        if duplicates:
            raise ValueError(
                f"方案 ID 重复（{sorted(duplicates)[0]}），每个方案只能出现一次"
            )
        if self.box_page_capacity < 1:
            raise ValueError(
                "箱体页容量必须为正整数"
                f"（实际 {self.box_page_capacity}）"
            )
        return self


class PackingBookOut(BaseModel):
    """参与装箱的一册乐谱：方案 ID 与其页数（册厚）。"""

    id: str = Field(..., description="已保存方案 ID")
    pages: int = Field(..., description="该方案结果的页数，即本册厚度")


class PackingBoxOut(BaseModel):
    """一个运输箱的装载结果（箱内方案 ID 已升序）。"""

    box_index: int = Field(..., description="箱序号（1 基，按箱列表字典序顺序）")
    plan_ids: List[str] = Field(
        ..., description="本箱内方案 ID（字典序升序），每册整本放入"
    )
    loaded_pages: int = Field(..., description="本箱装载页数（各册页数之和）")
    remaining_capacity: int = Field(
        ..., description="本箱剩余容量（箱体页容量 − 装载页数）"
    )


class TourPackingResponse(BaseModel):
    request: TourPackingRequest
    box_page_capacity: int = Field(..., description="箱体页容量")
    total_pages: int = Field(..., description="全部乐谱册的总页数")
    box_count: int = Field(..., description="最少箱数")
    max_loaded_pages: int = Field(
        ..., description="最重箱的装载页数（同箱数下最小）"
    )
    books: List[PackingBookOut] = Field(
        ..., description="参与装箱的各册（按方案 ID 字典序升序）"
    )
    boxes: List[PackingBoxOut] = Field(
        ...,
        description="分箱结果：箱内方案 ID 升序、箱列表按 ID 元组字典序排序",
    )


# ---------------------------------------- 巡演曲目单谱架换册


class TourStandChangesRequest(BaseModel):
    """巡演曲目单谱架换册请求体。"""

    model_config = ConfigDict(extra="forbid")

    plan_ids: List[str] = Field(
        ...,
        description="按演出顺序排列的已保存方案 ID（允许重复，1~500 项）；"
        "每场需要对应的一册乐谱",
    )
    slot_count: int = Field(
        ..., description="谱架槽位数（1~20 的严格整数），开场全部槽位为空"
    )

    @field_validator("plan_ids", mode="before")
    @classmethod
    def _reject_non_string_ids(cls, value: object) -> object:
        """方案 ID 必须是字符串：拒绝数字/布尔/null 等非字符串元素。"""
        if isinstance(value, list):
            for plan_id in value:
                if not isinstance(plan_id, str):
                    raise ValueError(f"方案 ID 必须为字符串（收到 {plan_id!r}）")
        return value

    @field_validator("slot_count", mode="before")
    @classmethod
    def _reject_non_integer_slot_count(cls, value: object) -> object:
        """槽位数必须是严格整数：拒绝 1.5 之类浮点（pydantic 默认会强转）与布尔。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"谱架槽位数必须为整数（收到 {value!r}）")
        return value

    @model_validator(mode="after")
    def _validate(self) -> "TourStandChangesRequest":
        if len(self.plan_ids) < 1:
            raise ValueError(
                "演出场数（0）少于下限 1：一次巡演必须提交 1~500 场"
            )
        if len(self.plan_ids) > 500:
            raise ValueError(
                f"演出场数（{len(self.plan_ids)}）超过上限 500："
                "一次巡演只能提交 1~500 场"
            )
        if not 1 <= self.slot_count <= 20:
            raise ValueError(
                f"谱架槽位数（{self.slot_count}）必须在 1~20 之间"
            )
        return self


class StandActionOut(BaseModel):
    """一次上架/撤下动作。"""

    plan_id: str = Field(..., description="动作涉及的方案 ID")
    slot: int = Field(..., description="动作发生的槽位编号（1 基）")


class StandChangeShowOut(BaseModel):
    """一场演出的换册明细。"""

    show_index: int = Field(..., description="场次序号（1 基，按演出顺序）")
    plan_id: str = Field(..., description="本场所需的方案 ID")
    load: Optional[StandActionOut] = Field(
        ..., description="上架动作；本场所需册已在架上时为 null"
    )
    unload: Optional[StandActionOut] = Field(
        ..., description="撤下动作；有空槽或无需上架时为 null"
    )
    slots_after: List[Optional[str]] = Field(
        ...,
        description="本场换册后各槽位上的方案 ID（按槽位编号 1..k 顺序），"
        "空槽为 null",
    )


class TourStandChangesResponse(BaseModel):
    request: TourStandChangesRequest
    slot_count: int = Field(..., description="谱架槽位数")
    show_count: int = Field(..., description="演出场数")
    total_loads: int = Field(..., description="总上架次数（最少）")
    shows: List[StandChangeShowOut] = Field(
        ..., description="按演出顺序的每场换册明细"
    )


# ---------------------------------------------------------------- 接口


@app.post("/api/v1/plans", response_model=PlanResponse, status_code=201)
def create_plan(payload: PlanRequest) -> PlanResponse:
    """提交一次翻页规划，返回最优方案并保存到本地。"""
    try:
        plan = plan_pages(
            heights=payload.measure_heights,
            silences=payload.silence_seconds,
            page_height=payload.page_height,
            min_turn_time=payload.min_turn_time,
            required_breaks=payload.required_breaks,
            forbidden_breaks=payload.forbidden_breaks,
        )
    except PlanningError as exc:
        # 单小节超高 / 必翻点静默不足 / 无合法方案：语义错误，明确报错
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = _plan_to_result(plan)
    assert store is not None
    plan_id = store.save(payload.model_dump(), result.model_dump())
    record = store.get(plan_id)
    assert record is not None
    return PlanResponse(
        id=record["id"],
        created_at=record["created_at"],
        request=payload,
        result=result,
    )


@app.get("/api/v1/plans/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: str) -> PlanResponse:
    """按 ID 查询已保存的规划结果。"""
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")
    return PlanResponse(
        id=record["id"],
        created_at=record["created_at"],
        request=PlanRequest(**record["request"]),
        result=PlanResult(**record["result"]),
    )


@app.post(
    "/api/v1/plans/{plan_id}/rehearsal/locate",
    response_model=RehearsalLocateResponse,
)
def locate_rehearsal_timestamps(
    plan_id: str, payload: RehearsalLocateRequest
) -> RehearsalLocateResponse:
    """按规划 ID 对排练录音时间戳做定位。

    依据提交的每小节拍数、速度与重复段，按谱面顺序展开实际演奏序列，
    以左闭右开区间把每个时间戳定位到原谱小节、重复遍号、拍数偏移和页码。
    """
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")

    plan_request = PlanRequest(**record["request"])
    plan_result = PlanResult(**record["result"])
    page_starts = [p.start_measure for p in plan_result.pages]

    try:
        rehearsal = prepare_rehearsal(
            measure_count=len(plan_request.measure_heights),
            beats_per_measure=payload.beats_per_measure,
            tempo_bpm=payload.tempo_bpm,
            repeats=[
                (r.start_measure, r.end_measure, r.play_count)
                for r in payload.repeats
            ],
            page_starts=page_starts,
        )
        locations = rehearsal.locate_all(payload.timestamps)
    except RehearsalError as exc:
        # 数组长度不符 / 重复段重叠 / 时间戳越界等语义错误：明确报错
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RehearsalLocateResponse(
        id=record["id"],
        request=payload,
        total_duration=rehearsal.total_duration,
        locations=[_location_to_out(loc) for loc in locations],
    )


@app.post(
    "/api/v1/plans/{plan_id}/rehearsal/cues",
    response_model=RehearsalCuesResponse,
)
def plan_rehearsal_cues(
    plan_id: str, payload: RehearsalCuesRequest
) -> RehearsalCuesResponse:
    """为已保存方案生成排练翻页提示。

    复用定位接口的演奏顺序与时间轴：按每小节拍数、速度与互不相交的重复段
    展开实际演奏序列，仅在相邻两次发声所属页码变化时生成提示，给出边界
    秒数、前后页码、前后原谱小节与遍次、前进/回翻方向，并用前一小节后的
    静默秒数与方案的最低翻页时间比较给出是否充足；演奏结束不生成提示。
    """
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")

    plan_request = PlanRequest(**record["request"])
    plan_result = PlanResult(**record["result"])
    page_starts = [p.start_measure for p in plan_result.pages]

    try:
        rehearsal = prepare_rehearsal(
            measure_count=len(plan_request.measure_heights),
            beats_per_measure=payload.beats_per_measure,
            tempo_bpm=payload.tempo_bpm,
            repeats=[
                (r.start_measure, r.end_measure, r.play_count)
                for r in payload.repeats
            ],
            page_starts=page_starts,
        )
        cues = rehearsal.turn_cues(
            silences=plan_request.silence_seconds,
            min_turn_time=plan_request.min_turn_time,
        )
    except RehearsalError as exc:
        # 数组长度不符 / 重复段重叠等语义错误：明确报错
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RehearsalCuesResponse(
        id=record["id"],
        request=payload,
        min_turn_time=plan_request.min_turn_time,
        total_duration=rehearsal.total_duration,
        cues=[_cue_to_out(cue) for cue in cues],
    )


@app.post(
    "/api/v1/plans/{plan_id}/rehearsal/turn-slots",
    response_model=RehearsalTurnSlotsResponse,
)
def plan_rehearsal_turn_slots(
    plan_id: str, payload: RehearsalTurnSlotsRequest
) -> RehearsalTurnSlotsResponse:
    """为已保存方案挑选每个跨页边界上的翻页动作空档。

    复用定位/提示接口的演奏顺序与时间轴：按实际演奏顺序展开重复段，仅对
    相邻演奏小节跨页的边界，在前一个演奏小节内按左右手忙碌区间求连续
    空闲窗，挑选能容纳完整翻页动作（turn_seconds）的最晚开始时刻，左右
    手并列时优先左手；找不到可用窗的边界保留在结果中并标记为不可行。
    """
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")

    plan_request = PlanRequest(**record["request"])
    plan_result = PlanResult(**record["result"])
    page_starts = [p.start_measure for p in plan_result.pages]

    try:
        rehearsal = prepare_rehearsal(
            measure_count=len(plan_request.measure_heights),
            beats_per_measure=payload.beats_per_measure,
            tempo_bpm=payload.tempo_bpm,
            repeats=[
                (r.start_measure, r.end_measure, r.play_count)
                for r in payload.repeats
            ],
            page_starts=page_starts,
        )
        slots = rehearsal.turn_slots(
            hands_busy=[
                (list(m.left), list(m.right)) for m in payload.hands_busy
            ],
            turn_seconds=payload.turn_seconds,
        )
    except RehearsalError as exc:
        # 数组长度不符 / 重复段重叠 / 忙碌区间非法等语义错误：明确报错
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RehearsalTurnSlotsResponse(
        id=record["id"],
        request=payload,
        total_duration=rehearsal.total_duration,
        slots=[_slot_to_out(slot) for slot in slots],
    )


@app.post(
    "/api/v1/plans/{plan_id}/rehearsal/pedal-audit",
    response_model=RehearsalPedalAuditResponse,
)
def audit_rehearsal_pedal_log(
    plan_id: str, payload: RehearsalPedalAuditRequest
) -> RehearsalPedalAuditResponse:
    """为已保存方案校对排练踏板日志。

    校对内部把每小节拍数、速度（BPM）按输入数值的十进制字面量构造有理数，
    按重复段展开精确重建含回跳回翻的应翻页提示时刻（不经浮点累加）；踏板
    事件秒数与非负容差同样按十进制字面量转换，匹配资格（目标页相同且时间差
    不超过容差）与累计时间差均为精确有理数比较，不使用浮点或 epsilon。依次
    最小化漏翻与多翻总数、匹配时间差之和，仍并列取匹配的踏板事件序号序列
    字典序最小。返回每条提示的匹配或漏翻、未匹配事件的多翻标记及两项代价
    （响应数值仍为 JSON number）。事件秒数须严格落在 [0, 演奏总时长) 内且
    按时间非降序（精确比较：略早于终点可接受、略逆序即 422），页码越出
    1..规划页数同样返回 422。
    """
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")

    plan_request = PlanRequest(**record["request"])
    plan_result = PlanResult(**record["result"])
    page_starts = [p.start_measure for p in plan_result.pages]

    try:
        result = audit_pedal_log(
            measure_count=len(plan_request.measure_heights),
            beats_per_measure=payload.beats_per_measure,
            tempo_bpm=payload.tempo_bpm,
            repeats=[
                (r.start_measure, r.end_measure, r.play_count)
                for r in payload.repeats
            ],
            page_starts=page_starts,
            page_count=plan_result.page_count,
            events=[
                PedalEvent(seconds=e.seconds, page_after=e.page_after)
                for e in payload.pedal_events
            ],
            tolerance_seconds=payload.tolerance_seconds,
        )
    except (RehearsalError, PedalAuditError) as exc:
        # 数组长度不符 / 重复段重叠 / 事件越界或非降序 / 容差为负：422
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return RehearsalPedalAuditResponse(
        id=record["id"],
        request=payload,
        total_duration=result.total_duration,
        cue_audits=[
            CueAuditOut(
                cue_index=a.cue_index,
                expected_seconds=a.expected_seconds,
                from_page=a.from_page,
                to_page=a.to_page,
                matched=a.matched,
                pedal_event_index=a.pedal_event_index,
                pedal_seconds=a.pedal_seconds,
                time_diff_seconds=a.time_diff_seconds,
            )
            for a in result.cue_audits
        ],
        extra_turns=result.extra_turns,
        missed_count=result.missed_count,
        extra_count=result.extra_count,
        total_error_count=result.total_error_count,
        total_time_diff_seconds=result.total_time_diff_seconds,
    )


@app.post("/api/v1/plans/{plan_id}/print-audit", response_model=PrintAuditResponse)
def audit_plan_print_pages(
    plan_id: str, payload: PrintAuditRequest
) -> PrintAuditResponse:
    """为已保存规划核对纸质备用谱的实物页序。

    以规划的 1..page_count 为正确顺序，把按实物顺序抄录的 scanned_pages
    （可为空、可含重复页）按补入缺页、移除多页、替换错页修正，求最少操作数
    与一条确定的修正步骤序列；同成本方案依次优先保留、替换、移除、补入。
    """
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")

    plan_result = PlanResult(**record["result"])
    try:
        result = audit_print_pages(
            page_count=plan_result.page_count,
            scanned_pages=payload.scanned_pages,
        )
    except PrintAuditError as exc:
        # 页码非正整数 / 含布尔值 / 规划或实物页数超限：语义错误，明确报错
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PrintAuditResponse(
        id=record["id"],
        request=payload,
        page_count=result.page_count,
        operation_count=result.operation_count,
        steps=[
            AuditStepOut(
                action=step.action,
                position=step.position,
                observed_page=step.observed_page,
                target_page=step.target_page,
            )
            for step in result.steps
        ],
    )


@app.post("/api/v1/plans/{plan_id}/practice-tabs", response_model=PracticeTabsResponse)
def plan_practice_tabs_for_plan(
    plan_id: str, payload: PracticeTabsRequest
) -> PracticeTabsResponse:
    """为已保存方案规划纸质谱练习页签。

    按方案把每个常练小节归到其所在页，同页多个小节的正整数查找权重合并；
    页签只能贴在有练习目标的页。每个目标页从最近页签开始查找（距离并列取
    较小页码），代价为页码距离乘该页权重；在恰好 K 个不同页签下全局最小化
    总代价，同代价取升序页签列表字典序最小者。
    """
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")

    plan_request = PlanRequest(**record["request"])
    plan_result = PlanResult(**record["result"])
    page_starts = [p.start_measure for p in plan_result.pages]

    try:
        result = plan_practice_tabs(
            page_starts=page_starts,
            measure_count=len(plan_request.measure_heights),
            entries=[(m.measure, m.weight) for m in payload.practice_measures],
            tab_count=payload.tab_count,
        )
    except PracticeTabError as exc:
        # 空目标 / 小节越界或重复 / 权重非正 / K 越界 / 目标过多：语义错误
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PracticeTabsResponse(
        id=record["id"],
        request=payload,
        page_count=plan_result.page_count,
        target_page_count=result.target_page_count,
        tab_pages=result.tab_pages,
        total_cost=result.total_cost,
        targets=[
            PracticeTargetOut(
                page=t.page,
                weight=t.weight,
                nearest_tab=t.nearest_tab,
                distance=t.distance,
            )
            for t in result.targets
        ],
    )


@app.post(
    "/api/v1/plans/{plan_id}/practice-route",
    response_model=PracticeRouteResponse,
)
def plan_practice_route_for_plan(
    plan_id: str, payload: PracticeRouteRequest
) -> PracticeRouteResponse:
    """为已保存方案规划练习路线。

    按方案把 1~15 个互异的常练小节映射到所在页，在满足全部先后依赖（二元组
    [先练, 后练]）的前提下排出练习顺序，使"起始页到首项及相邻项之间的页码
    距离之和"最小；同成本取小节序号序列字典序最小者，返回顺序、各段翻页
    距离与总距离。
    """
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")

    plan_request = PlanRequest(**record["request"])
    plan_result = PlanResult(**record["result"])
    page_starts = [p.start_measure for p in plan_result.pages]

    try:
        result = plan_practice_route(
            page_starts=page_starts,
            measure_count=len(plan_request.measure_heights),
            measures=payload.practice_measures,
            start_page=payload.start_page,
            dependencies=[(a, b) for a, b in payload.dependencies],
        )
    except PracticeRouteError as exc:
        # 空目标 / 小节或页码越界 / 重复小节 / 依赖指向非目标小节 / 依赖成环
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PracticeRouteResponse(
        id=record["id"],
        request=payload,
        page_count=plan_result.page_count,
        order=result.order,
        order_pages=result.order_pages,
        segment_distances=result.segment_distances,
        total_distance=result.total_distance,
    )


@app.post(
    "/api/v1/plans/{plan_id}/booklet-imposition",
    response_model=BookletImpositionResponse,
)
def impose_plan_booklet(
    plan_id: str, payload: BookletImpositionRequest
) -> BookletImpositionResponse:
    """为已保存方案做纸质乐谱小册子骑马订拼版。

    页数从已存规划按 ID 读取。请求给出严格递增的曲段末页页码（末项等于
    方案页数，曲段不可跨帖、每帖至少含一段）与每帖最多印张数（帖容量为
    4×该值 页，帖尾按 4 页倍数补空白）。用动态规划在合法分帖中依次最小化
    总空白页、帖数，同分取各帖末页序列字典序最小。返回每帖原谱页范围、
    空白页数，以及从外到内各印张正反面左右页码；骑马订外层正面为
    末页/首页、背面为次页/倒数第二页，空白以 null 表示。
    """
    assert store is not None
    record = store.get(plan_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"规划结果不存在：{plan_id}")

    plan_result = PlanResult(**record["result"])
    try:
        result = impose_booklet(
            page_count=plan_result.page_count,
            segment_end_pages=payload.segment_end_pages,
            max_sheets_per_signature=payload.max_sheets_per_signature,
        )
    except ImpositionError as exc:
        # 末页非严格递增 / 末项不等于方案页数 / 单段超容量 / 页数超限：422
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return BookletImpositionResponse(
        id=record["id"],
        request=payload,
        page_count=result.page_count,
        signature_count=result.signature_count,
        total_blank_pages=result.total_blank_pages,
        signatures=[
            SignatureOut(
                signature_index=sig.signature_index,
                start_page=sig.start_page,
                end_page=sig.end_page,
                content_pages=sig.content_pages,
                blank_pages=sig.blank_pages,
                sheets=[
                    SheetOut(
                        sheet_index=sheet.sheet_index,
                        front_left=sheet.front_left,
                        front_right=sheet.front_right,
                        back_left=sheet.back_left,
                        back_right=sheet.back_right,
                    )
                    for sheet in sig.sheets
                ],
            )
            for sig in result.signatures
        ],
    )


@app.post("/api/v1/tour/packing", response_model=TourPackingResponse)
def pack_tour_shipment(payload: TourPackingRequest) -> TourPackingResponse:
    """为巡演装运把已保存方案的乐谱册装入运输箱。

    提交 2~12 个互异的已保存方案 ID 与正整数箱体页容量：每份方案对应一册，
    册厚即该方案结果的页数；每册只能整本放入一箱，每箱至少一册且装载页数
    不超过容量。先求最少箱数，再使最重箱的装载页数最小；仍并列时，把各箱
    内方案 ID 升序、箱列表再按 ID 元组排序，取字典序最小的分箱。返回每箱
    的方案 ID、装载页数与剩余容量。该接口只读取已保存方案，不写入新方案。
    """
    assert store is not None
    missing: List[str] = []
    books: List[Tuple[str, int]] = []
    for plan_id in payload.plan_ids:
        record = store.get(plan_id)
        if record is None:
            missing.append(plan_id)
            continue
        page_count = PlanResult(**record["result"]).page_count
        books.append((plan_id, page_count))

    if missing:
        raise HTTPException(
            status_code=404,
            detail="以下方案不存在，无法装箱：" + "、".join(missing),
        )

    try:
        result = pack_tour_books(
            books=books, capacity=payload.box_page_capacity
        )
    except TourPackingError as exc:
        # 方案数/重复等已在请求层拦截；单册超容量等语义错误在此明确报错
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return TourPackingResponse(
        request=payload,
        box_page_capacity=result.capacity,
        total_pages=result.total_pages,
        box_count=result.box_count,
        max_loaded_pages=result.max_loaded_pages,
        books=[
            PackingBookOut(id=plan_id, pages=pages)
            for plan_id, pages in zip(result.plan_ids, result.book_pages)
        ],
        boxes=[
            PackingBoxOut(
                box_index=box.box_index,
                plan_ids=box.plan_ids,
                loaded_pages=box.loaded_pages,
                remaining_capacity=box.remaining_capacity,
            )
            for box in result.boxes
        ],
    )


@app.post("/api/v1/tour/stand-changes", response_model=TourStandChangesResponse)
def plan_tour_stand_changes(
    payload: TourStandChangesRequest,
) -> TourStandChangesResponse:
    """为巡演曲目单规划谱架换册。

    提交按演出顺序排列的 1~500 个已保存方案 ID（允许重复，每场需要对应的
    一册乐谱）与 1~20 个谱架槽位：开场所有槽位为空，每册占一槽，每场所需
    册须在架上。以总上架次数最少为目标逐场模拟：缺册时先用编号最小的空槽；
    架满则撤下未来最晚再次使用的册（永不再用视为最晚，并列取方案 ID 字典
    序最小者），再把所需册放上腾出的槽位。返回每场的上架/撤下动作、场后各
    槽位方案 ID 与总上架次数。该接口只读取已保存方案，不写入新方案。
    """
    assert store is not None
    missing: List[str] = []
    checked: set[str] = set()
    for plan_id in payload.plan_ids:
        if plan_id in checked:
            continue
        checked.add(plan_id)
        if store.get(plan_id) is None:
            missing.append(plan_id)
    if missing:
        raise HTTPException(
            status_code=404,
            detail="以下方案不存在，无法规划谱架换册：" + "、".join(missing),
        )

    try:
        result = plan_stand_changes(
            plan_ids=payload.plan_ids, slot_count=payload.slot_count
        )
    except StandChangeError as exc:
        # 场数/槽位数/ID 类型等已在请求层拦截；此处为兜底语义错误
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _action_out(action: Optional[StandAction]) -> Optional[StandActionOut]:
        if action is None:
            return None
        return StandActionOut(plan_id=action.plan_id, slot=action.slot)

    return TourStandChangesResponse(
        request=payload,
        slot_count=result.slot_count,
        show_count=len(result.shows),
        total_loads=result.total_loads,
        shows=[
            StandChangeShowOut(
                show_index=show.show_index,
                plan_id=show.plan_id,
                load=_action_out(show.load),
                unload=_action_out(show.unload),
                slots_after=show.slots_after,
            )
            for show in result.shows
        ],
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
