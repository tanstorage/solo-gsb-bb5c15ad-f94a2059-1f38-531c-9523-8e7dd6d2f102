"""纸质乐谱小册子拼版核心（骑马订分帖）。

背景
====
一份已保存的翻页方案把乐谱分成页码 ``1..page_count`` 的若干页。纸质印制时，
要把若干连续**曲段**（以"曲段末页页码"给出，末项恰为方案页数）分组成
若干**帖**（signature）：一张大纸正反双面印刷、对折后成为一个印张（4 个
页面），若干印张从外到内叠在一起、中缝骑马装订成一帖。

约束
====
- 曲段不可跨帖：一个曲段的所有页必须完整落在同一帖内；
- 每帖至少包含一个曲段；
- 每帖的印张数不超过 ``max_sheets_per_signature``，即帖内总页数（含补入的
  空白页）不超过 ``4 × max_sheets_per_signature``；
- 每帖总页数必须是 4 的倍数：帖尾空白页按 4 页倍数补（补 0~3 页空白）。

若某个曲段自身页数已超过帖容量，因曲段不可拆，任何分帖方案都无法容纳，
直接报错（接口层映射为 422）。

优化目标（动态规划，按优先级）
==============================
在所有合法分帖中依次比较：

1. **总空白页最少**（Σ 各帖补空白页数）；
2. 总空白页相同时，**帖数最少**；
3. 仍并列时，取**各帖末页序列字典序最小**的分帖（先比第一帖末页，
   再依次比后续；末页即曲段末页页码，序列末项恒为方案页数）。

设曲段末页为 ``e[1..k]``（补 ``e[0]=0``），``dp[i]`` 表示覆盖前 i 个曲段的
最优分帖，代价为三元组 ``(总空白页, 帖数, 各帖末页序列)``，按目标优先级
做字典序比较（Python 元组比较恰为该顺序）：

    dp[0]  = (0, 0, ())
    dp[i]  = min  over a 使 e[i] - e[a-1] ≤ 容量:
             ( 补白(e[i] - e[a-1]) + dp[a-1].空白,
               dp[a-1].帖数 + 1,
               dp[a-1].末页序列 + (e[i],) )

``e`` 严格递增，可行的 a 构成随 i 右移的连续区间（二分定位），整体 O(k²)；
纸质模块页数上限 MAX_PAGES = 500，规模充足。重构时记录每个 i 选中的 a，
回溯即得各帖。

骑马订页码排列
==============
一帖共 ``P`` 页（P 为 4 的倍数），印张数 ``S = P/4``，印张按**从外到内**
``t = 0..S-1`` 编号。对折后外层正面左侧是全帖末页、右侧是首页；翻到背面
左侧是次页、右侧是倒数第二页；向内一层 t：

    正面（外层朝上）：左 = P - 2t，  右 = 1 + 2t
    背面（翻面后）  ：左 = 2 + 2t，  右 = P - 1 - 2t

其中属于帖尾补入空白的页码（大于该帖实际原谱页数）以 ``None`` 表示。

本模块仅依赖标准库，便于脱离 Web 层直接单测。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

#: 纸质模块的规划页数上限（与页序核对、练习页签模块一致）
MAX_PAGES = 500

#: DP 代价：(总空白页, 帖数, 各帖末页序列)
_Cost = Tuple[int, int, Tuple[int, ...]]


class ImpositionError(ValueError):
    """小册子拼版输入不合法：曲段末页非严格递增/末项不等于方案页数、
    每帖印张数非正整数、单段超容量或规划页数超限等。"""


@dataclass(frozen=True)
class Sheet:
    """一个印张（一张大纸对折成 4 页），按骑马订排列给出四个页码位。

    页码均为该帖所属原谱的**全局 1 基页码**；帖尾补入的空白页为 None。

    :param sheet_index: 印张序号（1 基，按从外到内顺序）
    :param front_left: 外层正面左页页码
    :param front_right: 外层正面右页页码
    :param back_left: 背面翻面后左页页码
    :param back_right: 背面翻面后右页页码
    """

    sheet_index: int
    front_left: Optional[int]
    front_right: Optional[int]
    back_left: Optional[int]
    back_right: Optional[int]


@dataclass(frozen=True)
class Signature:
    """一帖：连续若干曲段拼版的结果。"""

    signature_index: int  # 帖序号（1 基，按原谱先后）
    start_page: int  # 本帖原谱首页页码（全局 1 基，含）
    end_page: int  # 本帖原谱末页页码（全局 1 基，含）
    content_pages: int  # 本帖实际原谱页数
    blank_pages: int  # 帖尾按 4 页倍数补入的空白页数
    sheets: List[Sheet]  # 从外到内各印张


@dataclass(frozen=True)
class Imposition:
    """小册子拼版结果。"""

    page_count: int  # 方案页数
    segment_end_pages: List[int]  # 原样输入的曲段末页页码（严格递增）
    max_sheets_per_signature: int  # 每帖最多印张数
    signature_count: int  # 总帖数
    total_blank_pages: int  # 总空白页
    signatures: List[Signature]  # 按原谱先后的各帖


def _blank_pages(content_pages: int) -> int:
    """帖尾补到 4 的倍数所需的空白页数（0~3）。"""
    return (-content_pages) % 4


def _validate(
    page_count: int,
    segment_end_pages: Sequence[int],
    max_sheets_per_signature: int,
) -> Tuple[List[int], int]:
    """校验全部输入，返回 (曲段末页列表, 每帖页数容量)。

    :raises ImpositionError: 任一约束不满足
    """
    if not isinstance(page_count, int) or isinstance(page_count, bool):
        raise ImpositionError("规划页数必须为整数")
    if page_count < 1:
        raise ImpositionError(f"规划页数必须为正整数（实际 {page_count}）")
    if page_count > MAX_PAGES:
        raise ImpositionError(
            f"规划页数（{page_count}）超过纸质拼版上限 {MAX_PAGES}，无法拼版"
        )

    if not isinstance(max_sheets_per_signature, int) or isinstance(
        max_sheets_per_signature, bool
    ):
        raise ImpositionError(
            f"每帖最多印张数必须为整数（收到 {max_sheets_per_signature!r}）"
        )
    if max_sheets_per_signature < 1:
        raise ImpositionError(
            "每帖最多印张数必须为正整数"
            f"（实际 {max_sheets_per_signature}）"
        )

    if not isinstance(segment_end_pages, Sequence) or isinstance(
        segment_end_pages, (str, bytes)
    ):
        raise ImpositionError("segment_end_pages 必须是曲段末页页码数组")
    if not segment_end_pages:
        raise ImpositionError(
            "segment_end_pages 不能为空：至少需要一个曲段，且末页等于方案页数"
        )
    if len(segment_end_pages) > page_count:
        # 每段至少含 1 页，曲段数不可能超过页数
        raise ImpositionError(
            f"曲段数（{len(segment_end_pages)}）不能超过方案页数（{page_count}）"
        )

    ends: List[int] = []
    prev = 0
    for idx, end in enumerate(segment_end_pages):
        if not isinstance(end, int) or isinstance(end, bool):
            raise ImpositionError(
                f"第 {idx + 1} 个曲段末页页码必须为正整数（收到 {end!r}），"
                "不接受布尔值或小数"
            )
        if end < 1:
            raise ImpositionError(
                f"第 {idx + 1} 个曲段末页页码必须为正整数（实际 {end}）"
            )
        if end <= prev:
            raise ImpositionError(
                f"曲段末页页码必须严格递增：第 {idx} 个为 {prev}、"
                f"第 {idx + 1} 个为 {end}，不满足严格递增"
            )
        ends.append(end)
        prev = end

    if ends[-1] != page_count:
        raise ImpositionError(
            f"曲段末页末项（{ends[-1]}）必须等于方案页数（{page_count}）"
        )

    capacity = 4 * max_sheets_per_signature
    # 曲段不可跨帖：单段超容量时任何分帖都无法容纳，直接判定无解。
    boundaries = [0] + ends
    for idx in range(1, len(boundaries)):
        length = boundaries[idx] - boundaries[idx - 1]
        if length > capacity:
            raise ImpositionError(
                f"第 {idx} 个曲段（原谱页 {boundaries[idx - 1] + 1}.."
                f"{boundaries[idx]}，共 {length} 页）超过每帖容量"
                f"（{max_sheets_per_signature} 个印张 = {capacity} 页），"
                "曲段不可跨帖，无法拼版"
            )
    return ends, capacity


def _build_signature(
    signature_index: int, start_page: int, end_page: int
) -> Signature:
    """把一帖的连续原谱页排成骑马订印张序列（从外到内）。"""
    content_pages = end_page - start_page + 1
    blanks = _blank_pages(content_pages)
    total_pages = content_pages + blanks  # 4 的倍数
    base = start_page - 1  # 帖内局部页码 -> 全局页码的偏移

    def global_page(local: int) -> Optional[int]:
        # 帖尾补入的空白（局部页码大于实际页数）为 None。
        return base + local if local <= content_pages else None

    sheets: List[Sheet] = []
    for t in range(total_pages // 4):
        sheets.append(
            Sheet(
                sheet_index=t + 1,
                front_left=global_page(total_pages - 2 * t),
                front_right=global_page(1 + 2 * t),
                back_left=global_page(2 + 2 * t),
                back_right=global_page(total_pages - 1 - 2 * t),
            )
        )
    return Signature(
        signature_index=signature_index,
        start_page=start_page,
        end_page=end_page,
        content_pages=content_pages,
        blank_pages=blanks,
        sheets=sheets,
    )


def impose_booklet(
    page_count: int,
    segment_end_pages: Sequence[int],
    max_sheets_per_signature: int,
) -> Imposition:
    """为已保存翻页方案做纸质乐谱小册子骑马订拼版。

    :param page_count: 已存方案的页数，正确页码即 ``1..page_count``
    :param segment_end_pages: 严格递增的曲段末页页码，末项必须等于
        ``page_count``；曲段边界即相邻末页之间（首页从 1 起）
    :param max_sheets_per_signature: 每帖最多印张数（正整数），帖容量为
        ``4 × 该值`` 页（含帖尾补白）
    :return: 按目标优先级最优的分帖与各帖从外到内印张页码
    :raises ImpositionError: 末页非严格递增/非正整数、末项不等于方案页数、
        每帖印张数非正整数、单段超容量或规划页数超过 MAX_PAGES
    """
    ends, capacity = _validate(
        page_count, segment_end_pages, max_sheets_per_signature
    )
    k = len(ends)

    # 曲段边界（页码）：boundaries[i] 为前 i 个曲段的末页（boundaries[0] = 0）。
    boundaries = [0] + ends

    # ---- 分帖 DP：dp[i] = 覆盖前 i 个曲段的最优代价 ----
    # 代价三元组 (总空白页, 帖数, 各帖末页序列) 直接按目标优先级字典序比较。
    dp: List[Optional[_Cost]] = [None] * (k + 1)
    parent: List[int] = [0] * (k + 1)  # 最优转移中本帖之前已覆盖的曲段数
    dp[0] = (0, 0, ())

    for i in range(1, k + 1):
        # 本帖覆盖曲段 a..i，原谱页为 boundaries[a-1]+1 .. boundaries[i]，
        # 合法当且仅当实际页数 <= 帖容量（补白后总页数仍是不超过容量的 4
        # 的倍数，因为容量本身为 4 的倍数）。
        # boundaries 严格递增：boundaries[a-1] >= boundaries[i] - capacity
        # 给出 a 的下界，可行 a 为连续区间，二分定位。
        first_prev = bisect_left(
            boundaries, boundaries[i] - capacity, 0, i
        )
        a_lo = first_prev + 1

        best: Optional[_Cost] = None
        best_a = a_lo
        for a in range(a_lo, i + 1):
            prev_cost = dp[a - 1]
            assert prev_cost is not None
            content = boundaries[i] - boundaries[a - 1]
            candidate: _Cost = (
                prev_cost[0] + _blank_pages(content),
                prev_cost[1] + 1,
                prev_cost[2] + (boundaries[i],),
            )
            if best is None or candidate < best:
                best = candidate
                best_a = a
        assert best is not None
        dp[i] = best
        parent[i] = best_a

    assert dp[k] is not None
    total_blanks, signature_count, _ = dp[k]

    # ---- 回溯分帖，按原谱先后组装各帖 ----
    cuts: List[Tuple[int, int]] = []  # (首曲段序号, 末曲段序号)，均 1 基
    i = k
    while i > 0:
        a = parent[i]
        cuts.append((a, i))
        i = a - 1
    cuts.reverse()

    signatures: List[Signature] = []
    for sig_idx, (a, i) in enumerate(cuts, start=1):
        signatures.append(
            _build_signature(
                signature_index=sig_idx,
                start_page=boundaries[a - 1] + 1,
                end_page=boundaries[i],
            )
        )

    return Imposition(
        page_count=page_count,
        segment_end_pages=ends,
        max_sheets_per_signature=max_sheets_per_signature,
        signature_count=signature_count,
        total_blank_pages=total_blanks,
        signatures=signatures,
    )
