"""Shared frozen entry point. CLI also hosts isolated inference workers."""
import multiprocessing
from pathlib import Path
import sys


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if Path(sys.executable).stem == 'PaperBridge':
        from paperbridge.gui import launch
        raise SystemExit(launch())
    from paperbridge.cli import main
    raise SystemExit(main())
