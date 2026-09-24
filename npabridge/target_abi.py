"""The iOS arm64 ABI constants the payload depends on.

The values are frozen in the manifest. They were read once from a probe
object compiled against the iOS SDK (`dev/abi_probe.py`), and they are
validated here on every load, so the Dl_info layout and the RTLD_DEFAULT
bit pattern are never hardcoded in code.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _version_tuple(value: object) -> tuple[int, int, int]:
    parts = tuple(int(part) for part in value)
    if len(parts) > 3:
        raise ValueError(f"invalid version tuple: {parts}")
    return parts + (0,) * (3 - len(parts))


def from_manifest(data: dict) -> TargetABI:
    """Build and validate the frozen ABI block of one manifest."""

    masked = data["rtld_default_masked"]
    abi = TargetABI(
        platform=str(data["platform"]),
        minos=_version_tuple(data["minos"]),
        sdk=_version_tuple(data["sdk"]),
        dl_info_size=int(data["dl_info_size"]),
        dl_info_fname_offset=int(data["dl_info_fname_offset"]),
        dl_info_fbase_offset=int(data["dl_info_fbase_offset"]),
        dl_info_sname_offset=int(data["dl_info_sname_offset"]),
        dl_info_saddr_offset=int(data["dl_info_saddr_offset"]),
        rtld_default_masked=int(masked, 0) if isinstance(masked, str) else int(masked),
    )
    _require(abi.platform.upper() == "IOS", "target ABI platform is not iOS")
    _require(abi.minos[:2] == (13, 0), f"target ABI minos is not iOS 13: {abi.minos}")
    offsets = (
        abi.dl_info_fname_offset,
        abi.dl_info_fbase_offset,
        abi.dl_info_sname_offset,
        abi.dl_info_saddr_offset,
    )
    _require(
        all(0 <= offset < abi.dl_info_size for offset in offsets),
        "target ABI has a Dl_info offset outside the struct",
    )
    _require(
        list(offsets) == sorted(offsets) and len(set(offsets)) == 4,
        "Dl_info offsets are not distinct and increasing",
    )
    _require(
        abi.rtld_default_masked == (1 << 64) - 2,
        f"unexpected RTLD_DEFAULT bit pattern: {abi.rtld_default_masked}",
    )
    return abi
