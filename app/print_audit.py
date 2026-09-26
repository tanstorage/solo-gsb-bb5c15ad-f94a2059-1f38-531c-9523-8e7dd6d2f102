"""纸质备用谱页序核对核心。

背景
====
演出时除电子谱外还备有一套纸质谱，按规划应有页码 ``1..page_count`` 依次排列。
实际翻阅前，按**实物顺序**把每页上印的页码抄录为 ``scanned_pages``：可能漏抄
（缺页）、重复（同一页印了多份/多抄）或页码本身错误（错页），实物页数也可能
与规划页数不一致。

本模块以规划的 ``1..page_count`` 为正确顺序，求把实物序列修正为正确序列的
**最少操作数**，以及一条确定的修正步骤序列。允许四种对齐动作：

- ``keep`` 保留（成本 0，不计入操作数）：实物该页与目标页码一致，无需处理；
- ``replace`` 替换错页（成本 1）：把该位置的错页换成目标页码；
- ``remove`` 移除多页（成本 1）：抽走该位置多余/错误的实物页；
- ``insert`` 补入缺页（成本 1）：在指定位置之前插入目标页码的缺页。

这正是以 1..page_count 为目标序列的 Levenshtein 编辑距离（保留/替换/移除/
补入，替换各计一次），用后缀动态规划 ``O(m·n)`` 求解（m、n 均 ≤ 500）。

确定性（同成本方案的取舍）
==========================
最少操作数可能对应多条对齐方案。在 DP 重构的**每一步**上，若多种动作都能达到
同样的剩余最优成本，按以下顺序优先采用：

1. 保留 keep；2. 替换 replace；3. 移除 remove；4. 补入 insert。

步骤按实物从前到后的顺序给出，因此该序列即一份确定的、可逐步照做的修正清单。

每个步骤都给出**原输入数组中的 0 基位置** position：

- 保留/替换/移除：position 是被处理实物页在原 ``scanned_pages`` 中的下标；
- 补入：position 表示插在原输入该位置之前，``position == 数组长度`` 即末尾补页。

observed_page 为该位置抄录到的页码（补入时为 null）；target_page 为该位置
应有页码（移除时为 null）。

本模块仅依赖标准库，便于脱离 Web 层直接单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

#: 规划页数与实物页数的共同上限
MAX_PAGES = 500

#: 四种对齐动作的标识（同成本时的优先顺序即 keep < replace < remove < insert）
KEEP = "keep"
REPLACE = "replace"
REMOVE = "remove"
INSERT = "insert"


class PrintAuditError(ValueError):
    """纸质谱页序核对输入不合法：页码非正整数、含布尔值或长度超限等。"""


@dataclass(frozen=True)
class AuditStep:
    """一步修正动作。

    :param action: ``keep`` 保留 / ``replace`` 替换 / ``remove`` 移除 /
        ``insert`` 补入
    :param position: 原输入 ``scanned_pages`` 中的 0 基位置；补入时表示插在
        该位置之前，等于原数组长度即末尾
    :param observed_page: 该位置抄录到的页码；补入没有实物页，为 None
    :param target_page: 该位置应有的页码；移除不产生目标页，为 None
    """

    action: str
    position: int
    observed_page: int | None
    target_page: int | None


@dataclass(frozen=True)
class AuditResult:
    """页序核对结果。"""

    page_count: int  # 规划页数（正确序列长度）
    scanned_pages: List[int]  # 原样抄录的实物页码序列
    operation_count: int  # 最少操作数（替换/移除/补入的步数，不含保留）
    steps: List[AuditStep]  # 从前到后的确定性修正步骤序列


def _validate(page_count: int, scanned_pages: Sequence[int]) -> List[int]:
    """校验规划页数与实物页码序列，返回实物页码列表。"""
    if not isinstance(page_count, int) or isinstance(page_count, bool):
        raise PrintAuditError("规划页数必须为整数")
    if page_count < 1:
        raise PrintAuditError(f"规划页数必须为正整数（实际 {page_count}）")
    if page_count > MAX_PAGES:
        raise PrintAuditError(
            f"规划页数（{page_count}）超过上限 {MAX_PAGES}，无法核对"
        )

    if not isinstance(scanned_pages, Sequence) or isinstance(scanned_pages, (str, bytes)):
        raise PrintAuditError("scanned_pages 必须是页码数组")
    if len(scanned_pages) > MAX_PAGES:
        raise PrintAuditError(
            f"实物页数（{len(scanned_pages)}）超过上限 {MAX_PAGES}，无法核对"
        )
    checked: List[int] = []
    for idx, page in enumerate(scanned_pages):
        if not isinstance(page, int) or isinstance(page, bool):
            raise PrintAuditError(
                f"第 {idx + 1} 个抄录页码必须为正整数（收到 {page!r}），"
                "不接受布尔值或小数"
            )
        if page < 1:
            raise PrintAuditError(
                f"第 {idx + 1} 个抄录页码必须为正整数（实际 {page}）"
            )
        checked.append(page)
    return checked


def audit_print_pages(
    page_count: int, scanned_pages: Sequence[int]
) -> AuditResult:
    """把按实物顺序抄录的页码序列核对到规划的 ``1..page_count``。

    :param page_count: 规划页数，正确序列即 ``[1, 2, ..., page_count]``，
        范围 1..{MAX_PAGES}
    :param scanned_pages: 按实物顺序抄录的页码，长度 0..{MAX_PAGES}，元素为
        正整数（允许重复，也允许出现超过规划页数的错页页码）
    :return: 最少操作数与确定性的修正步骤序列
    :raises PrintAuditError: 任一输入约束不满足
    """
    source = _validate(page_count, scanned_pages)
    m = len(source)
    n = page_count
    target = list(range(1, n + 1))

    # 后缀 DP：d[i][j] = 把 source[i:] 修正成 target[j:] 的最少操作数。
    d: List[List[int]] = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        d[i][n] = m - i  # 目标已耗尽：剩余实物全部移除
    for j in range(n + 1):
        d[m][j] = n - j  # 实物已耗尽：剩余目标全部补入
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            best = 1 + d[i + 1][j + 1]  # 替换
            remove_cost = 1 + d[i + 1][j]
            insert_cost = 1 + d[i][j + 1]
            if remove_cost < best:
                best = remove_cost
            if insert_cost < best:
                best = insert_cost
            if source[i] == target[j]:
                keep_cost = d[i + 1][j + 1]  # 保留：成本 0
                if keep_cost < best:
                    best = keep_cost
            d[i][j] = best

    # 前向贪心重构：每一步在能保持全局最优的候选动作中，按
    # 保留 → 替换 → 移除 → 补入 的顺序取第一个，序列因此确定。
    steps: List[AuditStep] = []
    i = j = 0
    while i < m or j < n:
        if (
            i < m
            and j < n
            and source[i] == target[j]
            and d[i + 1][j + 1] == d[i][j]
        ):
            steps.append(AuditStep(KEEP, i, source[i], target[j]))
            i += 1
            j += 1
        elif i < m and j < n and 1 + d[i + 1][j + 1] == d[i][j]:
            steps.append(AuditStep(REPLACE, i, source[i], target[j]))
            i += 1
            j += 1
        elif i < m and 1 + d[i + 1][j] == d[i][j]:
            steps.append(AuditStep(REMOVE, i, source[i], None))
            i += 1
        else:
            # 必为补入：j < n 且 1 + d[i][j+1] == d[i][j]。
            # position 取当前原输入下标 i：插在该实物页之前；i == m 即末尾。
            steps.append(AuditStep(INSERT, i, None, target[j]))
            j += 1

    operation_count = sum(1 for step in steps if step.action != KEEP)
    return AuditResult(
        page_count=n,
        scanned_pages=source,
        operation_count=operation_count,
        steps=steps,
    )
