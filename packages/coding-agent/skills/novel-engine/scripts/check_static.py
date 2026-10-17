#!/usr/bin/env python3
"""Static check: pyflakes undefined-name scan over novel_engine non-test sources.

Exit code 0 = clean.  Non-zero = list of violations printed to stdout.
"""
from __future__ import annotations
import sys
import subprocess
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / 'src' / 'novel_engine'
EXCLUDE = {SRC / 'tests'}


def main() -> int:
    py_files = [
        str(p) for p in SRC.rglob('*.py')
        if not any(exc in p.parts for exc in ('tests', '__pycache__'))
    ]
    if not py_files:
        print('No source files found', file=sys.stderr)
        return 2
    result = subprocess.run(
        [sys.executable, '-m', 'pyflakes'] + py_files,
        capture_output=True, text=True,
    )
    lines = [l for l in result.stdout.splitlines() if 'undefined name' in l]
    if lines:
        print('Undefined-name violations:')
        for line in lines:
            print(line)
        return 1
    print('OK: 0 undefined-name violations')
    return 0


if __name__ == '__main__':
    sys.exit(main())
