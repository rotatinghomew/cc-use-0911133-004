from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DataContextTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures/questionnaire_link.json").read_text(encoding="utf-8"))

    def test_presentation_and_nonresponse_have_distinct_encodings(self):
        self.assertTrue(any("travel" not in row["displayed"] for row in self.data["responses"]))
        self.assertTrue(any(value is None for row in self.data["responses"] for value in row["answers"].values()))
        self.assertTrue(all(set(row["answers"]) <= set(row["displayed"]) for row in self.data["responses"]))

    def test_bridge_people_and_consent(self):
        self.assertTrue(all(row["long"]["subject_id"] == row["short"]["subject_id"] for row in self.data["bridges"]))
        self.assertTrue(any(not row["analysis_consent"] for row in self.data["responses"]))
        self.assertTrue(any(row["version"] == "third" for row in self.data["responses"]))

