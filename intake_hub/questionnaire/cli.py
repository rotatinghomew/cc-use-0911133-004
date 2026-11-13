"""``python -m intake_hub.questionnaire`` 命令行入口。

子命令：
- ``fit --input 数据.json --output 映射.json``: 拟合并保存带版本与适用范围的映射；
- ``apply --input 新批次.json --mapping 映射.json --output 报告.json [--as-of YYYY-MM-DD]``:
  读取已保存（指纹校验通过）的映射应用到新批次，不重新拟合。

两个子命令都支持 ``--input`` 与 ``--output``；fit 的输出可被 apply 直接读取。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from intake_hub.questionnaire.pipeline import (
    ArtifactError,
    apply,
    fit,
    load_artifact,
    load_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intake_hub.questionnaire",
        description="问卷量尺衔接（fit/apply），仅分析用途，不改变授权与分流规则",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fit_parser = sub.add_parser("fit", help="拟合 long/short 锚点衔接映射并保存")
    fit_parser.add_argument("--input", required=True, help="问卷批量资料 JSON（含 bridges/responses）")
    fit_parser.add_argument("--output", required=True, help="映射工件输出路径（apply 可直接读取）")

    apply_parser = sub.add_parser("apply", help="把已保存映射应用到新批次问卷")
    apply_parser.add_argument("--input", required=True, help="新批次问卷 JSON")
    apply_parser.add_argument("--mapping", required=True, help="fit 保存的映射工件路径")
    apply_parser.add_argument("--output", required=True, help="应用报告输出路径")
    apply_parser.add_argument(
        "--as-of",
        default=None,
        help="人工确认可见性截止日（默认取映射的 fit_known_through）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "fit":
            data = load_json(args.input)
            artifact = fit(data, args.output)
            print(
                json.dumps(
                    {
                        "command": "fit",
                        "mapping_id": artifact["id"],
                        "mapping_version": artifact["mapping_version"],
                        "output": str(Path(args.output)),
                        "supported_cells": [
                            {"group": c["group"], "branch": c["branch"]}
                            for c in artifact["cells"]
                            if c["supported"]
                        ],
                        "unsupported_cells": artifact["checks"]["branch_coverage"][
                            "unsupported_cells"
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        data = load_json(args.input)
        artifact = load_artifact(args.mapping)
        report = apply(data, artifact, as_of=args.as_of, output_path=args.output)
        print(
            json.dumps(
                {
                    "command": "apply",
                    "mapping_id": report["mapping_id"],
                    "output": str(Path(args.output)),
                    "record_count": report["record_count"],
                    "converted_count": report["converted_count"],
                    "not_converted_count": report["not_converted_count"],
                    "out_of_scope": report["out_of_scope"],
                    "reconciliation_conclusion": report["raw_decline_reconciliation"]["conclusion"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except ArtifactError as exc:
        print(f"映射工件错误: {exc}", file=sys.stderr)
        return 2
    except (KeyError, ValueError, FileNotFoundError) as exc:
        print(f"输入错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
