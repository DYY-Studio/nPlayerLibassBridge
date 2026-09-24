"""Semantic and artifact verification for the libass bridge prototype.

Every property is one named check. Checks never raise: they return a
verdict plus a detail string, so a report can list all failures at once
and each deliberate mutation maps to one error code.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import macho
from .manifest import Manifest, encode_bl
from .payload import Payload, PayloadLayout, assemble_payload
from .target_abi import TargetABI


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SOURCE = ROOT / "bridge" / "npa_ass_bridge.c"
BRIDGE_INSTALL_NAME = "@rpath/LibASSBridge.dylib"
FORBIDDEN_PATH_PREFIXES = ("/usr/local/", "/opt/homebrew/", "/Users/")
FORBIDDEN_DEPENDENCY_STEMS = (
    "libass",
    "libfreetype",
    "libfontconfig",
    "libexpat",
    "libharfbuzz",
    "libfribidi",
)
FORBIDDEN_CALLBACK_TOKENS = ("va_start", "va_end", "va_copy")
CALLBACK_ARGS = "void(int, const char *, va_list, void *)"
CALLBACK_PARAMETER = "(*message_cb)(int, const char *, va_list, void *)"


class VerificationError(RuntimeError):
    def __init__(self, codes: tuple[str, ...]) -> None:
        super().__init__("verification failed: " + ", ".join(codes))
        self.codes = codes


@dataclass(frozen=True)
class Check:
    code: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class VerificationReport:
    artifact: str
    mode: str
    checks: tuple[Check, ...]
    main_sha256: str = ""
    bridge_sha256: str = ""
    state_initial: int | None = None

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def require(self) -> None:
        if self.failed:
            raise VerificationError(tuple(check.code for check in self.failed))

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "mode": self.mode,
            "main_sha256": self.main_sha256,
            "bridge_sha256": self.bridge_sha256,
            "state_initial": self.state_initial,
            "checks": [
                {"code": check.code, "ok": check.ok, "detail": check.detail}
                for check in self.checks
            ],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


class _Checks:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def run(self, code: str, check: Callable[[], str]) -> None:
        try:
            self.checks.append(Check(code, True, check()))
        except Exception as error:  # noqa: BLE001 - every failure is a verdict
            self.fail(code, str(error) or type(error).__name__)

    def fail(self, code: str, detail: str) -> None:
        self.checks.append(Check(code, False, detail))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _host_paths(path: Path) -> str:
    data = path.read_bytes()
    for prefix in FORBIDDEN_PATH_PREFIXES:
        _require(prefix.encode() not in data, f"host path in artifact: {prefix}")
    return "no host paths"


def _bridge_target(binary: Any) -> str:
    _require(macho.enum_name(binary.header.cpu_type).lower() == "arm64", "bridge is not arm64")
    _require(macho.enum_name(binary.header.file_type) == "DYLIB", "bridge is not a dylib")
    _require(binary.has_build_version, "bridge has no LC_BUILD_VERSION")
    platform = macho.enum_name(binary.build_version.platform)
    minos = macho.version_tuple(binary.build_version.minos)
    _require(platform == "IOS" and minos[:2] == [13, 0], f"bridge target is {platform} {minos}")
    return f"arm64 DYLIB {platform} {minos[0]}.{minos[1]}"


def _bridge_exports(binary: Any, manifest: Manifest) -> str:
    exports = macho.exported_symbols(binary)
    expected = sorted(api.macho_name for api in manifest.apis)
    _require(exports == expected, f"exports differ: {exports}")
    return f"{len(exports)} underscore-prefixed exports"


def _bridge_install_name(path: Path) -> str:
    name = macho.install_name(path)
    _require(name == BRIDGE_INSTALL_NAME, f"install name is {name}")
    return name


def _bridge_dependencies(path: Path) -> str:
    dependencies = macho.dependency_lines(path)
    _require(dependencies.count(BRIDGE_INSTALL_NAME) == 1, "missing self install name")
    external = [item for item in dependencies if item != BRIDGE_INSTALL_NAME]
    _require(bool(external), "bridge has no dynamic dependency")
    for dependency in external:
        _require(dependency.startswith("/usr/lib/"), f"non-system dependency: {dependency}")
        stem = Path(dependency).name.split(".")[0]
        _require(stem not in FORBIDDEN_DEPENDENCY_STEMS, f"static dependency leaked: {dependency}")
    return " ".join(external)


def _bridge_initializers(binary: Any) -> str:
    init = macho.section_size(binary, "__mod_init_func")
    term = macho.section_size(binary, "__mod_term_func")
    _require(init == 0 and term == 0, f"initializer sections: init={init} term={term}")
    local = [
        str(symbol.name)
        for symbol in binary.symbols
        if str(symbol.name).lstrip("_").startswith("GLOBAL__sub_I_")
    ]
    _require(not local, f"C++ global initializers: {local}")
    atexit = [name for name in macho.imported_symbols(binary) if "__cxa_atexit" in name]
    _require(not atexit, f"__cxa_atexit import: {atexit}")
    return "no implicit initializers"


def _bridge_callback(manifest: Manifest) -> str:
    _require(manifest.callback.prototype == CALLBACK_ARGS, "manifest callback ABI changed")
    source = " ".join(BRIDGE_SOURCE.read_text(encoding="utf-8").split())
    _require(CALLBACK_PARAMETER in source, "bridge source lost the four-argument callback")
    for token in FORBIDDEN_CALLBACK_TOKENS:
        _require(token not in source, f"bridge source calls {token}")
    return CALLBACK_ARGS


def verify_bridge(path: Path, manifest: Manifest) -> VerificationReport:
    """Verify one LibASSBridge dylib against the frozen manifest."""

    path = Path(path).resolve()
    checks = _Checks()
    try:
        binary = macho.parse(path)
    except Exception as error:  # noqa: BLE001
        checks.fail("bridge.macho", str(error))
        return VerificationReport(str(path), "bridge", tuple(checks.checks))
    checks.run("bridge.target", lambda: _bridge_target(binary))
    checks.run("bridge.exports", lambda: _bridge_exports(binary, manifest))
    checks.run("bridge.install_name", lambda: _bridge_install_name(path))
    checks.run("bridge.dependencies", lambda: _bridge_dependencies(path))
    checks.run("bridge.initializers", lambda: _bridge_initializers(binary))
    checks.run("bridge.host_paths", lambda: _host_paths(path))
    checks.run("bridge.callback", lambda: _bridge_callback(manifest))
    return VerificationReport(
        str(path),
        "bridge",
        tuple(checks.checks),
        bridge_sha256=_sha256(path),
    )


def payload_layout(binary: Any) -> PayloadLayout:
    text = binary.get_segment(macho.SEGMENT_TEXT)
    data = binary.get_segment(macho.SEGMENT_DATA)
    _require(text is not None and data is not None, "payload segments are missing")
    return PayloadLayout(
        text_vmaddr=int(text.virtual_address),
        data_vmaddr=int(data.virtual_address),
        state_rva=0,
        slots_rva=8,
    )


def _payload_text(binary: Any, payload: Payload) -> str:
    reserved = bytes(binary.get_segment(macho.SEGMENT_TEXT).content)
    _require(
        reserved[: len(payload.text)] == payload.text,
        "payload text differs from the assembled dispatch",
    )
    _require(
        reserved[len(payload.text) :] == b"\x00" * (len(reserved) - len(payload.text)),
        "payload text reservation is not zero-filled",
    )
    return f"{len(payload.text)} bytes"


def _payload_data(binary: Any, payload: Payload) -> str:
    data = bytes(binary.get_segment(macho.SEGMENT_DATA).content)
    _require(data[: len(payload.data)] == payload.data, "payload data blob differs")
    return f"{len(payload.data)} bytes"


def _payload_relocations(binary: Any) -> str:
    """The payload segments must stay out of the binding tables."""

    ranges = []
    for name in (macho.SEGMENT_TEXT, macho.SEGMENT_DATA):
        segment = binary.get_segment(name)
        ranges.append((int(segment.virtual_address), int(segment.virtual_address) + int(segment.virtual_size)))
    for name, _, address in macho.snapshot(binary).bind_targets:
        for start, end in ranges:
            _require(not start <= address < end, f"binding inside the payload: {name}")
    return "no bind target in the payload segments"


def _payload_state(binary: Any) -> str:
    data = bytes(binary.get_segment(macho.SEGMENT_DATA).content)
    state = struct.unpack_from("<I", data, 0)[0]
    padding = struct.unpack_from("<I", data, 4)[0]
    _require(state == 0, f"state word is {state}")
    _require(padding == 0, "state padding is not zero")
    return "state=0"


def _payload_slots(binary: Any) -> str:
    data = bytes(binary.get_segment(macho.SEGMENT_DATA).content)
    slots = data[8:128]
    _require(len(slots) == 120, "slot table is not 120 bytes")
    _require(slots == b"\x00" * 120, "slot table is not fifteen null slots")
    return "15 null slots"


def _payload_checks(checks: _Checks, binary: Any, manifest: Manifest, abi: TargetABI) -> Payload | None:
    try:
        payload = assemble_payload(payload_layout(binary), manifest, abi)
    except Exception as error:  # noqa: BLE001
        checks.fail("main.payload", str(error))
        return None
    checks.run("payload.text", lambda: _payload_text(binary, payload))
    checks.run("payload.data", lambda: _payload_data(binary, payload))
    checks.run("payload.state", lambda: _payload_state(binary))
    checks.run("payload.slots", lambda: _payload_slots(binary))
    checks.run("payload.relocations", lambda: _payload_relocations(binary))
    return payload


def verify_payload(
    binary: Any,
    manifest: Manifest,
    target_abi: TargetABI | None = None,
) -> VerificationReport:
    """Verify the payload text, state word and slot table of one main."""

    abi = target_abi or manifest.target_abi
    checks = _Checks()
    _payload_checks(checks, binary, manifest, abi)
    return VerificationReport(str(macho.SEGMENT_TEXT), "payload", tuple(checks.checks))


def verify_main(
    baseline: Path,
    patched: Path,
    manifest: Manifest,
    target_abi: TargetABI | None = None,
) -> VerificationReport:
    """Verify a patched main against the frozen clean baseline."""

    baseline = Path(baseline).resolve()
    patched = Path(patched).resolve()
    abi = target_abi or manifest.target_abi
    checks = _Checks()
    try:
        before = macho.parse(baseline)
        after = macho.parse(patched)
    except Exception as error:  # noqa: BLE001
        checks.fail("main.macho", str(error))
        return VerificationReport(str(patched), "bridge", tuple(checks.checks))
    checks.run("main.baseline_hash", lambda: _baseline_hash(baseline, manifest))
    checks.run("main.entrypoint", lambda: _same_entrypoint(before, after))
    checks.run("main.bindings", lambda: _bindings(before, after))
    checks.run("main.segments", lambda: _segments(before, after))
    checks.run("main.sections", lambda: _sections(before, after, strict_text=False))
    checks.run("main.dylib_ordinals", lambda: _dylib_ordinals(before, after, manifest))
    checks.run(
        "main.instructions",
        lambda: _changed_sites(
            before,
            after,
            {site for api in manifest.apis for site in api.call_sites} | set(macho.NOP_SITES),
        ),
    )
    checks.run("main.call_sites", lambda: _changed_and_patched(patched, after, manifest, abi))
    checks.run("main.nops", lambda: _nops(patched))
    _payload_checks(checks, after, manifest, abi)
    state = struct.unpack_from(
        "<I", bytes(after.get_segment(macho.SEGMENT_DATA).content), 0
    )[0]
    return VerificationReport(
        str(patched),
        "bridge",
        tuple(checks.checks),
        main_sha256=_sha256(patched),
        state_initial=state,
    )


def _baseline_hash(baseline: Path, manifest: Manifest) -> str:
    actual = _sha256(baseline)
    _require(actual == manifest.main_sha256, f"baseline hash is {actual}")
    return actual


def _same_entrypoint(before: Any, after: Any) -> str:
    _require(int(before.entrypoint) == int(after.entrypoint), "entrypoint changed")
    return f"entrypoint {after.entrypoint:#x}"


def _segments(before: Any, after: Any) -> str:
    old = {segment.name: int(segment.virtual_address) for segment in before.segments}
    new = {segment.name: int(segment.virtual_address) for segment in after.segments}
    moved = [name for name, address in old.items() if new.get(name) != address]
    _require(moved in ([], [macho.LINKEDIT]), f"segments moved: {moved}")
    _require(after.segments[-1].name == macho.LINKEDIT, "__LINKEDIT is not the final segment")
    text = after.get_segment(macho.SEGMENT_TEXT)
    data = after.get_segment(macho.SEGMENT_DATA)
    _require(text is not None and data is not None, "payload segments are missing")
    _require(int(text.init_protection) == macho.PROTECTION_RX, "text segment is not r-x")
    _require(int(data.init_protection) == macho.PROTECTION_RW, "data segment is not rw-")
    _require(
        int(text.file_size) % macho.PAGE == 0 and int(data.file_size) % macho.PAGE == 0,
        "payload segments are not page sized",
    )
    return f"payload segments at {text.virtual_address:#x}/{data.virtual_address:#x}"


def _sections(before: Any, after: Any, strict_text: bool = True) -> str:
    _require(
        macho.snapshot(before).section_vas == macho.snapshot(after).section_vas,
        "an existing section moved",
    )
    old_bytes = macho.section_bytes(before)
    new_bytes = macho.section_bytes(after)
    for name, content in old_bytes.items():
        if name == "__TEXT,__text" and not strict_text:
            continue
        _require(new_bytes.get(name) == content, f"section {name} changed")
    return f"{len(old_bytes)} sections stable"


def _changed_words(before: Any, after: Any, section: str) -> set[int]:
    old = macho.section_bytes(before)[section]
    new = macho.section_bytes(after)[section]
    _require(len(old) == len(new), f"{section} changed shape")
    base = macho.snapshot(before).section_vas[section]
    return {
        base + index
        for index in range(0, len(old) - 3, 4)
        if old[index : index + 4] != new[index : index + 4]
    }


def _changed_sites(before: Any, after: Any, expected: set[int]) -> str:
    changed = _changed_words(before, after, "__TEXT,__text")
    _require(
        changed == expected,
        f"instruction changes {[hex(item) for item in sorted(changed)]} "
        f"differ from the allowed sites",
    )
    return f"{len(changed)} instruction sites changed"


def _dylib_ordinals(before: Any, after: Any, manifest: Manifest) -> str:
    old = macho.snapshot(before).dylib_ordinals
    new = macho.snapshot(after).dylib_ordinals
    _require(new[:-1] == old, "existing dylib ordinals changed")
    _require(new[-1][0] == manifest.bridge_path, "bridge is not the final dependency")
    return f"{len(new)} dylib commands"


def _bindings(before: Any, after: Any) -> str:
    old = macho.snapshot(before)
    new = macho.snapshot(after)
    _require(old.bind_targets == new.bind_targets, "standard bindings changed")
    _require(old.lazy_targets == new.lazy_targets, "lazy bindings changed")
    _require(old.export_symbols == new.export_symbols, "export trie changed")
    return f"{len(old.bind_targets)} bindings stable"


def _changed_and_patched(
    patched: Path, binary: Any, manifest: Manifest, abi: TargetABI
) -> str:
    payload = assemble_payload(payload_layout(binary), manifest, abi)
    raw = patched.read_bytes()
    count = 0
    for api in manifest.apis:
        for site in api.call_sites:
            offset = int(binary.virtual_address_to_offset(site))
            veneer = payload.symbols[f"veneer_{api.symbol}"]
            expected = struct.pack("<I", encode_bl(site, veneer))
            _require(
                raw[offset : offset + 4] == expected,
                f"call site {site:#x} is not redirected",
            )
            count += 1
    return f"{count} redirects"


def _nops(patched: Path) -> str:
    binary = macho.parse(patched)
    raw = patched.read_bytes()
    for site in macho.NOP_SITES:
        offset = int(binary.virtual_address_to_offset(site))
        _require(
            raw[offset : offset + 4] == struct.pack("<I", macho.NOP_WORD),
            f"NOP site {site:#x} is not patched",
        )
    return f"{len(macho.NOP_SITES)} NOPs"


def verify_artifact(
    baseline: Path,
    patched: Path,
    manifest: Manifest,
    bridge: Path,
    target_abi: TargetABI | None = None,
) -> VerificationReport:
    """Verify one packaged main plus its bridge dylib."""

    abi = target_abi or manifest.target_abi
    main = verify_main(baseline, patched, manifest, abi)
    bridge_report = verify_bridge(bridge, manifest)
    return VerificationReport(
        artifact=main.artifact,
        mode="bridge",
        checks=main.checks + bridge_report.checks,
        main_sha256=main.main_sha256,
        bridge_sha256=bridge_report.bridge_sha256,
        state_initial=main.state_initial,
    )
