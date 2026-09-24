"""Generate the fixed-address arm64 dispatch payload.

The payload is assembled at a caller-supplied final virtual address.  It uses
only PC-relative control flow and PC-relative data/string addresses; the data
blob is deliberately limited to the state word and fifteen null slots.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import struct
from typing import Iterable

from .manifest import Manifest, encode_bl
from .target_abi import TargetABI
from .toolchain import Toolchain


STATE_UNINITIALIZED = 0
STATE_INITIALIZING = 1
STATE_NEW = 2
STATE_OLD = 3

_API_COUNT = 15
_CALLSITE_COUNT = 16
_LOGICAL_DATA_SIZE = 128
_SLOTS_OFFSET = 8
_SLOT_SIZE = 8
_LR_OFFSET = 64
_CONTINUATION_OFFSET = 72
_FBASE_OFFSET = 80
_CANDIDATE_OFFSET = 88
_INFO_OFFSET = 96
_MIN_FRAME_SIZE = 128
_BRANCH_LIMIT = 1 << 27
_ADR_LIMIT = 1 << 20
_ADRP_LIMIT = 1 << 31


@dataclass(frozen=True)
class PayloadLayout:
    """Final virtual addresses and data-relative offsets for the payload."""

    text_vmaddr: int
    data_vmaddr: int
    state_rva: int
    slots_rva: int


@dataclass(frozen=True)
class Payload:
    text: bytes
    data: bytes
    symbols: dict[str, int]
    stubs: dict[str, int]


@dataclass(frozen=True)
class _Block:
    name: str
    lines: tuple[str, ...]


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _hex(value: int) -> str:
    return f"0x{value:x}"


def _require_uint(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 1 << 64:
        raise ValueError(f"{name} must be a uint64")
    return value


def _require_aligned(value: int, alignment: int, name: str) -> None:
    if value & (alignment - 1):
        raise ValueError(f"{name} must be {alignment}-byte aligned")


def _check_branch(source: int, target: int, name: str) -> None:
    _require_aligned(source, 4, f"{name} source")
    _require_aligned(target, 4, f"{name} target")
    displacement = target - source
    if not -_BRANCH_LIMIT <= displacement < _BRANCH_LIMIT:
        raise ValueError(
            f"{name} is outside the arm64 +/-128 MiB branch range: "
            f"{source:#x} -> {target:#x}"
        )


def _check_adr(source: int, target: int, name: str) -> None:
    displacement = target - source
    if not -_ADR_LIMIT <= displacement < _ADR_LIMIT:
        raise ValueError(f"{name} is outside the arm64 ADR range")


def _check_adrp(source: int, target_page: int, name: str) -> None:
    source_page = source & ~0xFFF
    displacement = target_page - source_page
    if not -_ADRP_LIMIT <= displacement < _ADRP_LIMIT:
        raise ValueError(f"{name} is outside the arm64 ADRP range")


def _validate_layout(layout: PayloadLayout) -> tuple[int, int, int]:
    if not isinstance(layout, PayloadLayout):
        raise TypeError("layout must be a PayloadLayout")
    text_vmaddr = _require_uint(layout.text_vmaddr, "text_vmaddr")
    data_vmaddr = _require_uint(layout.data_vmaddr, "data_vmaddr")
    state_rva = _require_uint(layout.state_rva, "state_rva")
    slots_rva = _require_uint(layout.slots_rva, "slots_rva")
    _require_aligned(text_vmaddr, 4, "text_vmaddr")
    _require_aligned(data_vmaddr, 16, "data_vmaddr")
    if state_rva != 0 or slots_rva != _SLOTS_OFFSET:
        raise ValueError("payload data layout requires state_rva=0 and slots_rva=8")
    if data_vmaddr + _LOGICAL_DATA_SIZE > 1 << 64:
        raise ValueError("payload data range overflows uint64")
    return text_vmaddr, data_vmaddr, state_rva


def _validate_manifest(manifest: Manifest) -> tuple[tuple, ...]:
    if not isinstance(manifest, Manifest):
        raise TypeError("manifest must be a Manifest")
    apis = tuple(manifest.apis)
    if len(apis) != _API_COUNT:
        raise ValueError(f"payload requires exactly {_API_COUNT} APIs")
    symbols = [api.symbol for api in apis]
    if len(set(symbols)) != _API_COUNT:
        raise ValueError("payload API symbols must be unique")
    for symbol in symbols:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
            raise ValueError(f"invalid API symbol: {symbol!r}")
    callsites = tuple(
        site for api in apis for site in api.call_sites
    )
    if len(callsites) != _CALLSITE_COUNT or len(set(callsites)) != _CALLSITE_COUNT:
        raise ValueError("payload requires sixteen unique call sites")
    process_data = next(
        (api for api in apis if api.symbol == "npa_ass_process_data"),
        None,
    )
    if process_data is None or len(process_data.call_sites) != 2:
        raise ValueError("process_data must retain its two shared callsites")
    for site in callsites:
        _require_aligned(_require_uint(site, "call site"), 4, "call site")
    for api in apis:
        _require_aligned(
            _require_uint(api.old_target, f"old target for {api.symbol}"),
            4,
            f"old target for {api.symbol}",
        )
    _require_aligned(
        _require_uint(manifest.dlsym_stub, "dlsym_stub"),
        4,
        "dlsym_stub",
    )
    _require_aligned(
        _require_uint(manifest.dladdr_stub, "dladdr_stub"),
        4,
        "dladdr_stub",
    )
    if manifest.expected_bridge_basename != "LibASSBridge.dylib":
        raise ValueError("payload requires the LibASSBridge.dylib basename")
    if any(ord(character) > 0x7F for character in manifest.expected_bridge_basename):
        raise ValueError("bridge basename must be ASCII")
    return apis


def _validate_abi(target_abi: TargetABI) -> tuple[int, int, int, int]:
    if not isinstance(target_abi, TargetABI):
        raise TypeError("target_abi must be a TargetABI")
    if target_abi.platform.upper() != "IOS":
        raise ValueError("payload target ABI must be iOS")
    if target_abi.rtld_default_masked != (1 << 64) - 2:
        raise ValueError("payload requires the iOS RTLD_DEFAULT value")
    size = _require_uint(target_abi.dl_info_size, "dl_info_size")
    if size < 32:
        raise ValueError("payload requires a complete 64-bit Dl_info")
    offsets = (
        _require_uint(target_abi.dl_info_fname_offset, "dl_info_fname_offset"),
        _require_uint(target_abi.dl_info_fbase_offset, "dl_info_fbase_offset"),
    )
    if any(offset + 8 > size for offset in offsets):
        raise ValueError("Dl_info offsets are outside the target ABI")
    frame_size = _align(_INFO_OFFSET + size, 16)
    if frame_size < _MIN_FRAME_SIZE:
        frame_size = _MIN_FRAME_SIZE
    return size, offsets[0], offsets[1], frame_size


def _data_address(data_vmaddr: int) -> tuple[int, int]:
    page = data_vmaddr & ~0xFFF
    return page, data_vmaddr - page


def _state_lines(register: str, data_page: int, data_lo: int) -> list[str]:
    return [
        f"  adrp {register}, {_hex(data_page)}",
        f"  add {register}, {register}, #{data_lo}",
    ]


def _address_lines(register: str, address: int) -> list[str]:
    page = address & ~0xFFF
    return [
        f"  adrp {register}, {_hex(page)}",
        f"  add {register}, {register}, #{address - page}",
    ]


def _target(addresses: dict[str, int], name: str) -> str:
    return _hex(addresses[name])


def _branch(opcode: str, addresses: dict[str, int], name: str) -> str:
    return f"  {opcode} #{_target(addresses, name)}"


def _branch_register(opcode: str, register: str, addresses: dict[str, int], name: str) -> str:
    return f"  {opcode} {register}, #{_target(addresses, name)}"


def _direct_branch(opcode: str, target: int) -> str:
    return f"  {opcode} #{_hex(target)}"


def _stub_branch(opcode: str, target: int) -> str:
    return _direct_branch(opcode, target)


def _slot_offset(index: int) -> int:
    return _SLOTS_OFFSET + index * _SLOT_SIZE


def _veneer_blocks(
    api: object,
    index: int,
    addresses: dict[str, int],
    data_page: int,
    data_lo: int,
    frame_size: int,
    text_vmaddr: int,
    placeholder: bool,
) -> list[_Block]:
    symbol = api.symbol
    dispatch = f"dispatch_{symbol}"
    enter = f"enter_{symbol}"
    new = f"new_{symbol}"
    dispatch_address = addresses[dispatch]
    old_target = text_vmaddr if placeholder else api.old_target
    return [
        _Block(
            dispatch,
            (
                f"{dispatch}:",
                *_state_lines("x8", data_page, data_lo),
                "  ldar w16, [x8]",
                f"  cmp w16, #{STATE_UNINITIALIZED}",
                _branch("b.eq", addresses, enter),
                f"  cmp w16, #{STATE_INITIALIZING}",
                _branch("b.eq", addresses, enter),
                f"  cmp w16, #{STATE_NEW}",
                _branch("b.eq", addresses, new),
                _direct_branch("b", old_target),
            ),
        ),
        _Block(
            enter,
            (
                f"{enter}:",
                f"  sub sp, sp, #{frame_size}",
                "  stp x0, x1, [sp, #0]",
                "  stp x2, x3, [sp, #16]",
                "  stp x4, x5, [sp, #32]",
                "  stp x6, x7, [sp, #48]",
                "  str x30, [sp, #64]",
                *_address_lines("x9", dispatch_address),
                "  str x9, [sp, #72]",
                _branch("b", addresses, "bootstrap"),
            ),
        ),
        _Block(
            new,
            (
                f"{new}:",
                *_state_lines("x15", data_page, data_lo),
                f"  ldr x9, [x15, #{_slot_offset(index)}]",
                "  br x9",
            ),
        ),
    ]


def _bootstrap_block(
    addresses: dict[str, int],
    data_page: int,
    data_lo: int,
    first_api_symbol: str,
) -> _Block:
    return _Block(
        "bootstrap",
        (
            "bootstrap:",
            *_state_lines("x8", data_page, data_lo),
            "  ldaxr w16, [x8]",
            f"  cmp w16, #{STATE_UNINITIALIZED}",
            _branch("b.ne", addresses, "wait_initializing"),
            "  mov w17, #1",
            "  stxr w9, w17, [x8]",
            _branch_register("cbnz", "w9", addresses, "wait_initializing"),
            "  str xzr, [sp, #80]",
            "  str xzr, [sp, #88]",
            f"  add x9, sp, #{_INFO_OFFSET}",
            "  stp xzr, xzr, [x9]",
            "  stp xzr, xzr, [x9, #16]",
            _branch("b", addresses, f"resolve_{first_api_symbol}"),
        ),
    )


def _wait_blocks(addresses: dict[str, int], data_page: int, data_lo: int) -> list[_Block]:
    return [
        _Block(
            "wait_initializing",
            (
                "wait_initializing:",
                *_state_lines("x8", data_page, data_lo),
                "  ldar w16, [x8]",
                f"  cmp w16, #{STATE_INITIALIZING}",
                _branch("b.eq", addresses, "wait_pause"),
                _branch("b", addresses, "dispatch_after_wait"),
            ),
        ),
        _Block(
            "wait_pause",
            (
                "wait_pause:",
                "  yield",
                _branch("b", addresses, "wait_initializing"),
            ),
        ),
        _Block(
            "dispatch_after_wait",
            (
                "dispatch_after_wait:",
                _branch("b", addresses, "finish"),
            ),
        ),
    ]


def _resolve_block(
    api: object,
    index: int,
    addresses: dict[str, int],
    string_addresses: dict[str, int],
    data_page: int,
    data_lo: int,
    basename: str,
    fname_offset: int,
    fbase_offset: int,
    text_vmaddr: int,
    dlsym_target: int,
    dladdr_target: int,
    next_api_symbol: str | None,
    placeholder: bool,
) -> list[_Block]:
    symbol = api.symbol
    resolve = f"resolve_{symbol}"
    scan = f"basename_scan_{symbol}"
    suffix = f"basename_suffix_{symbol}"
    store = f"store_{symbol}"
    string_address = text_vmaddr if placeholder else string_addresses[symbol]
    return [
        _Block(
            resolve,
            (
                f"{resolve}:",
                "  mov x0, #-2",
                *(("  // basename LibASSBridge.dylib",) if index == 0 else ()),
                *_address_lines("x1", string_address),
                _stub_branch("bl", dlsym_target),
                _branch_register("cbz", "x0", addresses, "publish_old"),
                "  str x0, [sp, #88]",
                "  ldr x0, [sp, #88]",
                f"  add x1, sp, #{_INFO_OFFSET}",
                _stub_branch("bl", dladdr_target),
                _branch_register("cbnz", "w0", addresses, "publish_old"),
                f"  add x1, sp, #{_INFO_OFFSET}",
                f"  ldr x2, [x1, #{fbase_offset}]",
                _branch_register("cbz", "x2", addresses, "publish_old"),
                *(
                    ("  str x2, [sp, #80]",)
                    if index == 0
                    else (
                        "  ldr x3, [sp, #80]",
                        "  cmp x2, x3",
                        _branch("b.ne", addresses, "publish_old"),
                    )
                ),
                f"  ldr x3, [x1, #{fname_offset}]",
                _branch_register("cbz", "x3", addresses, "publish_old"),
                "  mov x4, x3",
                _branch("b", addresses, scan),
            ),
        ),
        _Block(
            scan,
            (
                f"{scan}:",
                "  ldrb w5, [x4]",
                _branch_register("cbz", "w5", addresses, "publish_old"),
                "  cmp w5, #47",
                _branch("b.eq", addresses, suffix),
                "  add x4, x4, #1",
                _branch("b", addresses, scan),
            ),
        ),
        _Block(
            suffix,
            (
                f"{suffix}:",
                "  add x4, x4, #1",
                *_basename_checks(basename, addresses),
                _branch("b", addresses, store),
            ),
        ),
        _Block(
            store,
            (
                f"{store}:",
                "  ldr x3, [sp, #88]",
                *_state_lines("x15", data_page, data_lo),
                f"  str x3, [x15, #{_slot_offset(index)}]",
                _branch(
                    "b",
                    addresses,
                    "publish_new"
                    if next_api_symbol is None
                    else f"resolve_{next_api_symbol}",
                ),
            ),
        ),
    ]


def _basename_checks(
    basename: str,
    addresses: dict[str, int],
) -> list[str]:
    lines: list[str] = []
    for offset, character in enumerate(basename):
        lines.extend(
            (
                f"  ldrb w5, [x4, #{offset}]",
                f"  cmp w5, #{ord(character)}",
                _branch("b.ne", addresses, "publish_old"),
            )
        )
    lines.extend(
        (
            f"  ldrb w5, [x4, #{len(basename)}]",
            _branch_register("cbnz", "w5", addresses, "publish_old"),
        )
    )
    return lines


def _resolve_blocks(
    apis: tuple,
    addresses: dict[str, int],
    string_addresses: dict[str, int],
    data_page: int,
    data_lo: int,
    basename: str,
    fname_offset: int,
    fbase_offset: int,
    text_vmaddr: int,
    dlsym_target: int,
    dladdr_target: int,
    placeholder: bool,
) -> list[_Block]:
    blocks: list[_Block] = []
    for index, api in enumerate(apis):
        blocks.extend(
            _resolve_block(
                api,
                index,
                addresses,
                string_addresses,
                data_page,
                data_lo,
                basename,
                fname_offset,
                fbase_offset,
                text_vmaddr,
                dlsym_target,
                dladdr_target,
                apis[index + 1].symbol if index + 1 < len(apis) else None,
                placeholder,
            )
        )
    return blocks


def _terminal_blocks(
    addresses: dict[str, int],
    data_page: int,
    data_lo: int,
    frame_size: int,
) -> list[_Block]:
    return [
        _Block(
            "publish_new",
            (
                "publish_new:",
                "  mov w16, #2",
                *_state_lines("x8", data_page, data_lo),
                "  stlr w16, [x8]  // stlr w16, [x_state]",
                _branch("b", addresses, "finish"),
            ),
        ),
        _Block(
            "publish_old",
            (
                "publish_old:",
                "  mov w16, #3",
                *_state_lines("x8", data_page, data_lo),
                "  stlr w16, [x8]  // stlr w16, [x_state]",
                _branch("b", addresses, "finish"),
            ),
        ),
        _Block(
            "finish",
            (
                "finish:",
                "  ldr x9, [sp, #72]",
                "  ldp x0, x1, [sp, #0]",
                "  ldp x2, x3, [sp, #16]",
                "  ldp x4, x5, [sp, #32]",
                "  ldp x6, x7, [sp, #48]",
                "  ldr x30, [sp, #64]",
                f"  add sp, sp, #{frame_size}",
                "  br x9",
            ),
        ),
    ]


def _block_names(apis: tuple) -> list[str]:
    names: list[str] = []
    for api in apis:
        names.extend(
            (
                f"dispatch_{api.symbol}",
                f"enter_{api.symbol}",
                f"new_{api.symbol}",
                f"resolve_{api.symbol}",
                f"basename_scan_{api.symbol}",
                f"basename_suffix_{api.symbol}",
                f"store_{api.symbol}",
            )
        )
    names.extend(
        (
            "bootstrap",
            "wait_initializing",
            "wait_pause",
            "dispatch_after_wait",
            "publish_new",
            "publish_old",
            "finish",
        )
    )
    return names


def _make_blocks(
    apis: tuple,
    addresses: dict[str, int],
    string_addresses: dict[str, int],
    layout: PayloadLayout,
    target_abi: TargetABI,
    manifest: Manifest,
    frame_size: int,
    placeholder: bool,
) -> list[_Block]:
    data_page, data_lo = _data_address(layout.data_vmaddr)
    blocks: list[_Block] = []
    for index, api in enumerate(apis):
        blocks.extend(
            _veneer_blocks(
                api,
                index,
                addresses,
                data_page,
                data_lo,
                frame_size,
                layout.text_vmaddr,
                placeholder,
            )
        )
    blocks.append(
        _bootstrap_block(
            addresses,
            data_page,
            data_lo,
            apis[0].symbol,
        )
    )
    blocks.extend(_wait_blocks(addresses, data_page, data_lo))
    blocks.extend(
        _resolve_blocks(
            apis,
            addresses,
            string_addresses,
            data_page,
            data_lo,
            manifest.expected_bridge_basename,
            target_abi.dl_info_fname_offset,
            target_abi.dl_info_fbase_offset,
            layout.text_vmaddr,
            layout.text_vmaddr if placeholder else manifest.dlsym_stub,
            layout.text_vmaddr if placeholder else manifest.dladdr_stub,
            placeholder,
        )
    )
    blocks.extend(_terminal_blocks(addresses, data_page, data_lo, frame_size))
    return blocks


def _assemble_block(toolchain: Toolchain, block: _Block, address: int) -> bytes:
    source = "\n".join(block.lines) + "\n"
    try:
        return toolchain.assemble(source, address)
    except RuntimeError as error:
        raise ValueError(f"failed to assemble payload block {block.name}: {error}") from error


def _source_for_blocks(blocks: Iterable[_Block], string_lines: Iterable[str]) -> str:
    return "\n\n".join("\n".join(block.lines) for block in blocks) + "\n\n" + "\n".join(string_lines) + "\n"


def _string_layout(
    apis: tuple,
    text_vmaddr: int,
    code_size: int,
) -> tuple[dict[str, int], list[str], int]:
    addresses: dict[str, int] = {}
    lines = []
    cursor = code_size
    for api in apis:
        cursor = _align(cursor, 4)
        lines.extend((".balign 4", f"string_{api.symbol}:", f"  .asciz \"{api.symbol}\""))
        addresses[api.symbol] = text_vmaddr + cursor
        cursor += len(api.symbol.encode("ascii")) + 1
    final_size = _align(cursor, 4)
    padding = final_size - cursor
    if padding:
        lines.append("  .byte " + ", ".join("0" for _ in range(padding)))
    return addresses, lines, final_size


def _validate_encoded_invariants(code: bytes, code_size: int) -> None:
    """Enforce the relocation-free text invariants on the assembled bytes."""

    publications = 0
    for offset in range(0, min(code_size, len(code)) - 3, 4):
        word = struct.unpack_from("<I", code, offset)[0]
        if (word & 0xFF800000) in (0xD2800000, 0xF2800000):
            raise ValueError("payload text contains a 64-bit MOVZ/MOVK literal")
        if word & 0xFF000000 in (0x18000000, 0x58000000):
            raise ValueError("payload text contains an LDR literal pool access")
        if word & 0xFFFFFC00 == 0x889FFC00:
            publications += 1
    if publications != 2:
        raise ValueError("payload must contain exactly two terminal publications")


def _decode_branch(code: bytes, offset: int) -> tuple[str, int, int] | None:
    if offset + 4 > len(code):
        return None
    word = struct.unpack_from("<I", code, offset)[0]
    if word & 0x7C000000 == 0x14000000:
        displacement = (word & 0x03FFFFFF) << 2
        if displacement & (1 << 27):
            displacement -= 1 << 28
        return "unconditional", displacement, _BRANCH_LIMIT
    if word & 0xFF000010 == 0x54000000 or word & 0x7E000000 == 0x34000000:
        displacement = ((word >> 5) & 0x7FFFF) << 2
        if displacement & (1 << 20):
            displacement -= 1 << 21
        return "conditional", displacement, 1 << 20
    return None


def _validate_encoded_branches(
    code: bytes,
    code_size: int,
    layout: PayloadLayout,
    manifest: Manifest,
) -> None:
    for offset in range(0, min(code_size, len(code)) - 3, 4):
        word = struct.unpack_from("<I", code, offset)[0]
        if (word & 0xFF800000) in (0xD2800000, 0xF2800000):
            raise ValueError("payload text contains a 64-bit MOVZ/MOVK literal")
    targets: list[int] = []
    for offset in range(0, min(code_size, len(code)) - 3, 4):
        decoded = _decode_branch(code, offset)
        if decoded is None:
            continue
        kind, displacement, limit = decoded
        source = layout.text_vmaddr + offset
        target = source + displacement
        _require_aligned(target, 4, "encoded branch target")
        if not -limit <= displacement < limit:
            raise ValueError(
                f"encoded {kind} branch is outside its arm64 range: "
                f"{source:#x} -> {target:#x}"
            )
        if kind == "unconditional":
            targets.append(target)
    old_targets = {api.old_target for api in manifest.apis}
    if not old_targets.issubset(set(targets)):
        raise ValueError("payload is missing one or more old-target branches")
    if sum(target in old_targets for target in targets) != _API_COUNT:
        raise ValueError("payload does not contain exactly one old branch per API")
    if targets.count(manifest.dlsym_stub) != _API_COUNT:
        raise ValueError("payload does not contain one dlsym call per API")
    if targets.count(manifest.dladdr_stub) != _API_COUNT:
        raise ValueError("payload does not contain one dladdr call per API")


def _check_external_branches(
    manifest: Manifest,
    addresses: dict[str, int],
) -> None:
    for api in manifest.apis:
        _check_branch(
            addresses[f"dispatch_{api.symbol}"],
            api.old_target,
            f"{api.symbol} old target",
        )
        resolve = addresses[f"resolve_{api.symbol}"]
        _check_branch(
            resolve,
            manifest.dlsym_stub,
            f"{api.symbol} dlsym stub",
        )
        _check_branch(
            resolve,
            manifest.dladdr_stub,
            f"{api.symbol} dladdr stub",
        )


def _validate_callsite_branches(
    layout: PayloadLayout,
    manifest: Manifest,
    symbols: dict[str, int],
) -> None:
    for api in manifest.apis:
        veneer = symbols[f"veneer_{api.symbol}"]
        for site in api.call_sites:
            _check_branch(site, veneer, f"callsite {site:#x} -> {api.symbol}")
            # Re-encode through the same range checker used by the patcher.
            if encode_bl(site, veneer) & 0xFC000000 != 0x94000000:
                raise ValueError("callsite redirect is not a BL")


def _build_payload(
    layout: PayloadLayout,
    manifest: Manifest,
    target_abi: TargetABI,
) -> Payload:
    text_vmaddr, data_vmaddr, _ = _validate_layout(layout)
    apis = _validate_manifest(manifest)
    info_size, fname_offset, fbase_offset, frame_size = _validate_abi(target_abi)
    if _INFO_OFFSET + info_size > frame_size:
        raise ValueError("Dl_info does not fit in the bootstrap frame")
    data_page, data_lo = _data_address(data_vmaddr)
    _check_adrp(text_vmaddr, data_page, "payload data")

    all_names = _block_names(apis)
    addresses = {name: text_vmaddr for name in all_names}
    string_addresses = {api.symbol: text_vmaddr for api in apis}

    toolchain = Toolchain()
    placeholder_blocks = _make_blocks(
        apis,
        addresses,
        string_addresses,
        layout,
        target_abi,
        manifest,
        frame_size,
        True,
    )
    cursor = 0
    for block in placeholder_blocks:
        encoded = _assemble_block(toolchain, block, text_vmaddr)
        if len(encoded) % 4:
            raise ValueError(f"payload block {block.name} is not instruction aligned")
        addresses[block.name] = text_vmaddr + cursor
        cursor += len(encoded)

    string_addresses, string_lines, text_size = _string_layout(
        apis,
        text_vmaddr,
        cursor,
    )
    for api in apis:
        _check_adrp(
            text_vmaddr,
            string_addresses[api.symbol] & ~0xFFF,
            f"string {api.symbol}",
        )
    _check_external_branches(manifest, addresses)

    final_blocks = _make_blocks(
        apis,
        addresses,
        string_addresses,
        layout,
        target_abi,
        manifest,
        frame_size,
        False,
    )
    source = _source_for_blocks(final_blocks, string_lines)
    try:
        text = toolchain.assemble(source, text_vmaddr)
    except RuntimeError as error:
        raise ValueError(f"failed to assemble final payload: {error}") from error
    if len(text) != text_size:
        raise ValueError(
            f"payload size prediction mismatch: predicted {text_size}, got {len(text)}"
        )

    symbols: dict[str, int] = {
        "text": text_vmaddr,
        "data": data_vmaddr,
        "state": data_vmaddr + layout.state_rva,
        "slots": data_vmaddr + layout.slots_rva,
        "text_end": text_vmaddr + len(text),
        "data_end": data_vmaddr + _LOGICAL_DATA_SIZE,
    }
    for block in final_blocks:
        symbols[block.name] = addresses[block.name]
    for api in apis:
        veneer = addresses[f"dispatch_{api.symbol}"]
        symbols[f"veneer_{api.symbol}"] = veneer
        symbols[f"string_{api.symbol}"] = string_addresses[api.symbol]
        for site in api.call_sites:
            symbols[f"callsite_{site:#x}"] = veneer
    stubs = {
        "dlsym": manifest.dlsym_stub,
        "dladdr": manifest.dladdr_stub,
    }
    data = (
        struct.pack("<II", STATE_UNINITIALIZED, 0)
        + b"\x00" * (_API_COUNT * _SLOT_SIZE)
    )
    if len(data) != _LOGICAL_DATA_SIZE:
        raise AssertionError("payload data layout changed unexpectedly")
    _validate_encoded_invariants(text, cursor)
    _validate_encoded_branches(text, cursor, layout, manifest)
    _validate_callsite_branches(layout, manifest, symbols)
    return Payload(text=bytes(text), data=data, symbols=symbols, stubs=stubs)


def measure_payload(layout: PayloadLayout, manifest: Manifest, target_abi: TargetABI) -> int:
    """Return the exact encoded text size, including the in-text symbol table."""

    return len(assemble_payload(layout, manifest, target_abi).text)


def assemble_payload(
    layout: PayloadLayout,
    manifest: Manifest,
    target_abi: TargetABI,
) -> Payload:
    """Assemble the atomic dispatch payload at the supplied final addresses."""

    return _build_payload(layout, manifest, target_abi)
