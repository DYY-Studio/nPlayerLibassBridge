"""Thin entry point for the public patch flow."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from npabridge.patch import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
