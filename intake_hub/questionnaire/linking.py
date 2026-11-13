"""桥接诊断、共同题漂移检验与量尺衔接拟合。

设计原则（对应业务约束）：
1. 只用同时填写两版、且**两侧都授予分析授权**的桥接样本；拒绝授权的人不进入拟合，
   也不会用其 ``confirmed_demand`` 反推任何系数。
2. 桥接是非随机招募（``recruitment`` 全部为 service-desk），因此衔接只在有桥接覆盖、
   通过漂移检验的（人群 group × 分支 branch）单元内成立，不向未覆盖群体外推。
3. 共同题若在某人群中改版后含义漂移（配对差异显著），该人群保留两个独立量尺，
   不产出换算。
4. 衔接采用**共同题锚点恒等映射**（anchor = 共同题已答分之和，两版同一定义），
   桥接样本用于检验恒等假设是否成立，而不是在 33 人小样本上回归出全人群换算表。
5. ``confirmed_demand`` 完全不参与本模块的任何计算（它属于后续趋势校验，且受
   ``fit_known_through`` 的 as-of 可见性约束）。
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from typing import Any, Mapping

from intake_hub.questionnaire.scoring import anchor_score, mean

# 拒绝换算 / 不支持的原因码（apply 与文档共用同一套词汇）
REASON_CONSENT_REFUSED = "analysis_consent_refused"
REASON_UNSUPPORTED_VERSION = "unsupported_version"
REASON_GROUP_NOT_BRIDGED = "group_not_bridged"
REASON_BRANCH_NOT_BRIDGED = "bridge_branch_missing"
REASON_COMMON_ITEM_DRIFT = "common_item_drift"
REASON_COMMON_ITEM_INCOMPLETE = "common_item_incomplete"

# 漂移检验闸门：配对不一致比例或配对均差超过任一阈值即判漂移
DISAGREEMENT_RATE_GATE = 0.25
MEAN_DIFF_GATE = 0.5
# 单元支持所需的最小可用（共同题 complete-case）桥接对数
MIN_BRIDGES_PER_CELL = 5
BOOTSTRAP_REPLICATES = 2000

ARTIFACT_KIND = "intake_hub.questionnaire.scale_mapping"
ARTIFACT_SCHEMA_VERSION = 1


def _stable_hash(payload: Any) -> str:
    body = _canonical_json(payload)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def usable_bridges(data: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """筛出两侧均授权、subject 一致的桥接对，并记录被排除的原因。"""
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for bridge in data.get("bridges", []):
        bid = bridge["bridge_id"]
        long_side, short_side = bridge["long"], bridge["short"]
        if long_side.get("subject_id") != short_side.get("subject_id"):
            excluded.append({"bridge_id": bid, "reason": "subject_mismatch"})
            continue
        if not long_side.get("analysis_consent") or not short_side.get("analysis_consent"):
            excluded.append({"bridge_id": bid, "reason": REASON_CONSENT_REFUSED})
            continue
        kept.append(bridge)
    return kept, excluded


def _paired_diff_stats(diffs: list[int], seed_key: str) -> dict[str, Any]:
    """配对差值（short - long）的描述统计与确定性 bootstrap 95% 区间。"""
    n = len(diffs)
    if n == 0:
        return {"n": 0}
    avg = sum(diffs) / n
    if n > 1:
        variance = sum((x - avg) ** 2 for x in diffs) / (n - 1)
        sd = variance ** 0.5
    else:
        sd = 0.0
    rng = random.Random(seed_key)
    if n >= 2:
        reps = []
        for _ in range(BOOTSTRAP_REPLICATES):
            sample = [diffs[rng.randrange(n)] for _ in range(n)]
            reps.append(sum(sample) / n)
        reps.sort()
        lo = reps[int(0.025 * BOOTSTRAP_REPLICATES)]
        hi = reps[int(0.975 * BOOTSTRAP_REPLICATES) - 1]
    else:
        lo = hi = avg
    return {
        "n": n,
        "mean_difference": round(avg, 6),
        "sd_difference": round(sd, 6),
        "ci95": [round(lo, 6), round(hi, 6)],
    }


def item_drift_by_group(
    usable: list[dict[str, Any]], common_items: list[str], seed: int
) -> dict[str, dict[str, dict[str, Any]]]:
    """按人群 × 共同题做配对漂移检验（仅纳入该题两侧均已答的对子）。"""
    result: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bridge in usable:
        grouped[bridge["long"]["group"]].append(bridge)
    for group, bridges in grouped.items():
        for item in common_items:
            diffs: list[int] = []
            excluded_null = 0
            for bridge in bridges:
                long_value = bridge["long"]["answers"].get(item)
                short_value = bridge["short"]["answers"].get(item)
                if long_value is None or short_value is None:
                    excluded_null += 1
                    continue
                diffs.append(int(short_value) - int(long_value))
            stats = _paired_diff_stats(diffs, f"{seed}:{group}:{item}")
            stats["pairs_excluded_unanswered"] = excluded_null
            if stats.get("n"):
                disagreement_rate = sum(1 for x in diffs if x != 0) / len(diffs)
                stats["disagreement_rate"] = round(disagreement_rate, 6)
                stats["drifted"] = (
                    disagreement_rate >= DISAGREEMENT_RATE_GATE
                    or abs(stats["mean_difference"]) >= MEAN_DIFF_GATE
                )
                stats["gate"] = {
                    "disagreement_rate_gate": DISAGREEMENT_RATE_GATE,
                    "mean_difference_gate": MEAN_DIFF_GATE,
                }
            else:
                stats["disagreement_rate"] = None
                stats["drifted"] = None
            result[group][item] = stats
    return {group: dict(items) for group, items in result.items()}


def _anchor_pair_complete(bridge: Mapping[str, Any], common_items: list[str]) -> bool:
    return (
        anchor_score(bridge["long"], common_items)["complete"]
        and anchor_score(bridge["short"], common_items)["complete"]
    )


def evaluate_cells(
    data: Mapping[str, Any],
    usable: list[dict[str, Any]],
    drift: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """对每个 (group, branch) 单元判定是否支持锚点恒等衔接。"""
    common_items = list(data["common_items"])
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for bridge in usable:
        key = (bridge["long"]["group"], bridge["short"]["branch"])
        grouped[key].append(bridge)

    # 响应数据里出现、但桥接完全没有覆盖的 (group, branch)
    observed_cells = {(r["group"], r["branch"]) for r in data.get("responses", [])}
    cells: list[dict[str, Any]] = []
    for group, branch in sorted(observed_cells | set(grouped.keys())):
        bridges = grouped.get((group, branch), [])
        complete_pairs = [b for b in bridges if _anchor_pair_complete(b, common_items)]
        drifted_items = [
            item
            for item, stats in drift.get(group, {}).items()
            if stats.get("drifted") is True
        ]
        problems: list[str] = []
        if not bridges:
            problems.append(REASON_GROUP_NOT_BRIDGED if group not in drift else REASON_BRANCH_NOT_BRIDGED)
        if len(complete_pairs) < MIN_BRIDGES_PER_CELL:
            problems.append("insufficient_bridge_sample")
        if drifted_items:
            problems.append(REASON_COMMON_ITEM_DRIFT)

        # 恒等假设检验：complete-case 对子的共同题轮廓是否逐题相等
        exact_matches = 0
        profile_diffs: list[int] = []
        for bridge in complete_pairs:
            long_anchor = anchor_score(bridge["long"], common_items)["score"]
            short_anchor = anchor_score(bridge["short"], common_items)["score"]
            profile_diffs.append(int(short_anchor) - int(long_anchor))
            if all(
                bridge["long"]["answers"].get(item) == bridge["short"]["answers"].get(item)
                for item in common_items
            ):
                exact_matches += 1
        n_complete = len(complete_pairs)
        identity = {
            "n_complete_pairs": n_complete,
            "exact_profile_matches": exact_matches,
            "exact_match_rate": round(exact_matches / n_complete, 6) if n_complete else None,
            "anchor_diff": _paired_diff_stats(profile_diffs, f"cell:{group}:{branch}")
            if profile_diffs
            else {"n": 0},
        }
        supported = not problems
        cells.append(
            {
                "group": group,
                "branch": branch,
                "supported": supported,
                "blocking_reasons": sorted(set(problems)),
                "drifted_items": drifted_items,
                "n_usable_bridges": len(bridges),
                "n_complete_anchor_pairs": n_complete,
                "identity_check": identity,
                "scale_policy": "single_anchor_scale"
                if supported
                else "retain_two_separate_scales",
            }
        )
    return cells


def form_effect(
    usable: list[dict[str, Any]], common_items: list[str], seed: int
) -> dict[str, Any]:
    """估计改版的**形式效应**：被移除题目（long 有、short 无）贡献的分数。

    仅用于人群层面的原始下降分解，不回填到任何个人记录。按支持单元分组报告，
    并给出确定性 bootstrap 95% 区间与配对总分差作为交叉验证。
    """
    dropped_by_cell: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(
        lambda: {"dropped": [], "paired_total_diff": []}
    )
    short_items = set(common_items)
    for bridge in usable:
        long_record, short_record = bridge["long"], bridge["short"]
        cell = (long_record["group"], short_record["branch"])
        for item, value in long_record["answers"].items():
            if item not in short_items and value is not None:
                dropped_by_cell[cell]["dropped"].append(int(value))
        long_total = sum(v for v in long_record["answers"].values() if v is not None)
        short_total = sum(v for v in short_record["answers"].values() if v is not None)
        dropped_by_cell[cell]["paired_total_diff"].append(long_total - short_total)

    report: dict[str, Any] = {}
    for (group, branch), buckets in sorted(dropped_by_cell.items()):
        key = f"{group}|{branch}"
        dropped = buckets["dropped"]
        paired = buckets["paired_total_diff"]
        report[key] = {
            "dropped_item_contribution": _paired_diff_stats(dropped, f"{seed}:form:{key}"),
            "paired_long_minus_short_total": _paired_diff_stats(paired, f"{seed}:pair:{key}"),
            "interpretation": "被移除题目的人均贡献，即原始分中纯由问卷形式造成的部分",
        }
    return report


def fit_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """拟合并返回带版本与适用范围的量尺衔接映射工件（不落盘）。"""
    seed = int(data.get("seed", 0))
    common_items = list(data["common_items"])
    versions = data["versions"]
    version_items = {name: list(spec["items"]) for name, spec in versions.items()}

    usable, excluded_bridges = usable_bridges(data)
    drift = item_drift_by_group(usable, common_items, seed)
    cells = evaluate_cells(data, usable, drift)
    form = form_effect(usable, common_items, seed)

    recruitment = sorted({b.get("recruitment", "unknown") for b in usable})
    anchor_max = 3 * len(common_items)

    # 适用范围：仅把 supported 单元展开成可机读的 applicability 清单
    applicability = [
        {
            "group": cell["group"],
            "branch": cell["branch"],
            "versions": ["long", "short"],
            "comparable_score": "common_anchor_sum",
            "anchor_range": [0, anchor_max],
        }
        for cell in cells
        if cell["supported"]
    ]

    checks = {
        "bridge_disconnect": {
            "total_bridges": len(data.get("bridges", [])),
            "usable_bridges": len(usable),
            "excluded": excluded_bridges,
            "recruitment_channels": recruitment,
            "note": "桥接为非随机招募，结论仅在覆盖单元内成立，禁止外推到未覆盖人群",
        },
        "branch_coverage": {
            "bridged_branches": sorted(
                {f"{b['long']['branch']}->{b['short']['branch']}" for b in usable}
            ),
            "observed_response_branches": sorted(
                {(r["branch"], r["version"]) for r in data.get("responses", [])}
            ),
            "unsupported_cells": [
                {"group": c["group"], "branch": c["branch"], "reasons": c["blocking_reasons"]}
                for c in cells
                if not c["supported"]
            ],
        },
        "common_item_drift": drift,
        "consent": {
            "refused_records": sorted(
                r["record_id"]
                for r in data.get("responses", [])
                if not r.get("analysis_consent")
            ),
            "policy": "拒绝分析授权的记录不参与拟合、不产出任何分析分，仅保留在队列量趋势的计数中",
        },
    }

    assumptions = [
        "锚点恒等假设：在通过检验的单元内，共同题在 long/short 两版测量同一构念且取值含义不变",
        "局部可推广假设：service-desk 招募的桥接对子可代表同 group × branch 单元内的常规应答者",
        "complete-case 假设：共同题未展示或未答的记录不插补、不按零分处理，直接退出锚点换算",
        "稳定性假设：拟合时点之后题目措辞与分支逻辑保持不变；任何题目改版都需要重新拟合新版本",
    ]
    uncertainties = [
        "桥接样本量小且非随机，单元外（未覆盖 group/branch/version）无等值证据",
        "bootstrap 区间仅反映桥接样本内变异，不覆盖招募选择偏差",
        "存在共同题漂移的单元保留双量尺，跨版本差异无法区分为真实变化或测量漂移",
    ]

    payload = {
        "kind": ARTIFACT_KIND,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "mapping_version": 1,
        "fit_known_through": data.get("fit_known_through"),
        "source_seed": seed,
        "common_items": common_items,
        "anchor_scale": {
            "name": "common_anchor_sum",
            "items": common_items,
            "range": [0, anchor_max],
            "rule": "sum of answered common items; complete-case required",
            "versions_using_scale": ["long", "short"],
        },
        "version_items": version_items,
        "version_ranges": {name: list(spec["range"]) for name, spec in versions.items()},
        "applicability": applicability,
        "cells": cells,
        "form_effect": form,
        "checks": checks,
        "assumptions": assumptions,
        "uncertainties": uncertainties,
        "calibration_inputs_excluded": ["confirmed_demand"],
        "scale_policy": (
            "supported cells use one common anchor scale; every unsupported cell retains two "
            "separate raw scales and no whole-population conversion table is emitted"
        ),
    }
    payload["content_fingerprint"] = _stable_hash({k: v for k, v in payload.items()})
    payload["id"] = f"scale-mapping-v1-{payload['content_fingerprint'][:12]}"
    return payload


def fingerprint_matches(artifact: Mapping[str, Any]) -> bool:
    """校验保存的映射工件内容指纹，防止历史映射被悄悄改写。"""
    stored = artifact.get("content_fingerprint")
    if not stored:
        return False
    body = {k: v for k, v in artifact.items() if k not in ("content_fingerprint", "id")}
    return _stable_hash(body) == stored
