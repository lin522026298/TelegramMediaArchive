"""Run isolated regressions for the findings fixed in 0.1.7."""
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests"), "-p", "test_review_fixes.py", "-v"], cwd=root))
