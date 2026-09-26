"""巡演曲目单的谱架换册核心。

背景
====
巡演按曲目单逐场演出：每场需要某一册**已保存方案**对应的乐谱。谱架有
``slot_count`` 个槽位（编号 1..slot_count），**开场时全部为空**；每册乐谱
占一个槽位，每场开演时该场所需的册必须已经在架上。两种换册动作：

- **上架**：把一册放上某个槽位，每上架一次计入总上架次数；
- **撤下**：把某槽位上的册取下（不计入上架次数）。

换册规则（确定性）
==================
按演出顺序逐场处理，设本场需要册 p：

1. p 已在架上：本场无需动作；
2. p 不在架上且有空槽：把 p 放上**编号最小的空槽**；
3. p 不在架上且架满：撤下**未来最晚再次使用**的册（**永不再用视为最晚**；
   并列时取**方案 ID 字典序最小**者），把 p 放上腾出的槽位。

最优性
======
该规则即 Belady 最优置换算法在谱架场景的形式，可证**总上架次数最少**：

- 提前上架一册不会减少它所需的上架次数（仍计一次），反而占槽可能迫使
  更早撤下；把任何提前上架推迟到首次需要时再做不增加上架次数，故存在
  只在缺册时上架的最优方案；
- 架满缺册时，撤"最晚再次使用"的册不劣于撤任何其他册（交换论证：被规则
  保留的册在被撤者下次需要之前必先被需要，或同样永不再用，交换两者去向
  不增加上架次数）；
- 空槽选哪个、架满并列时撤哪册都不影响上架次数，固定"编号最小空槽"与
  "ID 字典序最小"使结果确定、可复现。

本模块仅依赖标准库，便于脱离 Web 层直接单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

#: 一次巡演曲目单的场次数范围
MIN_SHOWS = 1
MAX_SHOWS = 500

#: 谱架槽位数范围
MIN_SLOTS = 1
MAX_SLOTS = 20


class StandChangeError(ValueError):
    """谱架换册输入不合法：场次数不在 1~500、槽位数不在 1~20、
    方案 ID 非字符串或为空等。"""


@dataclass(frozen=True)
class StandAction:
    """一次上架/撤下动作：方案 ID 与槽位编号（1 基）。"""

    plan_id: str  # 动作涉及的方案 ID
    slot: int  # 动作发生的槽位编号（1 基）


@dataclass(frozen=True)
class ShowChange:
    """一场演出的换册明细。"""

    show_index: int  # 场次序号（1 基，按演出顺序）
    plan_id: str  # 本场所需方案 ID
    load: Optional[StandAction]  # 上架动作；本场所需册已在架上为 None
    unload: Optional[StandAction]  # 撤下动作；未发生撤下为 None
    slots_after: List[Optional[str]]  # 场后各槽位（按槽位编号 1..k 顺序），空槽 None


@dataclass(frozen=True)
class StandChanges:
    """谱架换册结果。"""

    plan_ids: List[str]  # 按演出顺序的方案 ID
    slot_count: int  # 谱架槽位数
    total_loads: int  # 总上架次数（最少）
    shows: List[ShowChange]  # 每场换册明细（按演出顺序）


def _validate(
    plan_ids: Sequence[str], slot_count: int
) -> Tuple[List[str], int]:
    """校验全部输入，返回 (方案 ID 列表, 槽位数)。

    :raises StandChangeError: 任一约束不满足
    """
    if not isinstance(slot_count, int) or isinstance(slot_count, bool):
        raise StandChangeError(f"谱架槽位数必须为整数（收到 {slot_count!r}）")
    if not MIN_SLOTS <= slot_count <= MAX_SLOTS:
        raise StandChangeError(
            f"谱架槽位数（{slot_count}）必须在 {MIN_SLOTS}~{MAX_SLOTS} 之间"
        )

    if not isinstance(plan_ids, Sequence) or isinstance(plan_ids, (str, bytes)):
        raise StandChangeError("plan_ids 必须为方案 ID 序列")
    if len(plan_ids) < MIN_SHOWS:
        raise StandChangeError(
            f"演出场数（{len(plan_ids)}）少于下限 {MIN_SHOWS}："
            f"一次巡演必须提交 {MIN_SHOWS}~{MAX_SHOWS} 场"
        )
    if len(plan_ids) > MAX_SHOWS:
        raise StandChangeError(
            f"演出场数（{len(plan_ids)}）超过上限 {MAX_SHOWS}："
            f"一次巡演只能提交 {MIN_SHOWS}~{MAX_SHOWS} 场"
        )

    ids: List[str] = []
    for idx, plan_id in enumerate(plan_ids):
        if not isinstance(plan_id, str) or not plan_id:
            raise StandChangeError(
                f"第 {idx + 1} 场的方案 ID 必须为非空字符串（收到 {plan_id!r}）"
            )
        ids.append(plan_id)
    return ids, slot_count


def plan_stand_changes(
    plan_ids: Sequence[str], slot_count: int
) -> StandChanges:
    """按演出顺序模拟谱架换册，求总上架次数最少的确定方案。

    :param plan_ids: 按演出顺序排列的已保存方案 ID（允许重复，1~500 项）；
        每项即该场所需的一册乐谱
    :param slot_count: 谱架槽位数（1~20），开场全部槽位为空
    :return: 每场的上架/撤下动作、场后各槽位方案 ID 与总上架次数
    :raises StandChangeError: 场次数不在 1~500、槽位数不在 1~20、
        方案 ID 非字符串或为空
    """
    ids, slot_count = _validate(plan_ids, slot_count)
    n = len(ids)

    # next_after[i]：下标 i 之后下一场需要同一册的场次下标；不再使用记为 n
    # （"永不再用视为最晚"，n 大于任何真实场次下标）
    next_after = [n] * n
    last_seen: dict[str, int] = {}
    for i in range(n - 1, -1, -1):
        if ids[i] in last_seen:
            next_after[i] = last_seen[ids[i]]
        last_seen[ids[i]] = i

    slots: List[Optional[str]] = [None] * slot_count  # 槽位现状（0 基下标 ↔ 1 基编号）
    # 架上各册下一次被需要的场次下标（n 表示永不再用）；仅对架上册查询。
    # 不变量：处理完第 i 场后，架上册 b 的 next_use[b] 是 > i 的首个需要 b 的
    # 场次（或 n）——每次 b 被需要时都会用 next_after 刷新，故归纳成立。
    next_use: dict[str, int] = {}
    shows: List[ShowChange] = []
    total_loads = 0

    for i, plan_id in enumerate(ids):
        next_use[plan_id] = next_after[i]
        load: Optional[StandAction] = None
        unload: Optional[StandAction] = None
        if plan_id in slots:
            pass  # 已在架上：本场无需动作
        else:
            total_loads += 1
            if None in slots:
                slot_idx = slots.index(None)  # 编号最小的空槽
            else:

                def _evict_key(s: int) -> Tuple[int, str]:
                    book = slots[s]
                    assert book is not None  # 架满分支：各槽均有册
                    # 下次使用最晚（永不再用为 n，最晚）者优先撤；
                    # 并列取方案 ID 字典序最小者
                    return (-next_use[book], book)

                slot_idx = min(range(slot_count), key=_evict_key)
                victim = slots[slot_idx]
                assert victim is not None
                unload = StandAction(plan_id=victim, slot=slot_idx + 1)
                del next_use[victim]
            slots[slot_idx] = plan_id
            load = StandAction(plan_id=plan_id, slot=slot_idx + 1)
        shows.append(
            ShowChange(
                show_index=i + 1,
                plan_id=plan_id,
                load=load,
                unload=unload,
                slots_after=list(slots),
            )
        )

    return StandChanges(
        plan_ids=ids,
        slot_count=slot_count,
        total_loads=total_loads,
        shows=shows,
    )
