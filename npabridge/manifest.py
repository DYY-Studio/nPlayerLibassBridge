import json
from dataclasses import dataclass
from pathlib import Path


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
class Manifest:
    imagebase: int
    main_sha256: str
    dlsym_stub: int
    dladdr_stub: int
    bridge_path: str
    expected_bridge_basename: str
    callback: Callback
    apis: tuple[APIBinding, ...]

    def api(self, symbol: str) -> APIBinding:
        for api in self.apis:
            if api.symbol == symbol:
                return api
        raise KeyError(symbol)


def encode_bl(call_site: int, target: int) -> int:
    displacement = target - call_site
    if call_site & 3 or target & 3:
        raise ValueError("BL addresses must be 4-byte aligned")
    if not -(1 << 27) <= displacement < 1 << 27:
        raise ValueError("BL displacement is out of range")
    return 0x94000000 | ((displacement >> 2) & 0x03FFFFFF)


def load_manifest(path: Path) -> Manifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    callback = data["callback"]
    apis = tuple(
        APIBinding(
            symbol=api["symbol"],
            call_sites=tuple(int(value, 0) for value in api["call_sites"]),
            old_target=int(api["old_target"], 0),
        )
        for api in data["apis"]
    )
    return Manifest(
        imagebase=int(data["imagebase"], 0),
        main_sha256=data["main_sha256"],
        dlsym_stub=int(data["dlsym_stub"], 0),
        dladdr_stub=int(data["dladdr_stub"], 0),
        bridge_path=data["bridge_path"],
        expected_bridge_basename=data["expected_bridge_basename"],
        callback=Callback(
            app_callback=int(callback["app_callback"], 0),
            prototype=callback["prototype"],
            va_list_size=callback["va_list_size"],
            ignored_argument_register=callback["ignored_argument_register"],
        ),
        apis=apis,
    )
