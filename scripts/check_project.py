from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "project.json").read_text(encoding="utf-8"))
    started = time.monotonic()
    commands = [
        [sys.executable, "-m", "unittest", "discover", "-s", config["tests"], "-v"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests/analysis_context", "-v"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests/questionnaire_linking", "-v"],
        [sys.executable, "-m", "compileall", "-q", config["package"], "tests", "scripts"],
    ]
    for command in commands:
        print("执行：", " ".join(command), flush=True)
        subprocess.run(command, cwd=root, check=True, timeout=65)
    with tempfile.TemporaryDirectory(prefix="project-smoke-") as directory:
        command = [
            sys.executable, "-m", config["entrypoint"],
            *config["fixture_prefix"], "fixtures/demo.json",
            config["database_flag"], str(Path(directory) / "state.sqlite3"),
        ]
        print("执行：", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=True, timeout=15)
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or not response:
            raise RuntimeError("服务没有输出有效结果")
        print(json.dumps(response, ensure_ascii=False, sort_keys=True), flush=True)

        mapping_path = str(Path(directory) / "questionnaire_mapping.json")
        report_path = str(Path(directory) / "questionnaire_report.json")
        fit_command = [
            sys.executable, "-m", "intake_hub.questionnaire", "fit",
            "--input", config["analysis_fixture"], "--output", mapping_path,
        ]
        print("执行：", " ".join(fit_command), flush=True)
        subprocess.run(fit_command, cwd=root, check=True, timeout=30)
        apply_command = [
            sys.executable, "-m", "intake_hub.questionnaire", "apply",
            "--input", config["analysis_fixture"], "--mapping", mapping_path,
            "--output", report_path,
        ]
        print("执行：", " ".join(apply_command), flush=True)
        apply_run = subprocess.run(apply_command, cwd=root, capture_output=True, text=True, check=True, timeout=30)
        apply_summary = json.loads(apply_run.stdout)
        if not apply_summary.get("mapping_id") or apply_summary.get("record_count", 0) == 0:
            raise RuntimeError("问卷衔接 apply 没有产出有效报告")
        print(json.dumps(apply_summary, ensure_ascii=False, sort_keys=True), flush=True)
    print(json.dumps({"结果": "通过", "耗时秒": round(time.monotonic() - started, 3)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
