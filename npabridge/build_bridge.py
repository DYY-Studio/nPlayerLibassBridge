"""Build the 15-export LibASSBridge dylib.

The bridge is a thin pass-through layer over the statically linked libass
0.17.5 closure. It must stay a plain dylib: no implicit initializers, no
third-party dynamic dependency and exactly the 15 exported symbols.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import macho, verify
from .manifest import Manifest, load_manifest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bridge" / "npa_ass_bridge.c"
EXPORT_LIST = ROOT / "bridge" / "bridge.exports"
OUTPUT = ROOT / "build" / "LibASSBridge.dylib"
OBJECT = ROOT / "build" / "bridge" / "npa_ass_bridge.o"
REPORT = ROOT / "build" / "bridge" / "verification.json"
CLOSURE_PATH = ROOT / "build" / "deps" / "libass-closure.txt"
INCLUDE_ROOT = ROOT / "build" / "deps" / "include"
LIB_ROOT = ROOT / "build" / "deps" / "lib"
MANIFEST_PATH = ROOT / "manifests" / "nplayer-3.13.0.json"
TARGET = "arm64-apple-ios13.0"
INSTALL_NAME = verify.BRIDGE_INSTALL_NAME


def _run(command: list[object], cwd: Path | None = None) -> str:
    import subprocess

    result = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {command}\n{result.stdout}")
    return result.stdout


def load_closure(path: Path = CLOSURE_PATH) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    if not path.is_file():
        raise FileNotFoundError(f"dependency closure is missing: {path}")
    values: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.split()
    archives = tuple(LIB_ROOT / name for name in values.get("project_archives", []))
    for archive in archives:
        if not archive.is_file():
            raise FileNotFoundError(archive)
    return archives, tuple(values.get("system_link_args", []))


def nm_exports(path: Path = OUTPUT) -> list[str]:
    return macho.exported_symbols(macho.parse(path))


def verify_bridge(path: Path = OUTPUT, manifest: Manifest | None = None) -> dict[str, Any]:
    manifest = load_manifest(MANIFEST_PATH) if manifest is None else manifest
    report = verify.verify_bridge(path, manifest)
    report.require()
    report.write(REPORT)
    return report.as_dict()


def compile_bridge(sdk: Path, include_root: Path = INCLUDE_ROOT) -> Path:
    OBJECT.parent.mkdir(parents=True, exist_ok=True)
    OBJECT.unlink(missing_ok=True)
    _run(
        [
            macho.xcrun_find("clang"),
            "-target",
            TARGET,
            "-isysroot",
            str(sdk),
            "-arch",
            "arm64",
            "-miphoneos-version-min=13.0",
            "-fvisibility=hidden",
            "-fno-common",
            "-I" + str(include_root),
            "-ffile-prefix-map=" + str(ROOT) + "=.",
            "-fdebug-prefix-map=" + str(ROOT) + "=.",
            "-O2",
            "-Werror=implicit-function-declaration",
            "-Werror=incompatible-pointer-types",
            "-Werror=return-type",
            "-c",
            str(SOURCE),
            "-o",
            str(OBJECT),
        ]
    )
    return OBJECT


def link_bridge(
    sdk: Path,
    archives: tuple[Path, ...],
    system_link_args: tuple[str, ...],
    output: Path = OUTPUT,
    export_list: Path = EXPORT_LIST,
    object_path: Path = OBJECT,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    _run(
        [
            macho.xcrun_find("clang"),
            "-target",
            TARGET,
            "-isysroot",
            str(sdk),
            "-arch",
            "arm64",
            "-miphoneos-version-min=13.0",
            "-dynamiclib",
            "-Wl,-install_name," + INSTALL_NAME,
            "-Wl,-exported_symbols_list," + str(export_list),
            str(object_path),
            *(str(archive) for archive in archives),
            *system_link_args,
            "-o",
            str(output),
        ]
    )
    return output


def build_bridge(output: Path = OUTPUT) -> Path:
    archives, system_link_args = load_closure()
    sdk = macho.sdk_path()
    compile_bridge(sdk)
    return link_bridge(sdk, archives, system_link_args, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if not arguments.verify_only:
            build_bridge()
        print(json.dumps(verify_bridge(), indent=2, sort_keys=True))
    except Exception as error:
        print(f"bridge build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
