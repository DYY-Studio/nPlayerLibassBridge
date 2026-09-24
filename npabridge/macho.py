"""Two-phase Mach-O rewrite that installs the atomic libass dispatch.

Phase A lets LIEF rebuild the load commands and segment layout once, adds
the weak bridge dependency and two placeholder segments, and freezes the
final virtual addresses. Phase B never rebuilds the file: it reparses the
frozen layout and writes the assembled payload plus the sixteen BL and
two NOP patches at their final addresses.
"""

from __future__ import annotations

import hashlib
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import lief

from .manifest import Manifest, encode_bl
from .payload import PayloadLayout, assemble_payload, measure_payload
from .target_abi import TargetABI, load_target_abi


TARGET = "arm64-apple-ios13.0"
SEGMENT_TEXT = "__NPATCH_TEXT"
SEGMENT_DATA = "__NPATCH_DATA"
LINKEDIT = "__LINKEDIT"
PAGE = 0x4000
PROTECTION_RX = 5
PROTECTION_RW = 3
DATA_BLOB_SIZE = 128
IPA_MEMBER = "Payload/nPlayer.app/nPlayer"
NOP_WORD = 0xD503201F
NOP_SITES = {0x100A0392C: 0x35000148, 0x100ACBC14: 0x37000080}
STUB_THUNKS = {
    0x1011362CC: bytes.fromhex("302f00f0107640f900021fd6"),
    0x10113629C: bytes.fromhex("302f00f0106640f900021fd6"),
}


@dataclass(frozen=True)
class SemanticSnapshot:
    segment_vas: dict[str, int]
    section_vas: dict[str, int]
    entrypoint: int
    dylib_ordinals: tuple[tuple[str, int], ...]
    bind_targets: tuple[tuple[str, str, int], ...]
    lazy_targets: tuple[tuple[str, str, int], ...]
    export_symbols: tuple[tuple[str, int], ...]


class ParsedMachO:
    """A parsed Mach-O that keeps its LIEF container alive.

    LIEF binaries are views into the parse result; letting the container be
    collected leaves dangling references that crash on the next access.
    """

    __slots__ = ("_container", "lief")

    def __init__(self, container: Any, binary: Any) -> None:
        self._container = container
        self.lief = binary

    def __getattr__(self, name: str) -> Any:
        return getattr(self.lief, name)


def parse(path: Path) -> ParsedMachO:
    """Parse one Mach-O file, keeping the LIEF container alive."""

    container = lief.MachO.parse(str(path))
    binaries = list(container) if container is not None else []
    if len(binaries) != 1:
        raise ValueError(f"expected one Mach-O binary in {path}")
    return ParsedMachO(container, binaries[0])


def extract_clean_main(input_path: Path, destination: Path) -> Path:
    """Resolve the patch input, extracting the frozen IPA member if needed."""

    if input_path.suffix != ".ipa":
        return input_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(input_path) as archive:
        destination.write_bytes(archive.read(IPA_MEMBER))
    return destination


def _binding_targets(binary: Any, binding_class: str) -> tuple[tuple[str, str, int], ...]:
    seen = set()
    for item in binary.bindings:
        if str(item.binding_class).rsplit(".", 1)[-1] != binding_class:
            continue
        library = str(item.library.name) if item.has_library else ""
        seen.add((str(item.symbol), library, int(item.address)))
    return tuple(sorted(seen))


def snapshot(binary: Any) -> SemanticSnapshot:
    return SemanticSnapshot(
        segment_vas={segment.name: int(segment.virtual_address) for segment in binary.segments},
        section_vas={
            f"{segment.name},{section.name}": int(section.virtual_address)
            for segment in binary.segments
            for section in segment.sections
        },
        entrypoint=int(binary.entrypoint),
        dylib_ordinals=tuple(
            (str(library.name), index + 1) for index, library in enumerate(binary.libraries)
        ),
        bind_targets=_binding_targets(binary, "STANDARD"),
        lazy_targets=_binding_targets(binary, "LAZY"),
        export_symbols=tuple(
            sorted(
                (str(function.name), int(function.address))
                for function in binary.exported_functions
            )
        ),
    )


def section_bytes(binary: Any) -> dict[str, bytes]:
    return {
        f"{segment.name},{section.name}": bytes(section.content)
        for segment in binary.segments
        for section in segment.sections
    }


def exported_symbols(binary: Any) -> list[str]:
    return sorted({str(symbol.name) for symbol in binary.exported_symbols})


def imported_symbols(binary: Any) -> list[str]:
    return sorted({str(symbol.name) for symbol in binary.imported_symbols})


def section_size(binary: Any, name: str) -> int:
    return sum(int(section.size) for section in binary.sections if str(section.name) == name)


def enum_name(value: object) -> str:
    return str(value).rsplit(".", 1)[-1].upper()


def version_tuple(value: object) -> list[int]:
    parts = tuple(int(part) for part in value)
    if len(parts) > 3:
        raise ValueError(f"invalid Mach-O version: {parts}")
    return list(parts + (0,) * (3 - len(parts)))


def _otool(flag: str, path: Path) -> list[str]:
    result = subprocess.run(
        ["/usr/bin/xcrun", "otool", flag, str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def xcrun_find(name: str) -> str:
    result = subprocess.run(
        ["/usr/bin/xcrun", "--sdk", "iphoneos", "--find", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    path = result.stdout.strip()
    if not path:
        raise RuntimeError(f"xcrun could not find {name}")
    return path


def dependency_lines(path: Path) -> list[str]:
    lines = _otool("-L", path)
    if not lines or not lines[0].endswith(":"):
        raise ValueError(f"otool dependency output has no Mach-O header: {path}")
    return [line.split()[0] for line in lines[1:]]


def install_name(path: Path) -> str:
    lines = _otool("-D", path)
    if len(lines) != 2 or not lines[0].endswith(":"):
        raise ValueError(f"otool install-name output is malformed: {path}")
    return lines[1]


def _round_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _word(binary: Any, address: int) -> int:
    content = bytes(binary.get_content_from_virtual_address(address, 4))
    if len(content) != 4:
        raise ValueError(f"unable to read four bytes at {address:#x}")
    return struct.unpack("<I", content)[0]


def provisional_layout(binary: Any) -> PayloadLayout:
    """Addresses LIEF is expected to use for the two placeholder segments."""

    body = [segment for segment in binary.segments if segment.name != LINKEDIT]
    last = body[-1]
    text_vmaddr = (int(last.virtual_address) + int(last.virtual_size) + PAGE - 1) & ~(PAGE - 1)
    data_vmaddr = text_vmaddr + PAGE
    return PayloadLayout(
        text_vmaddr=text_vmaddr,
        data_vmaddr=data_vmaddr,
        state_rva=0,
        slots_rva=8,
    )


def preflight(input_path: Path, manifest: Manifest, digest: str | None = None) -> None:
    """Reject anything that is not the frozen clean main."""

    data = input_path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != (digest or manifest.main_sha256):
        raise ValueError(f"input SHA-256 does not match the frozen baseline: {actual}")
    binary = parse(input_path)
    if not binary.has_encryption_info or int(binary.encryption_info.crypt_id) != 0:
        raise ValueError("input is encrypted")
    if not binary.has_code_signature:
        raise ValueError("input has no LC_CODE_SIGNATURE to replace")
    if binary.segments[-1].name != LINKEDIT:
        raise ValueError("__LINKEDIT is not the final segment")
    for name in (SEGMENT_TEXT, SEGMENT_DATA):
        if binary.has_segment(name):
            raise ValueError(f"input already carries {name}")
    for library in binary.libraries:
        if str(library.name) == manifest.bridge_path:
            raise ValueError("input already loads the bridge")
    if int(binary.available_command_space) < 128:
        raise ValueError("no load-command space for the weak dependency")
    for site, expected in NOP_SITES.items():
        if _word(binary, site) != expected:
            raise ValueError(f"unexpected instruction at the NOP site {site:#x}")
    for stub, thunk in STUB_THUNKS.items():
        content = bytes(binary.get_content_from_virtual_address(stub, len(thunk)))
        if content != thunk:
            raise ValueError(f"dynamic stub at {stub:#x} is not the frozen thunk")
    for api in manifest.apis:
        for site in api.call_sites:
            expected = encode_bl(site, api.old_target)
            actual = _word(binary, site)
            if actual != expected:
                raise ValueError(
                    f"call site {site:#x} does not call {api.old_target:#x}: "
                    f"{actual:#010x} != {expected:#010x}"
                )


def _segment(name: str, vmaddr: int, offset: int, content: bytes, protection: int) -> Any:
    segment = lief.MachO.SegmentCommand(name)
    segment.virtual_address = vmaddr
    segment.file_offset = offset
    segment.content = list(content)
    segment.init_protection = protection
    segment.max_protection = protection
    return segment


def phase_a(
    input_path: Path,
    output_path: Path,
    manifest: Manifest,
    reserved_text: int | None = None,
    target_abi: TargetABI | None = None,
) -> dict[str, Any]:
    """Rebuild the load commands and freeze the payload segment layout."""

    abi = target_abi or load_target_abi(sdk_path())
    preflight(input_path, manifest)
    binary = parse(input_path)
    provisional = provisional_layout(binary)
    measured = measure_payload(provisional, manifest, abi)
    if reserved_text is not None and reserved_text != measured:
        raise ValueError(
            f"reserved payload size {reserved_text} does not match measured {measured}"
        )
    reserved_text = measured
    if binary.has_code_signature:
        binary.remove_signature()
    binary.add(lief.MachO.DylibCommand.weak_lib(manifest.bridge_path))
    text_offset = _round_up(
        int(binary.segments[-1].file_offset) + int(binary.segments[-1].file_size), PAGE
    )
    binary.add(
        _segment(
            SEGMENT_TEXT,
            provisional.text_vmaddr,
            text_offset,
            b"\x00" * reserved_text,
            PROTECTION_RX,
        )
    )
    binary.add(
        _segment(
            SEGMENT_DATA,
            provisional.data_vmaddr,
            text_offset + _round_up(reserved_text, PAGE),
            b"\x00" * DATA_BLOB_SIZE,
            PROTECTION_RW,
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    binary.write(str(output_path))
    del binary
    return verify_phase_a(input_path, output_path, manifest, reserved_text, abi)


def verify_phase_a(
    baseline_path: Path,
    layout_path: Path,
    manifest: Manifest,
    reserved_text: int,
    target_abi: TargetABI | None = None,
) -> dict[str, Any]:
    abi = target_abi or load_target_abi(sdk_path())
    before = parse(baseline_path)
    after = parse(layout_path)
    old, new = snapshot(before), snapshot(after)
    if before.header.cpu_type != lief.MachO.Header.CPU_TYPE.ARM64:
        raise ValueError("baseline is not arm64")
    if old.entrypoint != new.entrypoint:
        raise ValueError("entrypoint changed")
    if old.section_vas != new.section_vas:
        raise ValueError("an existing section moved")
    moved = [
        name
        for name, address in old.segment_vas.items()
        if new.segment_vas.get(name) != address
    ]
    if moved != [LINKEDIT]:
        raise ValueError(f"unexpected segment address changes: {moved}")
    if old.dylib_ordinals != new.dylib_ordinals[:-1]:
        raise ValueError("existing dylib ordinals changed")
    if new.dylib_ordinals[-1][0] != manifest.bridge_path:
        raise ValueError("bridge is not the final dylib dependency")
    for name, expected in (
        ("bind_targets", old.bind_targets),
        ("lazy_targets", old.lazy_targets),
        ("export_symbols", old.export_symbols),
    ):
        if getattr(new, name) != expected:
            raise ValueError(f"{name} changed during the rebuild")
    if after.segments[-1].name != LINKEDIT:
        raise ValueError("__LINKEDIT is not the final segment")
    if section_bytes(before) != section_bytes(after):
        raise ValueError("existing section content changed during the rebuild")
    text = after.get_segment(SEGMENT_TEXT)
    data = after.get_segment(SEGMENT_DATA)
    if text is None or data is None:
        raise ValueError("placeholder segments are missing")
    if int(text.init_protection) != PROTECTION_RX or int(data.init_protection) != PROTECTION_RW:
        raise ValueError("placeholder segment protections are wrong")
    reserved_page = _round_up(reserved_text, PAGE)
    if int(text.file_size) != reserved_page:
        raise ValueError(
            f"text reservation is {int(text.file_size)} instead of {reserved_page}"
        )
    if int(data.file_size) != _round_up(DATA_BLOB_SIZE, PAGE):
        raise ValueError(f"data reservation is {int(data.file_size)}")
    if bytes(text.content) != b"\x00" * reserved_page:
        raise ValueError("text reservation is not zero-filled")
    if bytes(data.content) != b"\x00" * int(data.file_size):
        raise ValueError("data reservation is not zero-filled")
    if not int(text.virtual_address) + reserved_text <= int(data.virtual_address):
        raise ValueError("payload text overlaps the data segment")
    layout = PayloadLayout(
        text_vmaddr=int(text.virtual_address),
        data_vmaddr=int(data.virtual_address),
        state_rva=0,
        slots_rva=8,
    )
    if measure_payload(layout, manifest, abi) != reserved_text:
        raise ValueError("payload size depends on the final addresses")
    return {
        "baseline": str(baseline_path),
        "layout": str(layout_path),
        "text_vmaddr": layout.text_vmaddr,
        "data_vmaddr": layout.data_vmaddr,
        "reserved_text": reserved_text,
        "text_file_offset": int(text.file_offset),
        "data_file_offset": int(data.file_offset),
        "linkedit_vmaddr": new.segment_vas[LINKEDIT],
        "dylib_ordinals": [list(item) for item in new.dylib_ordinals],
    }


def write_equal_length(buffer: bytearray, offset: int, payload: bytes) -> None:
    end = offset + len(payload)
    if offset < 0 or end > len(buffer):
        raise ValueError(f"write at {offset:#x} of {len(payload)} bytes leaves the file")
    buffer[offset:end] = payload


def patch_bl(site: int, target: int) -> bytes:
    return struct.pack("<I", encode_bl(site, target))


def phase_b(
    layout_path: Path,
    output_path: Path,
    manifest: Manifest,
    target_abi: TargetABI | None = None,
) -> dict[str, Any]:
    """Write the payload and the branch patches into the frozen layout."""

    abi = target_abi or load_target_abi(sdk_path())
    binary = parse(layout_path)
    text = binary.get_segment(SEGMENT_TEXT)
    data = binary.get_segment(SEGMENT_DATA)
    if text is None or data is None:
        raise ValueError("layout has no placeholder segments")
    layout = PayloadLayout(
        text_vmaddr=int(text.virtual_address),
        data_vmaddr=int(data.virtual_address),
        state_rva=0,
        slots_rva=8,
    )
    payload = assemble_payload(layout, manifest, abi)
    if len(payload.text) > int(text.file_size):
        raise ValueError(
            f"assembled payload is {len(payload.text)} bytes, "
            f"segment holds {int(text.file_size)}"
        )
    if int(text.file_offset) + len(payload.text) > int(data.file_offset):
        raise ValueError("assembled payload spills into the data segment")
    if len(payload.data) > int(data.file_size):
        raise ValueError("assembled data blob does not fit its segment")

    buffer = bytearray(layout_path.read_bytes())
    before = section_bytes(binary)
    write_equal_length(buffer, int(text.file_offset), payload.text)
    write_equal_length(buffer, int(data.file_offset), payload.data)
    for api in manifest.apis:
        for site in api.call_sites:
            offset = int(binary.virtual_address_to_offset(site))
            original = encode_bl(site, api.old_target)
            actual = struct.unpack_from("<I", buffer, offset)[0]
            if actual != original:
                raise ValueError(f"call site {site:#x} is no longer the frozen BL")
            target = payload.symbols[f"veneer_{api.symbol}"]
            write_equal_length(buffer, offset, patch_bl(site, target))
    for site, expected in NOP_SITES.items():
        offset = int(binary.virtual_address_to_offset(site))
        if struct.unpack_from("<I", buffer, offset)[0] != expected:
            raise ValueError(f"NOP site {site:#x} no longer holds the original guard")
        write_equal_length(buffer, offset, struct.pack("<I", NOP_WORD))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(buffer))
    del binary
    return verify_phase_b(layout_path, output_path, manifest, payload)


def verify_phase_b(
    layout_path: Path,
    output_path: Path,
    manifest: Manifest,
    payload: Any | None = None,
) -> dict[str, Any]:
    before = parse(layout_path)
    after = parse(output_path)
    old, new = snapshot(before), snapshot(after)
    for field in ("segment_vas", "section_vas", "entrypoint", "dylib_ordinals"):
        if getattr(old, field) != getattr(new, field):
            raise ValueError(f"phase B changed {field}")
    if old.bind_targets != new.bind_targets or old.lazy_targets != new.lazy_targets:
        raise ValueError("phase B changed the binding tables")
    if old.export_symbols != new.export_symbols:
        raise ValueError("phase B changed the export trie")
    changes = _changed_words(before, after)
    allowed = {
        site for api in manifest.apis for site in api.call_sites
    } | set(NOP_SITES)
    unexpected = sorted(changes - allowed)
    if unexpected:
        raise ValueError(f"unexpected instruction changes: {[hex(item) for item in unexpected]}")
    missing = sorted(allowed - changes)
    if missing:
        raise ValueError(f"missing patches: {[hex(item) for item in missing]}")
    if len(changes) != len(allowed):
        raise ValueError(f"changed {len(changes)} instructions, expected {len(allowed)}")
    if layout_path.stat().st_size != output_path.stat().st_size:
        raise ValueError("phase B changed the file size")
    if payload is not None:
        text = before.get_segment(SEGMENT_TEXT)
        data = before.get_segment(SEGMENT_DATA)
        raw = output_path.read_bytes()
        written = raw[
            int(text.file_offset) : int(text.file_offset) + len(payload.text)
        ]
        if written != payload.text:
            raise ValueError("payload text was not written at the reserved offset")
        blob = raw[int(data.file_offset) : int(data.file_offset) + len(payload.data)]
        if blob != payload.data:
            raise ValueError("payload data was not written at the reserved offset")
        for api in manifest.apis:
            for site in api.call_sites:
                offset = int(after.virtual_address_to_offset(site))
                veneer = payload.symbols[f"veneer_{api.symbol}"]
                expected = patch_bl(site, veneer)
                if raw[offset : offset + 4] != expected:
                    raise ValueError(f"call site {site:#x} was not redirected")
        for site in NOP_SITES:
            offset = int(after.virtual_address_to_offset(site))
            if raw[offset : offset + 4] != struct.pack("<I", NOP_WORD):
                raise ValueError(f"NOP site {site:#x} was not patched")
    report: dict[str, Any] = {
        "layout": str(layout_path),
        "output": str(output_path),
        "patched_call_sites": len(allowed) - len(NOP_SITES),
        "nop_sites": sorted(NOP_SITES),
        "text_vmaddr": old.segment_vas[SEGMENT_TEXT],
        "data_vmaddr": old.segment_vas[SEGMENT_DATA],
    }
    if payload is not None:
        report["payload_text_size"] = len(payload.text)
        report["payload_symbols"] = len(payload.symbols)
    return report


def _changed_words(before: Any, after: Any) -> set[int]:
    """Return the virtual addresses of every changed word outside the payload."""

    old_sections = section_bytes(before)
    new_sections = section_bytes(after)
    changed: set[int] = set()
    for name, old in old_sections.items():
        segment_name, _, section_name = name.partition(",")
        if segment_name in (SEGMENT_TEXT, SEGMENT_DATA):
            continue
        new = new_sections.get(name)
        if new is None or len(new) != len(old):
            raise ValueError(f"section {name} changed shape")
        if new == old:
            continue
        base = int(before.get_section(section_name).virtual_address)
        for index in range(0, len(old) - 3, 4):
            if old[index : index + 4] != new[index : index + 4]:
                changed.add(base + index)
    return changed


def sdk_path() -> Path:
    result = subprocess.run(
        ["/usr/bin/xcrun", "--sdk", "iphoneos", "--show-sdk-path"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return Path(result.stdout.strip())
