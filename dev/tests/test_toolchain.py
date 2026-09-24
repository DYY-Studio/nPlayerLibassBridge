from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dev import abi_probe
from dev.abi_probe import load_target_abi
from npabridge.toolchain import Toolchain


EXPECTED_RTLD_DEFAULT = (1 << 64) - 2


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    try:
        return Toolchain()
    except FileNotFoundError as error:
        pytest.skip(f"Keystone library is not built: {error}")


@pytest.fixture(scope="module")
def ios_sdk() -> Path:
    value = os.environ.get("NPA_IOS_SDK")
    if value is None:
        try:
            value = subprocess.check_output(
                ["xcrun", "--sdk", "iphoneos", "--show-sdk-path"],
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            pytest.skip(f"iOS SDK is not available: {error}")
    path = Path(value)
    if not path.is_dir():
        pytest.skip(f"iOS SDK is not available: {path}")
    return path


def test_keystone_assembles_arm64_branch(toolchain: Toolchain) -> None:
    code = toolchain.assemble("b #0x101000000", 0x100FFF000)
    assert isinstance(code, bytes)
    assert code[:4] == b"\x00\x04\x00\x14"


def test_target_abi_comes_from_ios_sdk(ios_sdk: Path) -> None:
    abi = load_target_abi(ios_sdk)
    assert abi.dl_info_size == 32
    assert abi.dl_info_fname_offset == 0
    assert abi.dl_info_fbase_offset == 8
    assert abi.dl_info_sname_offset == 16
    assert abi.dl_info_saddr_offset == 24
    assert abi.platform.upper() == "IOS"
    assert abi.minos[:2] == (13, 0)
    assert abi.rtld_default_masked == EXPECTED_RTLD_DEFAULT


def test_target_abi_reads_constants_from_object(
    tmp_path: Path, ios_sdk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_object = abi_probe._compile_probe(ios_sdk)
    mutated_object = tmp_path / "target_abi_probe.o"
    shutil.copy2(source_object, mutated_object)
    import lief

    binary = lief.MachO.parse(str(mutated_object))[0]
    section = next(item for item in binary.sections if item.name == "__const")
    symbols = {item.name: item for item in binary.symbols}
    size_offset = section.offset + symbols["_npa_dl_info_size"].value
    pointer_offset = section.offset + symbols["_npa_rtld_default_bits"].value
    data = bytearray(mutated_object.read_bytes())
    data[size_offset : size_offset + 4] = (40).to_bytes(4, "little")
    data[pointer_offset : pointer_offset + 8] = (0x1122334455667788).to_bytes(
        8, "little"
    )
    mutated_object.write_bytes(data)
    monkeypatch.setattr(
        "dev.abi_probe._compile_probe", lambda _sdk: mutated_object
    )
    abi = load_target_abi(ios_sdk)
    assert abi.dl_info_size == 40
    assert abi.rtld_default_masked == 0x1122334455667788
