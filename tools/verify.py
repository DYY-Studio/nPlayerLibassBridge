"""Verify one patched main (and optional bridge dylib) and write a report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from npabridge import package  # noqa: E402
from npabridge.manifest import load_manifest  # noqa: E402
from npabridge.verify import MODES, verify_artifact  # noqa: E402


IPA = ROOT.parent / "nPlayer_3.13.0.ipa"
DEFAULT_MAIN = ROOT / "build" / "macho" / "main-phase-b"
DEFAULT_BRIDGE = ROOT / "build" / "LibASSBridge.dylib"
DEFAULT_REPORT = ROOT / "dist" / "verification.json"
EXTRACT_ROOT = ROOT / "build" / "verify"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipa", type=Path, default=None, help="verify a packaged IPA")
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--main", type=Path, default=DEFAULT_MAIN)
    parser.add_argument("--bridge", type=Path, default=DEFAULT_BRIDGE)
    parser.add_argument("--mode", choices=MODES, default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    arguments = parser.parse_args(argv)
    try:
        manifest = load_manifest(ROOT / "manifests" / "nplayer-3.13.0.json")
        baseline = arguments.baseline
        if baseline is None:
            baseline = EXTRACT_ROOT / "nPlayer"
            if not baseline.is_file():
                from npabridge.macho import extract_clean_main

                extract_clean_main(IPA, baseline)
        if arguments.ipa is not None:
            extracted = package.extract_for_verification(
                arguments.ipa, EXTRACT_ROOT / arguments.ipa.stem
            )
            main = extracted["main"]
            bridge = extracted.get("bridge")
        else:
            main = arguments.main
            bridge = arguments.bridge if arguments.bridge.is_file() else None
        mode = arguments.mode or (arguments.ipa.stem if arguments.ipa else None)
        if mode not in MODES:
            raise ValueError(f"cannot infer a verification mode for {mode}")
        report = verify_artifact(baseline, main, manifest, bridge, mode)
        report.write(arguments.report)
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        report.require()
    except Exception as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
