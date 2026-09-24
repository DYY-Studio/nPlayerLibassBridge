"""Package one pseudo-signed IPA variant."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from npabridge import macho, package  # noqa: E402


MAIN_ARTIFACTS = {
    "baseline": ROOT / "build" / "macho" / "main-baseline",
    "weak-load-only": ROOT / "build" / "macho" / "main-phase-a",
    "fallback": ROOT / "build" / "macho" / "main-phase-b",
    "bridge": ROOT / "build" / "macho" / "main-phase-b",
}
DEFAULT_MAIN = ROOT / "build" / "input" / "nPlayer"


def _baseline_main(source_ipa: Path) -> Path:
    output = MAIN_ARTIFACTS["baseline"]
    clean = macho.extract_clean_main(source_ipa, DEFAULT_MAIN)
    macho.nop_only_main(clean, output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=package.VARIANTS, required=True)
    parser.add_argument("--source", type=Path, default=package.SOURCE_IPA)
    parser.add_argument("--dist", type=Path, default=package.DIST)
    parser.add_argument("--main", type=Path, default=None)
    parser.add_argument("--bridge", type=Path, default=ROOT / "build" / "LibASSBridge.dylib")
    arguments = parser.parse_args(argv)
    try:
        if arguments.main is not None:
            main = arguments.main
        elif arguments.variant == "baseline":
            main = _baseline_main(arguments.source)
        else:
            main = MAIN_ARTIFACTS[arguments.variant]
        if not main.is_file():
            raise FileNotFoundError(f"main artifact is missing: {main}")
        bridge = arguments.bridge if arguments.variant == "bridge" else None
        if bridge is not None and not bridge.is_file():
            raise FileNotFoundError(f"bridge artifact is missing: {bridge}")
        report = package.publish(
            arguments.variant,
            arguments.source,
            arguments.dist / f"{arguments.variant}.ipa",
            main,
            bridge,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
    except Exception as error:
        print(f"packaging failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
