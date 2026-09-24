"""Shared test inputs.

Tests that need a dev-only input skip when it is absent, so the public
suite stays runnable without an nPlayer IPA on the machine.
"""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_IPA = Path(os.environ.get("NPA_SOURCE_IPA", ROOT.parent / "nPlayer_3.13.0.ipa"))
