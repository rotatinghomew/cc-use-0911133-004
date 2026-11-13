"""questionnaire 量尺衔接 fit/apply 的单元与端到端测试。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from intake_hub.questionnaire.cli import main as cli_main
from intake_hub.questionnaire.linking import (
    REASON_COMMON_ITEM_DRIFT,
    REASON_CONSENT_REFUSED,
    fingerprint_matches,
    fit_mapping,
    usable_bridges,
)
from intake_hub.questionnaire.pipeline import (
    ArtifactError,
    apply,
    fit,
    load_artifact,
)
from intake_hub.questionnaire.scoring import (
    DISPLAYED_UNANSWERED,
    NOT_DISPLAYED,
    anchor_score,
    raw_score,
)
from intake_hub.questionnaire.trends import (
    classify_record,
    confirmation_visible,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "questionnaire_link.json"


class LinkingTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.artifact = fit_mapping(self.data)

    def test_three_way_missingness_is_never_zero_filled(self):
        # 展示但未答（null）：不计分但标记为 displayed_unanswered，而不是 0 分
        unanswered = {
            "version": "long",
            "displayed": ["contact", "travel", "digital", "availability"],
            "answers": {"contact": 1, "travel": 2, "digital": None, "availability": 3},
        }
        detail = raw_score(unanswered, self.artifact["version_items"])
        self.assertEqual(detail["score"], 6)  # 1+2+3，null 未按 0 填
        self.assertEqual(detail["displayed_unanswered"], ["digital"])
        self.assertFalse(detail["complete"])

        # 未展示（分支跳过）：标记为 not_displayed，与未答区分
        skipped = {
            "version": "short",
            "displayed": ["contact", "digital"],
            "answers": {"contact": 3, "digital": 2},
        }
        anchor = anchor_score(skipped, self.artifact["common_items"])
        self.assertIsNone(anchor["score"])
        self.assertEqual(anchor["not_displayed"], ["travel"])
        self.assertEqual(anchor["incomplete_reason"], NOT_DISPLAYED)

        null_anchor = anchor_score(unanswered, self.artifact["common_items"])
        self.assertIsNone(null_anchor["score"])
        self.assertEqual(null_anchor["displayed_unanswered"], ["digital"])
        self.assertEqual(null_anchor["incomplete_reason"], DISPLAYED_UNANSWERED)

    def test_consent_refused_bridges_excluded_from_fit(self):
        usable, excluded = usable_bridges(self.data)
        excluded_ids = {row["bridge_id"] for row in excluded}
        self.assertEqual(len(usable), 33)
        self.assertEqual(len(excluded), 3)
        self.assertEqual(
            {row["reason"] for row in excluded}, {REASON_CONSENT_REFUSED}
        )
        self.assertIn("bridge-0", excluded_ids)
        self.assertIn("bridge-17", excluded_ids)
        self.assertIn("bridge-34", excluded_ids)
        # confirmed_demand 的取值绝不进入拟合工件（字段名只出现在排除声明里）
        blob = json.dumps(self.artifact, ensure_ascii=False)
        self.assertNotIn("known_at", blob)
        self.assertNotIn("visible_true", blob)
        self.assertEqual(self.artifact["calibration_inputs_excluded"], ["confirmed_demand"])

    def test_remote_contact_drift_detected_urban_clean(self):
        drift = self.artifact["checks"]["common_item_drift"]
        self.assertTrue(drift["remote"]["contact"]["drifted"])
        self.assertGreaterEqual(drift["remote"]["contact"]["disagreement_rate"], 0.25)
        self.assertTrue(drift["remote"]["contact"]["ci95"][0] > 0)
        for item in self.artifact["common_items"]:
            self.assertFalse(drift["urban"][item]["drifted"])
            self.assertEqual(drift["urban"][item]["disagreement_rate"], 0.0)

    def test_only_urban_full_cell_is_supported(self):
        supported = {(c["group"], c["branch"]) for c in self.artifact["cells"] if c["supported"]}
        self.assertEqual(supported, {("urban", "full")})
        policy = {
            (c["group"], c["branch"]): c["scale_policy"]
            for c in self.artifact["cells"]
        }
        self.assertEqual(policy[("urban", "full")], "single_anchor_scale")
        self.assertEqual(policy[("remote", "full")], "retain_two_separate_scales")
        # 漂移单元必须带原因码
        remote_full = next(c for c in self.artifact["cells"]
                           if (c["group"], c["branch"]) == ("remote", "full"))
        self.assertIn(REASON_COMMON_ITEM_DRIFT, remote_full["blocking_reasons"])
        # skip-travel 没有任何桥接覆盖
        skip_cells = [c for c in self.artifact["cells"] if c["branch"] == "skip-travel"]
        self.assertTrue(skip_cells)
        for cell in skip_cells:
            self.assertFalse(cell["supported"])

    def test_identity_equating_holds_in_supported_cell(self):
        urban_full = next(c for c in self.artifact["cells"]
                          if (c["group"], c["branch"]) == ("urban", "full"))
        self.assertEqual(urban_full["identity_check"]["exact_match_rate"], 1.0)
        self.assertEqual(urban_full["identity_check"]["anchor_diff"]["mean_difference"], 0.0)

    def test_no_whole_population_conversion_table(self):
        # 工件只声明支持单元，不存在面向全人群/remote/skip 的换算系数
        self.assertEqual(len(self.artifact["applicability"]), 1)
        self.assertEqual(self.artifact["applicability"][0]["group"], "urban")
        self.assertIn("retains two separate raw scales", self.artifact["scale_policy"])

    def test_fit_is_deterministic_and_fingerprinted(self):
        again = fit_mapping(self.data)
        self.assertEqual(again["content_fingerprint"], self.artifact["content_fingerprint"])
        self.assertTrue(fingerprint_matches(self.artifact))
        tampered = copy.deepcopy(self.artifact)
        tampered["anchor_scale"]["range"] = [0, 99]
        self.assertFalse(fingerprint_matches(tampered))

    def test_form_effect_quantifies_removed_item(self):
        effect = self.artifact["form_effect"]["urban|full"]["dropped_item_contribution"]
        # availability 是被移除题目，人均贡献在 1 分以上
        self.assertGreater(effect["mean_difference"], 1.0)
        self.assertEqual(len(effect["ci95"]), 2)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mapping_path = Path(self.temp.name) / "mapping.json"
        self.report_path = Path(self.temp.name) / "report.json"
        self.artifact = fit(self.data, self.mapping_path)

    def _record(self, **overrides):
        base = {
            "record_id": "r-x",
            "subject_id": "s-x",
            "version": "short",
            "group": "urban",
            "collected_at": "2026-09-01",
            "analysis_consent": True,
            "displayed": ["contact", "travel", "digital"],
            "answers": {"contact": 2, "travel": 1, "digital": 0},
            "branch": "full",
        }
        base.update(overrides)
        return base

    def test_apply_roundtrip_and_classification_reasons(self):
        report = apply(self.data, load_artifact(self.mapping_path),
                       output_path=self.report_path)
        saved = json.loads(self.report_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["mapping_fingerprint"], self.artifact["content_fingerprint"])

        reasons = {
            tuple(row["reasons"]) if row["reasons"] else ("converted",)
            for row in report["records"]
        }
        # 全部预期原因类别都出现，且转换成功的记录有可比较分
        self.assertIn(("converted",), reasons)
        self.assertIn((REASON_CONSENT_REFUSED,), reasons)
        self.assertIn((REASON_COMMON_ITEM_DRIFT,), reasons)
        converted = [r for r in report["records"] if r["conversion_status"] == "converted"]
        self.assertTrue(converted)
        self.assertTrue(all(r["comparable_score"] is not None for r in converted))
        self.assertTrue(all(r["comparable_scale"] == "common_anchor_sum" for r in converted))

    def test_consent_refused_gets_no_derived_scores_but_stays_in_volume(self):
        record = self._record(record_id="r-refuse", analysis_consent=False)
        row = classify_record(record, self.artifact)
        self.assertIsNone(row["raw_score"])
        self.assertIsNone(row["comparable_score"])
        self.assertEqual(row["reasons"], [REASON_CONSENT_REFUSED])
        # 队列量仍包含该记录（weekly trend 的 volume_total 不过滤授权）
        report = apply({"responses": [record]}, self.artifact)
        self.assertEqual(report["weekly_trends"][0]["volume_total"], 1)
        self.assertEqual(report["weekly_trends"][0]["consent_refused"], 1)
        self.assertIsNone(report["weekly_trends"][0]["raw_mean_consenting"])

    def test_skip_travel_branch_refused_with_branch_reason(self):
        record = self._record(
            branch="skip-travel",
            displayed=["contact", "digital"],
            answers={"contact": 3, "digital": 2},
        )
        row = classify_record(record, self.artifact)
        self.assertIsNone(row["comparable_score"])
        self.assertIn("bridge_branch_missing", row["reasons"])

    def test_unsupported_version_and_unknown_scope(self):
        third = self._record(
            record_id="r-third", version="third", group="new-region", branch="other",
            displayed=["new-item"], answers={"new-item": 2},
        )
        row = classify_record(third, self.artifact)
        self.assertEqual(row["reasons"], ["unsupported_version"])
        report = apply({"responses": [third]}, self.artifact)
        self.assertEqual(report["out_of_scope"][0]["record_id"], "r-third")
        self.assertEqual(report["out_of_scope"][0]["reason"], "unsupported_version")

    def test_tampered_mapping_is_rejected(self):
        tampered = copy.deepcopy(self.artifact)
        tampered["anchor_scale"]["range"] = [0, 99]
        with self.assertRaises(ArtifactError):
            apply(self.data, tampered)
        bad_path = Path(self.temp.name) / "bad.json"
        bad_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(ArtifactError):
            load_artifact(bad_path)

    def test_new_batch_cannot_change_saved_mapping(self):
        before = self.mapping_path.read_text(encoding="utf-8")
        # 新批次只含 remote/skip 等无支持单元的数据
        new_batch = {
            "responses": [
                self._record(record_id="n1", group="remote",
                             answers={"contact": 3, "travel": 0, "digital": 0}),
                self._record(record_id="n2", branch="skip-travel",
                             displayed=["contact", "digital"],
                             answers={"contact": 1, "digital": 1}),
            ]
        }
        apply(new_batch, load_artifact(self.mapping_path))
        self.assertEqual(self.mapping_path.read_text(encoding="utf-8"), before)

    def test_as_of_gating_excludes_future_confirmations(self):
        record = self._record(
            confirmed_demand={"value": True, "known_at": "2026-09-20"}
        )
        self.assertEqual(confirmation_visible(record, "2026-08-08"), "pending")
        self.assertEqual(confirmation_visible(record, "2026-09-30"), "visible_true")
        report_default = apply({"responses": [record]}, self.artifact)  # as_of=fit_known_through
        self.assertEqual(
            report_default["demand_trend"]["status_counts"]["pending"], 1
        )
        report_later = apply({"responses": [record]}, self.artifact, as_of="2026-09-30")
        self.assertEqual(
            report_later["demand_trend"]["status_counts"]["visible_true"], 1
        )
        # 无论 as-of 如何变化，个人分数不受确认结果影响
        self.assertEqual(
            report_default["records"][0]["comparable_score"],
            report_later["records"][0]["comparable_score"],
        )

    def test_reconciliation_attributes_decline_to_form_not_population(self):
        report = apply(self.data, self.artifact)
        cell = report["raw_decline_reconciliation"]["cell_decomposition"][0]
        self.assertTrue(cell["decomposable"])
        self.assertGreater(cell["form_effect_removed_items"], 1.0)
        self.assertLess(abs(cell["anchor_population_change_short_minus_long"]), 0.5)
        self.assertIn("问卷形式", report["raw_decline_reconciliation"]["conclusion"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mapping_path = str(Path(self.temp.name) / "mapping.json")
        self.report_path = str(Path(self.temp.name) / "report.json")

    def test_cli_fit_then_apply_subprocess(self):
        fit_cmd = [
            sys.executable, "-m", "intake_hub.questionnaire", "fit",
            "--input", str(FIXTURE), "--output", self.mapping_path,
        ]
        fit_run = subprocess.run(fit_cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(fit_run.returncode, 0, fit_run.stderr)
        apply_cmd = [
            sys.executable, "-m", "intake_hub.questionnaire", "apply",
            "--input", str(FIXTURE), "--mapping", self.mapping_path,
            "--output", self.report_path,
        ]
        apply_run = subprocess.run(apply_cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(apply_run.returncode, 0, apply_run.stderr)
        report = json.loads(Path(self.report_path).read_text(encoding="utf-8"))
        self.assertEqual(report["record_count"], 181)
        self.assertGreater(report["converted_count"], 0)

    def test_cli_rejects_tampered_mapping(self):
        cli_main(["fit", "--input", str(FIXTURE), "--output", self.mapping_path])
        artifact = json.loads(Path(self.mapping_path).read_text(encoding="utf-8"))
        artifact["cells"] = []
        Path(self.mapping_path).write_text(json.dumps(artifact), encoding="utf-8")
        exit_code = cli_main(["apply", "--input", str(FIXTURE),
                              "--mapping", self.mapping_path, "--output", self.report_path])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
