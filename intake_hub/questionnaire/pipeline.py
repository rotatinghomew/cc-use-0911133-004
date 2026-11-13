"""fit/apply 编排与落盘工件管理。

不可变性保证：
- ``fit`` 产出的映射工件带 ``content_fingerprint`` 与显式 ``mapping_version``；
- ``apply`` 只读取已保存工件，**绝不**用新批次数据重新拟合或修改任何系数；
- 加载时校验指纹，工件被改动则直接报错，防止历史映射被悄悄改写；
- 新批次中出现工件适用范围外的版本/人群/分支时，记录到 ``out_of_scope`` 并逐条
  给出拒绝原因，而不是自动扩展映射。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from intake_hub.questionnaire.linking import (
    REASON_GROUP_NOT_BRIDGED,
    REASON_UNSUPPORTED_VERSION,
    fingerprint_matches,
    fit_mapping,
)
from intake_hub.questionnaire.trends import (
    classify_record,
    demand_series,
    reconcile_raw_decline,
    weekly_trends,
)


class ArtifactError(ValueError):
    """映射工件缺失、版本不符或指纹校验失败。"""


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_artifact(path: str | Path, payload: Mapping[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return output


def load_artifact(path: str | Path) -> dict[str, Any]:
    artifact = load_json(path)
    if artifact.get("kind") != "intake_hub.questionnaire.scale_mapping":
        raise ArtifactError("文件不是 questionnaire 量尺映射工件")
    if artifact.get("schema_version") != 1:
        raise ArtifactError(f"不支持的映射 schema_version: {artifact.get('schema_version')}")
    if not fingerprint_matches(artifact):
        raise ArtifactError("映射工件内容指纹不匹配：保存的映射可能已被改动，拒绝应用")
    return artifact


def fit(data: Mapping[str, Any], output_path: str | Path | None = None) -> dict[str, Any]:
    artifact = fit_mapping(data)
    if output_path is not None:
        write_artifact(output_path, artifact)
    return artifact


def _out_of_scope(record: Mapping[str, Any], artifact: Mapping[str, Any]) -> str | None:
    # 版本不在锚点量尺覆盖范围内（如 third）：映射对其无任何换算依据
    if record["version"] not in artifact["anchor_scale"]["versions_using_scale"]:
        return REASON_UNSUPPORTED_VERSION
    known = {(c["group"], c["branch"]) for c in artifact["cells"]}
    if (record.get("group"), record.get("branch")) not in known:
        return REASON_GROUP_NOT_BRIDGED
    return None


def apply(
    data: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    as_of: str | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """把已保存映射应用到一批（可能是新收集的）问卷记录。

    ``as_of`` 缺省取工件的 ``fit_known_through``，保证趋势口径与拟合时点一致；
    调用方可显式传入更晚日期，但确认结果始终只影响趋势区，不影响个人分数。
    """
    if not fingerprint_matches(artifact):
        raise ArtifactError("映射工件指纹不匹配，拒绝应用")
    records = list(data.get("responses", []))
    if as_of is None:
        as_of = artifact.get("fit_known_through")

    scored: list[dict[str, Any]] = []
    out_of_scope: list[dict[str, str]] = []
    for record in records:
        reason = _out_of_scope(record, artifact)
        if reason is not None:
            out_of_scope.append({"record_id": record["record_id"], "reason": reason})
        scored.append(classify_record(record, artifact))

    converted = sum(1 for s in scored if s["conversion_status"] == "converted")
    result = {
        "kind": "intake_hub.questionnaire.apply_report",
        "schema_version": 1,
        "mapping_id": artifact["id"],
        "mapping_version": artifact["mapping_version"],
        "mapping_fingerprint": artifact["content_fingerprint"],
        "mapping_fit_known_through": artifact["fit_known_through"],
        "as_of": as_of,
        "applicability": artifact["applicability"],
        "scale_policy": artifact["scale_policy"],
        "record_count": len(records),
        "converted_count": converted,
        "not_converted_count": len(records) - converted,
        "out_of_scope": out_of_scope,
        "records": scored,
        "weekly_trends": weekly_trends(records, scored, as_of),
        "demand_trend": demand_series(records, as_of),
        "raw_decline_reconciliation": reconcile_raw_decline(scored, artifact),
    }
    if output_path is not None:
        write_artifact(output_path, result)
    return result
