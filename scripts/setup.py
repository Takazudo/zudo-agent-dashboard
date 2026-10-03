"""Run deterministic setup from a clone, without installing any package."""
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("Setup requires Python 3.11 or later")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from zudo_agent.setup import main

if __name__ == "__main__":
    main()
