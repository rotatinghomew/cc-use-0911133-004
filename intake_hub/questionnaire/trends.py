"""队列需求趋势与原始分下降分解。

边界：
- ``confirmed_demand`` 只在本模块用于**趋势校验**，绝不回流到量尺拟合或个人换算；
- as-of 可见性：仅当 ``confirmed_demand.known_at <= as_of`` 时该确认结果才算可见，
  否则计为 ``pending``，避免用未来信息描述当期；
- 拒绝授权记录保留在队列量（volume）计数中，但不进入任何均分统计；
- 原始下降分解把 long→short 的原始均分差拆成「形式效应（被移除题目）」与
  「锚点人群变化」两部分，使业务能区分问卷形式与真实人群变化。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping

from intake_hub.questionnaire.linking import (
    REASON_BRANCH_NOT_BRIDGED,
    REASON_COMMON_ITEM_INCOMPLETE,
    REASON_CONSENT_REFUSED,
    REASON_GROUP_NOT_BRIDGED,
    REASON_UNSUPPORTED_VERSION,
)
from intake_hub.questionnaire.scoring import (
    NOT_DISPLAYED,
    anchor_score,
    mean,
    raw_score,
)


def classify_record(record: Mapping[str, Any], artifact: Mapping[str, Any]) -> dict[str, Any]:
    """对单条记录产出原始分、可比较分或拒绝换算原因。

    输出严格区分三类信息：
    - ``raw``: 版本内原始分（仅已答题求和，附缺失清单，永不零填）；
    - ``comparable``: 可比较锚点分，或 ``None`` + 结构化原因；
    - 授权状态本身不被分数掩盖。
    """
    version = record["version"]
    common_items = list(artifact["common_items"])
    version_items = artifact["version_items"]
    raw = raw_score(record, version_items)

    result: dict[str, Any] = {
        "record_id": record["record_id"],
        "subject_id": record["subject_id"],
        "version": version,
        "group": record.get("group"),
        "branch": record.get("branch"),
        "collected_at": record["collected_at"],
        "analysis_consent": bool(record.get("analysis_consent")),
        "raw_score": raw["score"],
        "raw_n_answered": raw["n_answered"],
        "raw_n_items": raw["n_items"],
        "raw_complete": raw["complete"],
        "not_displayed": raw["not_displayed"],
        "displayed_unanswered": raw["displayed_unanswered"],
        "comparable_score": None,
        "comparable_scale": None,
        "conversion_status": "not_converted",
        "reasons": [],
    }

    if not record.get("analysis_consent"):
        # 拒绝分析授权：本分析路径不产出任何派生分数（原始分也置空），
        # 仅保留队列量统计所需的结构性元数据与拒绝原因。
        result.update(
            {
                "raw_score": None,
                "raw_n_answered": None,
                "raw_complete": None,
                "not_displayed": [],
                "displayed_unanswered": [],
            }
        )
        result["reasons"] = [REASON_CONSENT_REFUSED]
        return result

    if version not in artifact["anchor_scale"]["versions_using_scale"]:
        result["reasons"] = [REASON_UNSUPPORTED_VERSION]
        return result

    cell = _find_cell(artifact, record.get("group"), record.get("branch"))
    if cell is None or not cell["supported"]:
        if cell is None:
            result["reasons"] = [REASON_GROUP_NOT_BRIDGED]
        else:
            result["reasons"] = list(cell["blocking_reasons"])
        return result

    anchor = anchor_score(record, common_items)
    if not anchor["complete"]:
        if anchor["incomplete_reason"] == NOT_DISPLAYED:
            result["reasons"] = [REASON_BRANCH_NOT_BRIDGED]
        else:
            result["reasons"] = [REASON_COMMON_ITEM_INCOMPLETE]
        result["anchor_not_displayed"] = anchor["not_displayed"]
        result["anchor_displayed_unanswered"] = anchor["displayed_unanswered"]
        return result

    result["comparable_score"] = anchor["score"]
    result["comparable_scale"] = artifact["anchor_scale"]["name"]
    result["conversion_status"] = "converted"
    return result


def _find_cell(artifact: Mapping[str, Any], group: str | None, branch: str | None) -> dict[str, Any] | None:
    for cell in artifact["cells"]:
        if cell["group"] == group and cell["branch"] == branch:
            return cell
    return None


def confirmation_visible(record: Mapping[str, Any], as_of: str | None) -> str:
    """返回 as-of 口径下人工确认的可见状态：visible_true / visible_false / pending / absent。"""
    confirmation = record.get("confirmed_demand")
    if not confirmation:
        return "absent"
    if as_of is not None and confirmation.get("known_at", "") > as_of:
        return "pending"
    return "visible_true" if confirmation["value"] else "visible_false"


def weekly_trends(
    records: list[Mapping[str, Any]],
    scored: list[Mapping[str, Any]],
    as_of: str | None,
) -> list[dict[str, Any]]:
    """按收集周（ISO 周）汇总队列量、原始分、可比较分与 as-of 确认状态。

    队列量包含拒绝授权与未换算记录（业务不能因为不换算就漏掉这些人）；
    均分只统计授权且有相应分数的记录。
    """
    by_week: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    for record, scored_row in zip(records, scored):
        week = f"{record['collected_at'][:4]}-W{_iso_week(record['collected_at']):02d}"
        by_week[week].append((record, scored_row))

    rows: list[dict[str, Any]] = []
    for week in sorted(by_week):
        bucket = by_week[week]
        raw_values = [s["raw_score"] for r, s in bucket if r.get("analysis_consent")]
        anchor_values = [s["comparable_score"] for _, s in bucket if s["comparable_score"] is not None]
        confirmations = Counter(confirmation_visible(r, as_of) for r, _ in bucket)
        rows.append(
            {
                "week": week,
                "volume_total": len(bucket),
                "by_version": dict(Counter(r["version"] for r, _ in bucket)),
                "by_branch": dict(Counter(r["branch"] for r, _ in bucket)),
                "consent_refused": sum(1 for r, _ in bucket if not r.get("analysis_consent")),
                "converted_count": len(anchor_values),
                "raw_mean_consenting": round(mean(raw_values), 4) if raw_values else None,
                "anchor_mean_comparable": round(mean(anchor_values), 4) if anchor_values else None,
                "demand_confirmation_as_of": dict(confirmations),
            }
        )
    return rows


def _iso_week(date_text: str) -> int:
    import datetime

    return datetime.date.fromisoformat(date_text).isocalendar().week


def demand_series(
    records: list[Mapping[str, Any]], as_of: str | None
) -> dict[str, Any]:
    """as-of 口径的服务意愿确认序列（仅趋势校验用途）。"""
    statuses = Counter(confirmation_visible(r, as_of) for r in records)
    visible_n = statuses["visible_true"] + statuses["visible_false"]
    return {
        "as_of": as_of,
        "visible_confirmed_rate": round(statuses["visible_true"] / visible_n, 4) if visible_n else None,
        "visible_n": visible_n,
        "pending_n": statuses["pending"],
        "absent_n": statuses["absent"],
        "status_counts": dict(statuses),
        "note": "确认结果只用于趋势校验，未用于任何个人分数的拟合或校准",
    }


def reconcile_raw_decline(
    scored: list[Mapping[str, Any]], artifact: Mapping[str, Any]
) -> dict[str, Any]:
    """把 long→short 原始均分差分解为形式效应与锚点人群变化。

    形式效应取拟合工件中 supported 单元的被移除题目贡献；锚点变化在同一
    supported 单元内用可比较分计算。若资料不足以分解则显式标注，而不是强行给数。
    """
    long_raw = [s["raw_score"] for s in scored if s["version"] == "long" and s["analysis_consent"]]
    short_raw = [s["raw_score"] for s in scored if s["version"] == "short" and s["analysis_consent"]]
    overall = {
        "long_raw_mean": round(mean(long_raw), 4) if long_raw else None,
        "short_raw_mean": round(mean(short_raw), 4) if short_raw else None,
        "observed_raw_drop": round(mean(long_raw) - mean(short_raw), 4)
        if long_raw and short_raw
        else None,
    }

    supported = {(c["group"], c["branch"]) for c in artifact["cells"] if c["supported"]}
    cell_rows: list[dict[str, Any]] = []
    for group, branch in sorted(supported):
        long_cell = [
            s for s in scored
            if s["version"] == "long" and s["group"] == group and s["branch"] == branch
            and s["analysis_consent"]
        ]
        short_cell = [
            s for s in scored
            if s["version"] == "short" and s["group"] == group and s["branch"] == branch
            and s["analysis_consent"]
        ]
        if not long_cell or not short_cell:
            cell_rows.append(
                {"group": group, "branch": branch, "decomposable": False,
                 "reason": "one_version_missing_in_cell"}
            )
            continue
        form_key = f"{group}|{branch}"
        form_estimate = artifact["form_effect"].get(form_key, {}).get(
            "dropped_item_contribution", {}
        ).get("mean_difference")
        long_anchor = mean([s["comparable_score"] for s in long_cell if s["comparable_score"] is not None])
        short_anchor = mean([s["comparable_score"] for s in short_cell if s["comparable_score"] is not None])
        long_raw_mean = mean([s["raw_score"] for s in long_cell])
        short_raw_mean = mean([s["raw_score"] for s in short_cell])
        raw_drop = long_raw_mean - short_raw_mean
        anchor_change = (
            round(short_anchor - long_anchor, 4)
            if long_anchor is not None and short_anchor is not None
            else None
        )
        cell_rows.append(
            {
                "group": group,
                "branch": branch,
                "decomposable": True,
                "n_long": len(long_cell),
                "n_short": len(short_cell),
                "long_raw_mean": round(long_raw_mean, 4),
                "short_raw_mean": round(short_raw_mean, 4),
                "raw_drop": round(raw_drop, 4),
                "form_effect_removed_items": form_estimate,
                "anchor_population_change_short_minus_long": anchor_change,
                "residual_unexplained": round(
                    raw_drop - (form_estimate or 0) - (-(anchor_change) if anchor_change is not None else 0), 4
                ),
                "interpretation": (
                    "raw_drop ≈ 形式效应（移除题目贡献） - 锚点人群变化；"
                    "锚点变化接近 0 说明下降主要来自问卷形式而非人群需求变化"
                ),
            }
        )

    non_comparable: Counter[str] = Counter()
    for s in scored:
        if s["version"] == "short" and s["comparable_score"] is None:
            key = "+".join(s["reasons"]) if s["reasons"] else "unknown"
            non_comparable[key] += 1
    return {
        "overall": overall,
        "cell_decomposition": cell_rows,
        "short_records_without_comparable_score": dict(non_comparable),
        "conclusion": _conclusion(overall, cell_rows),
    }


def _conclusion(overall: Mapping[str, Any], cell_rows: list[Mapping[str, Any]]) -> str:
    decomposable = [c for c in cell_rows if c.get("decomposable")]
    if not decomposable:
        return "资料不支持分解：保留 long/short 两个量尺，不对下降来源下结论"
    anchor_changes = [c["anchor_population_change_short_minus_long"] for c in decomposable
                      if c["anchor_population_change_short_minus_long"] is not None]
    form_effects = [c["form_effect_removed_items"] for c in decomposable
                    if c["form_effect_removed_items"] is not None]
    if not anchor_changes or not form_effects:
        return "部分单元信息不足，已在单元行内标注；整体结论需谨慎"
    avg_anchor = sum(anchor_changes) / len(anchor_changes)
    avg_form = sum(form_effects) / len(form_effects)
    if abs(avg_anchor) < 0.5 and avg_form >= 0.5:
        return (
            f"可比较锚点分两版基本持平（平均变化 {avg_anchor:.2f}），原始分下降主要由问卷形式变化"
            f"（被移除题目，形式效应约 {avg_form:.2f} 分/人）造成，而非人群服务需求下降"
        )
    return "锚点分与形式效应均有变化，原始下降同时包含形式与人群成分，见单元分解"
