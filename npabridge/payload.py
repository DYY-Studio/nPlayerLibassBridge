"""Generate the fixed-address arm64 dispatch payload.

The payload is assembled at a caller-supplied final virtual address.  It uses
only PC-relative control flow and PC-relative data/string addresses; the data
blob carries one state word plus one null slot per API, per unit.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import struct
from typing import Iterable, Sequence

from .manifest import Manifest, Unit, encode_bl
from .target_abi import TargetABI
from .toolchain import Toolchain


STATE_UNINITIALIZED = 0
STATE_INITIALIZING = 1
STATE_NEW = 2
STATE_OLD = 3

_UNIT_HEADER_SIZE = 8
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
    """Final virtual addresses of the two payload segments."""

    text_vmaddr: int
    data_vmaddr: int


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


@dataclass(frozen=True)
class _UnitData:
    """One unit's slice of the data blob, already lowered to adrp/add offsets."""

    unit: Unit
    base: int
    state_lo: int

    @property
    def size(self) -> int:
        return _UNIT_HEADER_SIZE + _SLOT_SIZE * self.unit.symbol_count

    def slot_offset(self, index: int) -> int:
        """Slot `index`, relative to the address `state_lo` lowers to."""

        return _UNIT_HEADER_SIZE + _SLOT_SIZE * index


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


def _validate_layout(layout: PayloadLayout) -> tuple[int, int]:
    if not isinstance(layout, PayloadLayout):
        raise TypeError("layout must be a PayloadLayout")
    text_vmaddr = _require_uint(layout.text_vmaddr, "text_vmaddr")
    data_vmaddr = _require_uint(layout.data_vmaddr, "data_vmaddr")
    _require_aligned(text_vmaddr, 4, "text_vmaddr")
    _require_aligned(data_vmaddr, 16, "data_vmaddr")
    return text_vmaddr, data_vmaddr


def _validate_units(units: Sequence[Unit]) -> tuple[Unit, ...]:
    if not isinstance(units, (tuple, list)) or not units:
        raise ValueError("payload requires at least one bridge unit")
    seen_units: set[str] = set()
    symbols: list[str] = []
    callsites: list[int] = []
    for unit in units:
        if unit.id in seen_units:
            raise ValueError(f"duplicate bridge unit: {unit.id}")
        seen_units.add(unit.id)
        if not unit.apis:
            raise ValueError(f"bridge unit {unit.id} has no APIs")
        if any(ord(character) > 0x7F for character in unit.basename):
            raise ValueError("bridge basename must be ASCII")
        for api in unit.apis:
            symbols.append(api.symbol)
            callsites.extend(api.call_sites)
    if len(set(symbols)) != len(symbols):
        raise ValueError("payload API symbols must be unique across units")
    if len(set(callsites)) != len(callsites):
        raise ValueError("one call site may only belong to one unit")
    for symbol in symbols:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
            raise ValueError(f"invalid API symbol: {symbol!r}")
    for site in callsites:
        _require_aligned(_require_uint(site, "call site"), 4, "call site")
    for unit in units:
        for api in unit.apis:
            _require_aligned(
                _require_uint(api.old_target, f"old target for {api.symbol}"),
                4,
                f"old target for {api.symbol}",
            )
    return tuple(units)


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


def data_size(units: Sequence[Unit]) -> int:
    """Exact size of the data blob for these units."""

    return sum(_UNIT_HEADER_SIZE + _SLOT_SIZE * unit.symbol_count for unit in units)


def unit_offsets(units: Sequence[Unit]) -> tuple[tuple[Unit, int], ...]:
    """Pair each unit with the byte offset of its state word in the data blob."""

    result = []
    cursor = 0
    for unit in units:
        result.append((unit, cursor))
        cursor += _UNIT_HEADER_SIZE + _SLOT_SIZE * unit.symbol_count
    return tuple(result)


def _unit_data(units: Sequence[Unit], data_vmaddr: int) -> tuple[_UnitData, ...]:
    page, data_lo = _data_address(data_vmaddr)
    result = []
    for unit, base in unit_offsets(units):
        result.append(
            _UnitData(
                unit=unit,
                base=base,
                state_lo=data_lo + base,
            )
        )
    total = data_size(units)
    if data_lo + total > 0x1000:
        raise ValueError("payload data blob must stay inside one page")
    return tuple(result)


def _state_lines(register: str, data_page: int, offset: int) -> list[str]:
    return [
        f"  adrp {register}, {_hex(data_page)}",
        f"  add {register}, {register}, #{offset}",
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


def _veneer_blocks(
    data: _UnitData,
    api: object,
    index: int,
    addresses: dict[str, int],
    data_page: int,
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
                *_state_lines("x8", data_page, data.state_lo),
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
                f"  str x30, [sp, #{_LR_OFFSET}]",
                *_address_lines("x9", dispatch_address),
                f"  str x9, [sp, #{_CONTINUATION_OFFSET}]",
                _branch("b", addresses, f"bootstrap_{data.unit.id}"),
            ),
        ),
        _Block(
            new,
            (
                f"{new}:",
                *_state_lines("x15", data_page, data.state_lo),
                f"  ldr x9, [x15, #{data.slot_offset(index)}]",
                "  br x9",
            ),
        ),
    ]


def _bootstrap_block(
    data: _UnitData,
    addresses: dict[str, int],
    data_page: int,
    first_api_symbol: str,
) -> _Block:
    name = f"bootstrap_{data.unit.id}"
    return _Block(
        name,
        (
            f"{name}:",
            *_state_lines("x8", data_page, data.state_lo),
            "  ldaxr w16, [x8]",
            f"  cmp w16, #{STATE_UNINITIALIZED}",
            _branch("b.ne", addresses, f"wait_initializing_{data.unit.id}"),
            "  mov w17, #1",
            "  stxr w9, w17, [x8]",
            _branch_register(
                "cbnz", "w9", addresses, f"wait_initializing_{data.unit.id}"
            ),
            f"  str xzr, [sp, #{_FBASE_OFFSET}]",
            f"  str xzr, [sp, #{_CANDIDATE_OFFSET}]",
            f"  add x9, sp, #{_INFO_OFFSET}",
            "  stp xzr, xzr, [x9]",
            "  stp xzr, xzr, [x9, #16]",
            _branch("b", addresses, f"resolve_{first_api_symbol}"),
        ),
    )


def _wait_blocks(
    unit: Unit,
    addresses: dict[str, int],
    data_page: int,
    state_lo: int,
) -> list[_Block]:
    initializing = f"wait_initializing_{unit.id}"
    pause = f"wait_pause_{unit.id}"
    after = f"dispatch_after_wait_{unit.id}"
    return [
        _Block(
            initializing,
            (
                f"{initializing}:",
                *_state_lines("x8", data_page, state_lo),
                "  ldar w16, [x8]",
                f"  cmp w16, #{STATE_INITIALIZING}",
                _branch("b.eq", addresses, pause),
                _branch("b", addresses, after),
            ),
        ),
        _Block(
            pause,
            (
                f"{pause}:",
                "  yield",
                _branch("b", addresses, initializing),
            ),
        ),
        _Block(
            after,
            (
                f"{after}:",
                _branch("b", addresses, "finish"),
            ),
        ),
    ]


def _resolve_block(
    data: _UnitData,
    api: object,
    index: int,
    addresses: dict[str, int],
    string_addresses: dict[str, int],
    data_page: int,
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
    publish_old = f"publish_old_{data.unit.id}"
    string_address = text_vmaddr if placeholder else string_addresses[symbol]
    return [
        _Block(
            resolve,
            (
                f"{resolve}:",
                "  mov x0, #-2",
                *((
                    f"  // unit {data.unit.id}, basename {data.unit.basename}",
                ) if index == 0 else ()),
                *_address_lines("x1", string_address),
                _stub_branch("bl", dlsym_target),
                _branch_register("cbz", "x0", addresses, publish_old),
                f"  str x0, [sp, #{_CANDIDATE_OFFSET}]",
                f"  ldr x0, [sp, #{_CANDIDATE_OFFSET}]",
                f"  add x1, sp, #{_INFO_OFFSET}",
                _stub_branch("bl", dladdr_target),
                _branch_register("cbnz", "w0", addresses, publish_old),
                f"  add x1, sp, #{_INFO_OFFSET}",
                f"  ldr x2, [x1, #{fbase_offset}]",
                _branch_register("cbz", "x2", addresses, publish_old),
                *(
                    (f"  str x2, [sp, #{_FBASE_OFFSET}]",)
                    if index == 0
                    else (
                        f"  ldr x3, [sp, #{_FBASE_OFFSET}]",
                        "  cmp x2, x3",
                        _branch("b.ne", addresses, publish_old),
                    )
                ),
                f"  ldr x3, [x1, #{fname_offset}]",
                _branch_register("cbz", "x3", addresses, publish_old),
                "  mov x4, x3",
                _branch("b", addresses, scan),
            ),
        ),
        _Block(
            scan,
            (
                f"{scan}:",
                "  ldrb w5, [x4]",
                _branch_register("cbz", "w5", addresses, publish_old),
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
                *_basename_checks(data.unit.basename, addresses, publish_old),
                _branch("b", addresses, store),
            ),
        ),
        _Block(
            store,
            (
                f"{store}:",
                f"  ldr x3, [sp, #{_CANDIDATE_OFFSET}]",
                *_state_lines("x15", data_page, data.state_lo),
                f"  str x3, [x15, #{data.slot_offset(index)}]",
                _branch(
                    "b",
                    addresses,
                    f"publish_new_{data.unit.id}"
                    if next_api_symbol is None
                    else f"resolve_{next_api_symbol}",
                ),
            ),
        ),
    ]


def _basename_checks(
    basename: str,
    addresses: dict[str, int],
    publish_old: str,
) -> list[str]:
    lines: list[str] = []
    for offset, character in enumerate(basename):
        lines.extend(
            (
                f"  ldrb w5, [x4, #{offset}]",
                f"  cmp w5, #{ord(character)}",
                _branch("b.ne", addresses, publish_old),
            )
        )
    lines.extend(
        (
            f"  ldrb w5, [x4, #{len(basename)}]",
            _branch_register("cbnz", "w5", addresses, publish_old),
        )
    )
    return lines


def _resolve_blocks(
    data: _UnitData,
    addresses: dict[str, int],
    string_addresses: dict[str, int],
    data_page: int,
    fname_offset: int,
    fbase_offset: int,
    text_vmaddr: int,
    dlsym_target: int,
    dladdr_target: int,
    placeholder: bool,
) -> list[_Block]:
    apis = data.unit.apis
    blocks: list[_Block] = []
    for index, api in enumerate(apis):
        blocks.extend(
            _resolve_block(
                data,
                api,
                index,
                addresses,
                string_addresses,
                data_page,
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
    unit: Unit,
    addresses: dict[str, int],
    data_page: int,
    state_lo: int,
    frame_size: int,
) -> list[_Block]:
    new = f"publish_new_{unit.id}"
    old = f"publish_old_{unit.id}"
    return [
        _Block(
            new,
            (
                f"{new}:",
                f"  mov w16, #{STATE_NEW}",
                *_state_lines("x8", data_page, state_lo),
                "  stlr w16, [x8]  // stlr w16, [x_state]",
                _branch("b", addresses, "finish"),
            ),
        ),
        _Block(
            old,
            (
                f"{old}:",
                f"  mov w16, #{STATE_OLD}",
                *_state_lines("x8", data_page, state_lo),
                "  stlr w16, [x8]  // stlr w16, [x_state]",
                _branch("b", addresses, "finish"),
            ),
        ),
    ]


def _finish_block(addresses: dict[str, int], frame_size: int) -> _Block:
    return _Block(
        "finish",
        (
            "finish:",
            f"  ldr x9, [sp, #{_CONTINUATION_OFFSET}]",
            "  ldp x0, x1, [sp, #0]",
            "  ldp x2, x3, [sp, #16]",
            "  ldp x4, x5, [sp, #32]",
            "  ldp x6, x7, [sp, #48]",
            f"  ldr x30, [sp, #{_LR_OFFSET}]",
            f"  add sp, sp, #{frame_size}",
            "  br x9",
        ),
    )


def _block_names(units: Sequence[Unit]) -> list[str]:
    names: list[str] = []
    for unit in units:
        for api in unit.apis:
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
                f"bootstrap_{unit.id}",
                f"wait_initializing_{unit.id}",
                f"wait_pause_{unit.id}",
                f"dispatch_after_wait_{unit.id}",
                f"publish_new_{unit.id}",
                f"publish_old_{unit.id}",
            )
        )
    names.append("finish")
    return names


def _make_blocks(
    units: Sequence[Unit],
    addresses: dict[str, int],
    string_addresses: dict[str, int],
    layout: PayloadLayout,
    manifest: Manifest,
    frame_size: int,
    placeholder: bool,
    unit_datas: Sequence[_UnitData],
) -> list[_Block]:
    data_page, _ = _data_address(layout.data_vmaddr)
    blocks: list[_Block] = []
    for data in unit_datas:
        for index, api in enumerate(data.unit.apis):
            blocks.extend(
                _veneer_blocks(
                    data,
                    api,
                    index,
                    addresses,
                    data_page,
                    frame_size,
                    layout.text_vmaddr,
                    placeholder,
                )
            )
        blocks.append(
            _bootstrap_block(
                data,
                addresses,
                data_page,
                data.unit.apis[0].symbol,
            )
        )
        blocks.extend(
            _wait_blocks(data.unit, addresses, data_page, data.state_lo)
        )
    for data in unit_datas:
        blocks.extend(
            _resolve_blocks(
                data,
                addresses,
                string_addresses,
                data_page,
                manifest.target_abi.dl_info_fname_offset,
                manifest.target_abi.dl_info_fbase_offset,
                layout.text_vmaddr,
                layout.text_vmaddr if placeholder else manifest.dlsym_stub,
                layout.text_vmaddr if placeholder else manifest.dladdr_stub,
                placeholder,
            )
        )
    for data in unit_datas:
        blocks.extend(
            _terminal_blocks(
                data.unit,
                addresses,
                data_page,
                data.state_lo,
                frame_size,
            )
        )
    blocks.append(_finish_block(addresses, frame_size))
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
    units: Sequence[Unit],
    text_vmaddr: int,
    code_size: int,
) -> tuple[dict[str, int], list[str], int]:
    addresses: dict[str, int] = {}
    lines = []
    cursor = code_size
    for unit in units:
        for api in unit.apis:
            cursor = _align(cursor, 4)
            lines.extend((".balign 4", f"string_{api.symbol}:", f"  .asciz \"{api.symbol}\""))
            addresses[api.symbol] = text_vmaddr + cursor
            cursor += len(api.symbol.encode("ascii")) + 1
    final_size = _align(cursor, 4)
    padding = final_size - cursor
    if padding:
        lines.append("  .byte " + ", ".join("0" for _ in range(padding)))
    return addresses, lines, final_size


def _validate_encoded_invariants(code: bytes, code_size: int, unit_count: int) -> None:
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
    if publications != 2 * unit_count:
        raise ValueError("payload must contain exactly two publications per unit")


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
    units: Sequence[Unit],
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
    apis = [api for unit in units for api in unit.apis]
    old_targets = {api.old_target for api in apis}
    if not old_targets.issubset(set(targets)):
        raise ValueError("payload is missing one or more old-target branches")
    if sum(target in old_targets for target in targets) != len(apis):
        raise ValueError("payload does not contain exactly one old branch per API")
    if targets.count(manifest.dlsym_stub) != len(apis):
        raise ValueError("payload does not contain one dlsym call per API")
    if targets.count(manifest.dladdr_stub) != len(apis):
        raise ValueError("payload does not contain one dladdr call per API")


def _check_external_branches(
    manifest: Manifest,
    addresses: dict[str, int],
    units: Sequence[Unit],
) -> None:
    for unit in units:
        for api in unit.apis:
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
    units: Sequence[Unit],
    symbols: dict[str, int],
) -> None:
    for unit in units:
        for api in unit.apis:
            veneer = symbols[f"veneer_{api.symbol}"]
            for site in api.call_sites:
                _check_branch(site, veneer, f"callsite {site:#x} -> {api.symbol}")
                # Re-encode through the same range checker used by the patcher.
                if encode_bl(site, veneer) & 0xFC000000 != 0x94000000:
                    raise ValueError("callsite redirect is not a BL")


def _payload_data(unit_datas: Sequence[_UnitData]) -> bytes:
    data = bytearray()
    for unit_data in unit_datas:
        data += struct.pack("<II", STATE_UNINITIALIZED, 0)
        data += b"\x00" * (_SLOT_SIZE * unit_data.unit.symbol_count)
    if len(data) != data_size([data.unit for data in unit_datas]):
        raise AssertionError("payload data layout changed unexpectedly")
    return bytes(data)


def _build_payload(
    layout: PayloadLayout,
    manifest: Manifest,
    target_abi: TargetABI,
    units: Sequence[Unit],
) -> Payload:
    text_vmaddr, data_vmaddr = _validate_layout(layout)
    units = _validate_units(units)
    info_size, fname_offset, fbase_offset, frame_size = _validate_abi(target_abi)
    if _INFO_OFFSET + info_size > frame_size:
        raise ValueError("Dl_info does not fit in the bootstrap frame")
    data_page, _ = _data_address(data_vmaddr)
    _check_adrp(text_vmaddr, data_page, "payload data")
    unit_datas = _unit_data(units, data_vmaddr)
    data = _payload_data(unit_datas)

    apis = [api for unit in units for api in unit.apis]
    all_names = _block_names(units)
    addresses = {name: text_vmaddr for name in all_names}
    string_addresses = {api.symbol: text_vmaddr for api in apis}

    toolchain = Toolchain()
    placeholder_blocks = _make_blocks(
        units,
        addresses,
        string_addresses,
        layout,
        manifest,
        frame_size,
        True,
        unit_datas,
    )
    cursor = 0
    for block in placeholder_blocks:
        encoded = _assemble_block(toolchain, block, text_vmaddr)
        if len(encoded) % 4:
            raise ValueError(f"payload block {block.name} is not instruction aligned")
        addresses[block.name] = text_vmaddr + cursor
        cursor += len(encoded)

    string_addresses, string_lines, text_size = _string_layout(
        units,
        text_vmaddr,
        cursor,
    )
    for api in apis:
        _check_adrp(
            text_vmaddr,
            string_addresses[api.symbol] & ~0xFFF,
            f"string {api.symbol}",
        )
    _check_external_branches(manifest, addresses, units)

    final_blocks = _make_blocks(
        units,
        addresses,
        string_addresses,
        layout,
        manifest,
        frame_size,
        False,
        unit_datas,
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
        "text_end": text_vmaddr + len(text),
        "data_end": data_vmaddr + len(data),
    }
    for block in final_blocks:
        symbols[block.name] = addresses[block.name]
    for unit_data in unit_datas:
        symbols[f"state_{unit_data.unit.id}"] = data_vmaddr + unit_data.base
        symbols[f"slots_{unit_data.unit.id}"] = (
            data_vmaddr + unit_data.base + _UNIT_HEADER_SIZE
        )
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
    _validate_encoded_invariants(text, cursor, len(units))
    _validate_encoded_branches(text, cursor, layout, manifest, units)
    _validate_callsite_branches(layout, units, symbols)
    return Payload(text=bytes(text), data=data, symbols=symbols, stubs=stubs)


def measure_payload(
    layout: PayloadLayout,
    manifest: Manifest,
    target_abi: TargetABI,
    units: Sequence[Unit],
) -> int:
    """Return the exact encoded text size, including the in-text symbol table."""

    return len(assemble_payload(layout, manifest, target_abi, units).text)


def assemble_payload(
    layout: PayloadLayout,
    manifest: Manifest,
    target_abi: TargetABI,
    units: Sequence[Unit],
) -> Payload:
    """Assemble the atomic dispatch payload at the supplied final addresses."""

    return _build_payload(layout, manifest, target_abi, units)
