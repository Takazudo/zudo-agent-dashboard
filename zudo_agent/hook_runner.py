"""Absolute-path hook entrypoint: works when the clone is outside the project."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    from zudo_agent.cli import main as observe
    from zudo_agent.model import DIGEST
    # Ownership is only a deterministic configuration marker, never a decision.
    if len(sys.argv) < 3 or sys.argv[1] != "--owner" or not DIGEST.fullmatch(sys.argv[2]):
        return
    sys.argv = [sys.argv[0], *sys.argv[3:]]
    observe()


if __name__ == "__main__":
    main()
