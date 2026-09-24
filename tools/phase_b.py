"""Phase B: write the payload and branch patches into the frozen layout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from npabridge.macho import phase_b  # noqa: E402
from npabridge.manifest import load_manifest  # noqa: E402


DEFAULT_LAYOUT = ROOT / "build" / "macho" / "main-phase-a"
DEFAULT_OUTPUT = ROOT / "build" / "macho" / "main-phase-b"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    try:
        manifest = load_manifest(ROOT / "manifests" / "nplayer-3.13.0.json")
        report = phase_b(arguments.layout, arguments.output, manifest)
        print(json.dumps(report, indent=2, sort_keys=True))
    except Exception as error:
        print(f"phase B failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
