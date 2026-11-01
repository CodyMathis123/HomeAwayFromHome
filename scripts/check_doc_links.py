#!/usr/bin/env python3
"""Reject broken local Markdown links in the maintained HomeAway docs."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^)]+)\)")


def check_file(path: Path) -> list[str]:
    errors = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for m in LINK.finditer(line):
            target = m.group(1).strip().split(" ", 1)[0].strip("<>")
            if not target or target.startswith("#"):
                continue
            parts = urlsplit(target)
            if parts.scheme or target.startswith("//"):
                continue
            candidate = ((path.parent / unquote(parts.path)).resolve())
            if not candidate.is_relative_to(ROOT) or not candidate.exists():
                errors.append(f"{path.relative_to(ROOT)}:{line_number}: broken link {target}")
    return errors


def main() -> int:
    errors = [error for file in FILES for error in check_file(file)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"PASS: local Markdown links in {len(FILES)} maintained HomeAway documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
