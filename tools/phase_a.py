"""Phase A: freeze the final Mach-O layout for the libass payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from npabridge.macho import extract_clean_main, phase_a  # noqa: E402
from npabridge.manifest import load_manifest  # noqa: E402


DEFAULT_INPUT = ROOT / "build" / "input" / "nPlayer"
DEFAULT_IPA = ROOT.parent / "nPlayer_3.13.0.ipa"
DEFAULT_OUTPUT = ROOT / "build" / "macho" / "main-phase-a"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    try:
        source = arguments.input or (DEFAULT_INPUT if DEFAULT_INPUT.is_file() else DEFAULT_IPA)
        input_path = extract_clean_main(source, DEFAULT_INPUT)
        manifest = load_manifest(ROOT / "manifests" / "nplayer-3.13.0.json")
        report = phase_a(input_path, arguments.output, manifest)
        print(json.dumps(report, indent=2, sort_keys=True))
    except Exception as error:
        print(f"phase A failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
