"""Build the standalone BridgeSmoke app and package it as dist/smoke.ipa."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from npabridge import macho, package  # noqa: E402


SOURCE = ROOT / "smoke" / "BridgeSmoke" / "BridgeSmokeApp.m"
PLIST = ROOT / "smoke" / "BridgeSmoke" / "Info.plist"
BUNDLE = ROOT / "build" / "smoke" / "BridgeSmoke.app"
BRIDGE = ROOT / "build" / "LibASSBridge.dylib"
OUTPUT = ROOT / "dist" / "smoke.ipa"
TARGET = "arm64-apple-ios13.0"


def build_bundle(bundle: Path = BUNDLE) -> Path:
    """Compile the smoke app and assemble its bundle layout."""

    if not BRIDGE.is_file():
        raise FileNotFoundError(f"bridge dylib is missing: {BRIDGE}")
    binary = bundle / "BridgeSmoke"
    frameworks = bundle / "Frameworks"
    frameworks.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            macho.xcrun_find("clang"),
            "-target",
            TARGET,
            "-isysroot",
            str(macho.sdk_path()),
            "-arch",
            "arm64",
            "-miphoneos-version-min=13.0",
            "-fobjc-arc",
            "-O1",
            "-framework",
            "Foundation",
            "-framework",
            "UIKit",
            "-framework",
            "CoreText",
            "-o",
            str(binary),
            str(SOURCE),
        ],
        check=True,
    )
    shutil.copy2(PLIST, bundle / "Info.plist")
    shutil.copy2(BRIDGE, frameworks / "LibASSBridge.dylib")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args(argv)
    try:
        report = package.publish_app_bundle(build_bundle(), arguments.output)
        print(json.dumps(report, indent=2, sort_keys=True))
    except Exception as error:
        print(f"smoke build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
