"""Read the iOS arm64 ABI constants the payload depends on.

The constants come from a probe object compiled against the iOS SDK, so
Dl_info layout and RTLD_DEFAULT are never hardcoded.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


REQUIRED_SYMBOLS = {
    "npa_rtld_default_bits": 8,
    "npa_dl_info_size": 4,
    "npa_dl_info_fname_offset": 4,
    "npa_dl_info_fbase_offset": 4,
    "npa_dl_info_sname_offset": 4,
    "npa_dl_info_saddr_offset": 4,
}


@dataclass(frozen=True)
class TargetABI:
    platform: str
    minos: tuple[int, int, int]
    sdk: tuple[int, int, int]
    dl_info_size: int
    dl_info_fname_offset: int
    dl_info_fbase_offset: int
    dl_info_sname_offset: int
    dl_info_saddr_offset: int
    rtld_default_masked: int


def _version_tuple(value: object) -> tuple[int, int, int]:
    parts = tuple(int(part) for part in value)
    if len(parts) > 3:
        raise ValueError(f"invalid version tuple: {parts}")
    return parts + (0,) * (3 - len(parts))


def _probe_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    return root / "tools" / "target_abi_probe.c", root / "build" / "target_abi_probe.o"


def _compile_probe(sdk: Path) -> Path:
    source, output = _probe_paths()
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "xcrun",
            "--sdk",
            str(sdk),
            "clang",
            "-target",
            "arm64-apple-ios13.0",
            "-c",
            str(source),
            "-o",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"target ABI probe compilation failed: {detail}")
    return output


def _read_symbol(binary: object, section: object, name: str) -> int:
    symbol_name = "_" + name
    symbol = next(
        (item for item in binary.symbols if item.name == symbol_name),
        None,
    )
    if symbol is None:
        raise ValueError(f"probe symbol is missing: {symbol_name}")
    size = REQUIRED_SYMBOLS[name]
    offset = int(symbol.value) - int(section.virtual_address)
    content = bytes(section.content)
    if offset < 0 or offset + size > len(content):
        raise ValueError(f"probe symbol is outside __const: {symbol_name}")
    return int.from_bytes(content[offset : offset + size], "little")


def load_target_abi(sdk: Path) -> TargetABI:
    import lief

    object_path = _compile_probe(Path(sdk))
    parsed = lief.MachO.parse(str(object_path))
    binaries = list(parsed) if parsed is not None else []
    if len(binaries) != 1:
        raise ValueError("target ABI probe did not contain one Mach-O binary")
    binary = binaries[0]
    sections = [
        item
        for item in binary.sections
        if item.segment_name == "__TEXT" and item.name == "__const"
    ]
    if len(sections) != 1:
        raise ValueError("target ABI probe must contain one __TEXT,__const section")
    section = sections[0]
    build_version = binary.build_version
    if build_version is None:
        raise ValueError("target ABI probe has no LC_BUILD_VERSION")
    platform = str(build_version.platform).rsplit(".", 1)[-1]
    if platform.upper() != "IOS":
        raise ValueError(f"target ABI probe platform is not iOS: {platform}")
    minos = _version_tuple(build_version.minos)
    if minos[:2] != (13, 0):
        raise ValueError(f"target ABI probe minos is not iOS 13: {minos}")
    values = {name: _read_symbol(binary, section, name) for name in REQUIRED_SYMBOLS}
    offsets = (
        values["npa_dl_info_fname_offset"],
        values["npa_dl_info_fbase_offset"],
        values["npa_dl_info_sname_offset"],
        values["npa_dl_info_saddr_offset"],
    )
    if any(offset < 0 or offset >= values["npa_dl_info_size"] for offset in offsets):
        raise ValueError("target ABI probe contains an invalid Dl_info offset")
    return TargetABI(
        platform=platform,
        minos=minos,
        sdk=_version_tuple(build_version.sdk),
        dl_info_size=values["npa_dl_info_size"],
        dl_info_fname_offset=values["npa_dl_info_fname_offset"],
        dl_info_fbase_offset=values["npa_dl_info_fbase_offset"],
        dl_info_sname_offset=values["npa_dl_info_sname_offset"],
        dl_info_saddr_offset=values["npa_dl_info_saddr_offset"],
        rtld_default_masked=values["npa_rtld_default_bits"],
    )
