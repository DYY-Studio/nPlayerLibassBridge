"""Verify one patched main (and optional bridge dylib) and write a report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from npabridge.manifest import load_manifest  # noqa: E402
from npabridge.verify import verify_artifact  # noqa: E402


DEFAULT_BASELINE = ROOT / "build" / "input" / "nPlayer"
DEFAULT_MAIN = ROOT / "build" / "macho" / "main-phase-b"
DEFAULT_BRIDGE = ROOT / "build" / "LibASSBridge.dylib"
DEFAULT_REPORT = ROOT / "dist" / "verification.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--main", type=Path, default=DEFAULT_MAIN)
    parser.add_argument("--bridge", type=Path, default=None)
    parser.add_argument(
        "--without-bridge",
        action="store_true",
        help="verify the fallback variant instead of the bridge variant",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    arguments = parser.parse_args(argv)
    try:
        manifest = load_manifest(ROOT / "manifests" / "nplayer-3.13.0.json")
        bridge = None
        if not arguments.without_bridge:
            bridge = arguments.bridge or DEFAULT_BRIDGE
            if not bridge.is_file():
                raise FileNotFoundError(bridge)
        report = verify_artifact(arguments.baseline, arguments.main, manifest, bridge)
        report.write(arguments.report)
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        report.require()
    except Exception as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
