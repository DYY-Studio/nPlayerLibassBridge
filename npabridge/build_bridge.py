"""Build and verify the 15-export LibASSBridge dylib.

The bridge is a thin pass-through layer over the statically linked libass
0.17.5 closure. It must stay a plain dylib: no implicit initializers, no
third-party dynamic dependency and exactly the 15 exported symbols.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

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
INSTALL_NAME = "@rpath/LibASSBridge.dylib"
FORBIDDEN_PATH_PREFIXES = ("/usr/local/", "/opt/homebrew/", "/Users/")
FORBIDDEN_DEPENDENCY_STEMS = (
    "libass",
    "libfreetype",
    "libfontconfig",
    "libexpat",
    "libharfbuzz",
    "libfribidi",
)
INITIALIZER_SOURCE_TOKENS = (
    "__mod_init_func",
    "__mod_term_func",
    "__cxa_atexit",
    "constructor",
)


def _xcrun(*arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/xcrun", *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return result.stdout.strip()


def _sdk_path() -> Path:
    return Path(_xcrun("--sdk", "iphoneos", "--show-sdk-path"))


def _run(command: list[object], cwd: Path | None = None) -> str:
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


def _macho(path: Path) -> tuple[object, object]:
    import lief

    parsed = lief.MachO.parse(str(path))
    binaries = list(parsed) if parsed is not None else []
    if len(binaries) != 1:
        raise ValueError(f"bridge is not one Mach-O binary: {path}")
    return parsed, binaries[0]


def _enum_name(value: object) -> str:
    return str(value).rsplit(".", 1)[-1].upper()


def _version_tuple(value: object) -> list[int]:
    parts = tuple(int(part) for part in value)
    if len(parts) > 3:
        raise ValueError(f"invalid Mach-O version: {parts}")
    return list(parts + (0,) * (3 - len(parts)))


def nm_exports(path: Path = OUTPUT) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    _, binary = _macho(path)
    return sorted({str(symbol.name) for symbol in binary.exported_symbols})


def _section_size(binary: object, name: str) -> int:
    return sum(int(section.size) for section in binary.sections if str(section.name) == name)


def _dependency_lines(path: Path) -> list[str]:
    lines = [line.strip() for line in _run([_xcrun("--find", "otool"), "-L", path]).splitlines()]
    if not lines or not lines[0].endswith(":"):
        raise ValueError(f"otool dependency output has no Mach-O header: {path}")
    return [line.split()[0] for line in lines[1:]]


def _install_name(path: Path) -> str:
    lines = [
        line.strip()
        for line in _run([_xcrun("--find", "otool"), "-D", path]).splitlines()
        if line.strip()
    ]
    if len(lines) != 2 or not lines[0].endswith(":"):
        raise ValueError(f"otool install-name output is malformed: {path}")
    return lines[1]


def _validate_dependencies(path: Path) -> list[str]:
    dependencies = _dependency_lines(path)
    if dependencies.count(INSTALL_NAME) != 1:
        raise ValueError(f"bridge must carry exactly one self install name: {INSTALL_NAME}")
    external = [item for item in dependencies if item != INSTALL_NAME]
    if not external:
        raise ValueError("bridge has no dynamic dependency")
    for dependency in external:
        if not dependency.startswith("/usr/lib/"):
            raise ValueError(f"non-system dynamic dependency: {dependency}")
        stem = Path(dependency).name.split(".")[0]
        if stem in FORBIDDEN_DEPENDENCY_STEMS:
            raise ValueError(f"static dependency leaked into the dylib: {dependency}")
    if _install_name(path) != INSTALL_NAME:
        raise ValueError(f"bridge install name is not {INSTALL_NAME}")
    data = path.read_bytes()
    for prefix in FORBIDDEN_PATH_PREFIXES:
        if prefix.encode() in data:
            raise ValueError(f"host path in bridge bytes: {prefix}")
    return external


def _validate_source() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for token in INITIALIZER_SOURCE_TOKENS:
        if token in source:
            raise ValueError(f"bridge source contains implicit initialization: {token}")


def verify_bridge(
    path: Path = OUTPUT,
    manifest: Manifest | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(MANIFEST_PATH) if manifest is None else manifest
    bridge = Path(path).resolve()
    if bridge.suffix != ".dylib" or not bridge.is_file():
        raise ValueError(f"bridge output is not a dylib: {bridge}")
    _validate_source()
    _, binary = _macho(bridge)
    cpu = _enum_name(binary.header.cpu_type).lower()
    file_type = _enum_name(binary.header.file_type)
    if cpu != "arm64":
        raise ValueError(f"bridge is not arm64: {cpu}")
    if file_type != "DYLIB":
        raise ValueError(f"bridge is not a dylib: {file_type}")
    build_version = binary.build_version
    if build_version is None:
        raise ValueError("bridge has no LC_BUILD_VERSION")
    platform = _enum_name(build_version.platform)
    minos = _version_tuple(build_version.minos)
    if platform != "IOS" or minos[:2] != [13, 0]:
        raise ValueError(f"bridge target is not iOS 13: {platform} {minos}")
    expected = {api.macho_name for api in manifest.apis}
    exports = nm_exports(bridge)
    if set(exports) != expected:
        raise ValueError(
            f"bridge exports differ: expected={sorted(expected)} actual={exports}"
        )
    global_initializers = sorted(
        {
            str(symbol.name)
            for symbol in binary.symbols
            if str(symbol.name).lstrip("_").startswith("GLOBAL__sub_I_")
        }
    )
    if global_initializers:
        raise ValueError(f"bridge has C++ global initializers: {global_initializers}")
    cxa_atexit = sorted(
        {str(symbol.name) for symbol in binary.imported_symbols if "__cxa_atexit" in str(symbol.name)}
    )
    if cxa_atexit:
        raise ValueError(f"bridge imports __cxa_atexit: {cxa_atexit}")
    mod_init_size = _section_size(binary, "__mod_init_func")
    mod_term_size = _section_size(binary, "__mod_term_func")
    if mod_init_size or mod_term_size:
        raise ValueError(
            f"bridge has initialization sections: init={mod_init_size} term={mod_term_size}"
        )
    dependencies = _validate_dependencies(bridge)
    report = {
        "target": TARGET,
        "path": str(bridge),
        "cpu": cpu,
        "file_type": file_type,
        "platform": platform,
        "minos": minos,
        "exports": exports,
        "install_name": INSTALL_NAME,
        "dependencies": dependencies,
        "mod_init_size": mod_init_size,
        "mod_term_size": mod_term_size,
        "global_initializers": global_initializers,
        "source": str(SOURCE),
        "export_list": str(EXPORT_LIST),
        "manifest": str(MANIFEST_PATH),
        "closure": str(CLOSURE_PATH),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def compile_bridge(sdk: Path, include_root: Path = INCLUDE_ROOT) -> Path:
    OBJECT.parent.mkdir(parents=True, exist_ok=True)
    OBJECT.unlink(missing_ok=True)
    _run(
        [
            _xcrun("--find", "clang"),
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
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    _run(
        [
            _xcrun("--find", "clang"),
            "-target",
            TARGET,
            "-isysroot",
            str(sdk),
            "-arch",
            "arm64",
            "-miphoneos-version-min=13.0",
            "-dynamiclib",
            "-Wl,-install_name," + INSTALL_NAME,
            "-Wl,-exported_symbols_list," + str(EXPORT_LIST),
            str(OBJECT),
            *(str(archive) for archive in archives),
            *system_link_args,
            "-o",
            str(output),
        ]
    )
    return output


def build_bridge(output: Path = OUTPUT) -> Path:
    archives, system_link_args = load_closure()
    sdk = _sdk_path()
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
