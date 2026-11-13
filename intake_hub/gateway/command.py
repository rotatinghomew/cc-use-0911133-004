from __future__ import annotations

import argparse
import json
from pathlib import Path

from intake_hub.contracts.episode import process_fixture

def main() -> int:
    parser = argparse.ArgumentParser(prog='consent_intake')
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    raw = json.loads(Path(args.fixture).read_text(encoding='utf-8'))
    result = process_fixture(args.database, raw)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
