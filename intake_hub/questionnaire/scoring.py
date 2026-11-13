"""原始分与共同题锚点分计算。

三种缺失状态必须区别对待，绝不用零分填满：
- ``not_displayed``: 题目未在该记录的 ``displayed`` 中（分支跳过）；
- ``displayed_unanswered``: 展示了但 ``answers`` 中为 ``null``；
- 已答: ``answers`` 中为取值范围内的整数。

原始分（``raw_score``）只对已答题求和，同时返回答题数与缺失清单，
因此缺失不会被悄悄当作 0 分。共同题锚点分（``anchor_score``）采用
complete-case 规则：任一共同题未展示或未答，则该记录不产出锚点分，
并给出结构化原因，而不是插补。
"""

from __future__ import annotations

from typing import Any, Mapping

NOT_DISPLAYED = "not_displayed"
DISPLAYED_UNANSWERED = "displayed_unanswered"
ANSWERED = "answered"


def item_status(record: Mapping[str, Any], item: str) -> str:
    """返回单题在单条记录中的三态编码。"""
    displayed = set(record.get("displayed", ()))
    answers = record.get("answers", {})
    if item not in displayed:
        return NOT_DISPLAYED
    if item not in answers or answers[item] is None:
        return DISPLAYED_UNANSWERED
    return ANSWERED


def score_items(record: Mapping[str, Any], items: list[str]) -> dict[str, Any]:
    """对给定题集合计算可审计的分项状态。

    返回字段：
    - ``sum_answered``: 仅已答题之和（未展示/未答不计入，更不会按 0 处理）；
    - ``answered``/``not_displayed``/``displayed_unanswered``: 三类题目清单；
    - ``complete``: 题集合内是否全部已答（complete-case 判定依据）；
    - ``incomplete_reason``: 不完整时的首要原因，完整时为 ``None``。
    """
    answers = record.get("answers", {})
    answered: list[str] = []
    not_displayed: list[str] = []
    unanswered: list[str] = []
    total = 0
    for item in items:
        status = item_status(record, item)
        if status == NOT_DISPLAYED:
            not_displayed.append(item)
        elif status == DISPLAYED_UNANSWERED:
            unanswered.append(item)
        else:
            answered.append(item)
            total += int(answers[item])
    reason = None
    if not_displayed:
        reason = NOT_DISPLAYED
    elif unanswered:
        reason = DISPLAYED_UNANSWERED
    return {
        "items": list(items),
        "sum_answered": total,
        "answered": answered,
        "not_displayed": not_displayed,
        "displayed_unanswered": unanswered,
        "complete": not not_displayed and not unanswered,
        "incomplete_reason": reason,
    }


def raw_score(record: Mapping[str, Any], version_items: Mapping[str, list[str]]) -> dict[str, Any]:
    """计算版本内原始分：仅对该版本定义且实际已答的题目求和。"""
    version = record["version"]
    items = list(version_items[version])
    detail = score_items(record, items)
    return {
        "score": detail["sum_answered"],
        "n_answered": len(detail["answered"]),
        "n_items": len(items),
        "not_displayed": detail["not_displayed"],
        "displayed_unanswered": detail["displayed_unanswered"],
        "complete": detail["complete"],
    }


def anchor_score(record: Mapping[str, Any], common_items: list[str]) -> dict[str, Any]:
    """共同题锚点分（complete-case）。

    任一共同题未展示（如 skip-travel 分支缺 travel）或展示未答（null），
    都不产出锚点分，返回 ``score=None`` 与具体原因及涉及题目。
    """
    detail = score_items(record, common_items)
    return {
        "score": detail["sum_answered"] if detail["complete"] else None,
        "items": list(common_items),
        "complete": detail["complete"],
        "incomplete_reason": detail["incomplete_reason"],
        "not_displayed": detail["not_displayed"],
        "displayed_unanswered": detail["displayed_unanswered"],
        "answered": detail["answered"],
    }


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
