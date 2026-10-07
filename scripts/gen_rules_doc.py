"""Regenerate docs/rules.md from the rule registry.

Usage:
    python scripts/gen_rules_doc.py          # write docs/rules.md
    python scripts/gen_rules_doc.py --check  # exit 1 if docs/rules.md is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from o2pg.catalog import rules_markdown  # noqa: E402  (needs the src path above)

TARGET = ROOT / "docs" / "rules.md"


def main() -> int:
    """Write or check the rules reference. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args()
    content = rules_markdown()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != content:
            print(
                f"{TARGET.relative_to(ROOT)} is stale; run scripts/gen_rules_doc.py",
                file=sys.stderr,
            )
            return 1
        return 0
    TARGET.write_text(content, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
