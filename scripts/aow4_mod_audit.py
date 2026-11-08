"""Self-contained Skill CLI and compatibility exports for earlier helper scripts."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aow4mod.core import *
from aow4mod.cli import main

if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
