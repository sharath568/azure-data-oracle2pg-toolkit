"""Regenerate docs/rules.md and docs/conversion-rules.md from the rule registries.

Usage:
    python scripts/gen_rules_doc.py          # write both files
    python scripts/gen_rules_doc.py --check  # exit 1 if either file is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from o2pg.catalog import rules_markdown  # noqa: E402  (needs the src path above)
from o2pg.convert import conversion_rules_markdown  # noqa: E402

TARGETS = {
    ROOT / "docs" / "rules.md": rules_markdown,
    ROOT / "docs" / "conversion-rules.md": conversion_rules_markdown,
}


def main() -> int:
    """Write or check the rule references. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a file is out of date")
    args = parser.parse_args()
    stale = []
    for target, render in TARGETS.items():
        content = render()
        if args.check:
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            if current != content:
                stale.append(target)
            continue
        target.write_text(content, encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}")
    for target in stale:
        print(f"{target.relative_to(ROOT)} is stale; run scripts/gen_rules_doc.py", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
