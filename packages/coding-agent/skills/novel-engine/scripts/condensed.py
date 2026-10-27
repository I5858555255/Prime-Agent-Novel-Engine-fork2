"""Cross-platform launcher; use the Python environment with project dependencies."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from novel_engine.condensed.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
