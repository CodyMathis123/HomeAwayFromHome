#!/usr/bin/env python3
"""SSH forced-command wrapper: original iOS Shortcut still sends 'pcctl gaming wake'."""
import os
import re
import sys
from pcctl import main

PATTERN = re.compile(r"^(?:pcctl )?([a-z][a-z0-9-]{0,31}) (status|wake|sleep|restart|shutdown)$")

def dispatch():
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    match = PATTERN.fullmatch(original)
    if not match:
        print("Only pcctl HOST ACTION is permitted", file=sys.stderr)
        return 2
    return main(list(match.groups()))

if __name__ == "__main__":
    raise SystemExit(dispatch())
