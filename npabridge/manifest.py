import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import target_abi


@dataclass(frozen=True)
class APIBinding:
    symbol: str
    call_sites: tuple[int, ...]
    old_target: int

    @property
    def dlsym_name(self) -> str:
        return self.symbol

    @property
    def macho_name(self) -> str:
        return "_" + self.symbol


@dataclass(frozen=True)
class Callback:
    app_callback: int
    prototype: str
    va_list_size: int
    ignored_argument_register: str


@dataclass(frozen=True)
class Domain:
    """One independently published unit of a dylib."""

    id: str
    apis: tuple[APIBinding, ...]


@dataclass(frozen=True)
class Unit:
    """A domain flattened together with the dylib that carries it."""

    dylib_id: str
    domain_id: str
    basename: str
    apis: tuple[APIBinding, ...]

    @property
    def id(self) -> str:
        return f"{self.dylib_id}/{self.domain_id}"

    @property
    def symbol_count(self) -> int:
        return len(self.apis)

    @property
    def call_site_count(self) -> int:
        return sum(len(api.call_sites) for api in self.apis)


@dataclass(frozen=True)
class ExtraSite:
    """A site that is rewritten whenever its dylib is installed."""

    site: int
    expected: int
    replacement: int


@dataclass(frozen=True)
class BuildSpec:
    """Dev-only inputs for building this dylib, relative to the repository root."""

    source: str
    exports: str
    closure: str
    include_root: str
    lib_root: str


@dataclass(frozen=True)
class Dylib:
    id: str
    library_version: str
    basename: str
    domains: tuple[Domain, ...]
    extra_sites: tuple[ExtraSite, ...] = ()
    callback: Callback | None = None
    build: BuildSpec | None = None

    @property
    def path(self) -> str:
        return f"@executable_path/Frameworks/{self.basename}"

    @property
    def install_name(self) -> str:
        return f"@rpath/{self.basename}"


@dataclass(frozen=True)
class Manifest:
    imagebase: int
    main_sha256: str
    app_version: str
    target_abi: target_abi.TargetABI
    dlsym_stub: int
    dladdr_stub: int
    dylibs: tuple[Dylib, ...]

    def units(self, dylib_ids: Sequence[str] | None = None) -> tuple[Unit, ...]:
        """Flatten the selected dylibs into their units, in manifest order."""

        selected = self._select(dylib_ids)
        units = tuple(
            Unit(
                dylib_id=dylib.id,
                domain_id=domain.id,
                basename=dylib.basename,
                apis=domain.apis,
            )
            for dylib in selected
            for domain in dylib.domains
        )
        if not units:
            raise ValueError("no bridge unit was selected")
        return units

    def dylib(self, dylib_id: str) -> Dylib:
        for dylib in self.dylibs:
            if dylib.id == dylib_id:
                return dylib
        raise KeyError(dylib_id)

    def extra_sites(self, dylib_ids: Sequence[str]) -> tuple[ExtraSite, ...]:
        wanted = set(dylib_ids)
        return tuple(
            site
            for dylib in self.dylibs
            if dylib.id in wanted
            for site in dylib.extra_sites
        )

    def api(self, symbol: str) -> APIBinding:
        for unit in self.units():
            for api in unit.apis:
                if api.symbol == symbol:
                    return api
        raise KeyError(symbol)

    def _select(self, dylib_ids: Sequence[str] | None) -> tuple[Dylib, ...]:
        if dylib_ids is None:
            return self.dylibs
        wanted = tuple(dict.fromkeys(dylib_ids))
        known = {dylib.id for dylib in self.dylibs}
        unknown = [dylib_id for dylib_id in wanted if dylib_id not in known]
        if unknown:
            raise KeyError(f"unknown dylib ids: {', '.join(unknown)}")
        return tuple(dylib for dylib in self.dylibs if dylib.id in set(wanted))


# The app reaches FFmpeg with calls (BL) and with tail calls (B). Both encode a
# displacement the same way but differ in frame behaviour: turning a tail call
# into a BL would make the callee return into whatever follows the site, so the
# patcher preserves the opcode the site already had.
B_OPCODE = 0x14000000
BL_OPCODE = 0x94000000
BRANCH_OPCODES = (B_OPCODE, BL_OPCODE)


def branch_opcode(word: int) -> int:
    """The opcode of a B/BL instruction word."""

    opcode = word & 0xFC000000
    if opcode not in BRANCH_OPCODES:
        raise ValueError(f"instruction {word:#010x} is neither B nor BL")
    return opcode


def encode_branch(opcode: int, call_site: int, target: int) -> int:
    if opcode not in BRANCH_OPCODES:
        raise ValueError("branch opcode must be B or BL")
    displacement = target - call_site
    if call_site & 3 or target & 3:
        raise ValueError("branch addresses must be 4-byte aligned")
    if not -(1 << 27) <= displacement < 1 << 27:
        raise ValueError("branch displacement is out of range")
    return opcode | ((displacement >> 2) & 0x03FFFFFF)


def _callback(raw: dict | None) -> Callback | None:
    if raw is None:
        return None
    return Callback(
        app_callback=int(raw["app_callback"], 0),
        prototype=raw["prototype"],
        va_list_size=raw["va_list_size"],
        ignored_argument_register=raw["ignored_argument_register"],
    )


def _build_spec(raw: dict | None) -> BuildSpec | None:
    if raw is None:
        return None
    return BuildSpec(
        source=raw["source"],
        exports=raw["exports"],
        closure=raw["closure"],
        include_root=raw["include_root"],
        lib_root=raw["lib_root"],
    )


def load_manifest(path: Path) -> Manifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    dylibs = tuple(
        Dylib(
            id=dylib["id"],
            library_version=dylib["library_version"],
            basename=dylib["basename"],
            domains=tuple(
                Domain(
                    id=domain["id"],
                    apis=tuple(
                        APIBinding(
                            symbol=api["symbol"],
                            call_sites=tuple(int(value, 0) for value in api["call_sites"]),
                            old_target=int(api["old_target"], 0),
                        )
                        for api in domain["apis"]
                    ),
                )
                for domain in dylib["domains"]
            ),
            extra_sites=tuple(
                ExtraSite(
                    site=int(site["site"], 0),
                    expected=int(site["expected"], 0),
                    replacement=int(site["replacement"], 0),
                )
                for site in dylib.get("extra_sites", ())
            ),
            callback=_callback(dylib.get("callback")),
            build=_build_spec(dylib.get("build")),
        )
        for dylib in data["dylibs"]
    )
    identifiers = [dylib.id for dylib in dylibs]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"duplicate dylib ids in {path.name}: {identifiers}")
    return Manifest(
        imagebase=int(data["imagebase"], 0),
        main_sha256=data["main_sha256"],
        app_version=data["app_version"],
        target_abi=target_abi.from_manifest(data["target_abi"]),
        dlsym_stub=int(data["dlsym_stub"], 0),
        dladdr_stub=int(data["dladdr_stub"], 0),
        dylibs=dylibs,
    )


def select_manifest(directory: Path, main: Path) -> Manifest:
    """Pick the manifest whose frozen main hash matches this executable."""

    digest = hashlib.sha256(Path(main).read_bytes()).hexdigest()
    supported = []
    for path in sorted(Path(directory).glob("*.json")):
        manifest = load_manifest(path)
        supported.append(manifest.app_version)
        if manifest.main_sha256 == digest:
            return manifest
    raise ValueError(
        "no manifest matches this main executable\n"
        f"  input sha256: {digest}\n"
        f"  supported nPlayer versions: {', '.join(sorted(supported)) or 'none'}\n"
        "  (an already-patched IPA and an App Store-encrypted dump both fail this check)"
    )
