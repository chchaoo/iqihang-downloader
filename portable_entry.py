"""Entry point for the self-contained Windows distribution."""

import os
import sys
from pathlib import Path


def main():
    if getattr(sys, "frozen", False):
        tools = str(Path(sys.executable).resolve().parent / "tools")
        os.environ["PATH"] = tools + os.pathsep + os.environ.get("PATH", "")
    if sys.argv[1:] == ["--transfer-worker"]:
        from transfer_process import worker
        worker()
    else:
        from app import main as desktop
        desktop()


if __name__ == "__main__":
    main()
