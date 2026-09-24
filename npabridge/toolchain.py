"""Keystone-backed arm64 assembler used to encode the dispatch payload."""

from __future__ import annotations

import ctypes
from pathlib import Path


DEFAULT_LIBRARY = (
    Path(__file__).resolve().parents[1] / "build" / "host" / "libkeystone.dylib"
)
ARCH_ARM64 = 2
MODE_LITTLE_ENDIAN = 0
SUPPORTED_VERSION = (0, 9)


class Toolchain:
    def __init__(self, library_path: str | Path | None = None) -> None:
        selected = DEFAULT_LIBRARY if library_path is None else Path(library_path)
        if not selected.is_file():
            raise FileNotFoundError(selected)
        self.library_path = selected.resolve()
        self._library = ctypes.CDLL(str(self.library_path))
        self._bind_api()
        major = ctypes.c_uint()
        minor = ctypes.c_uint()
        result = self._ks_version(ctypes.byref(major), ctypes.byref(minor))
        if result != (major.value << 8) + minor.value:
            raise RuntimeError("invalid Keystone version response")
        if (major.value, minor.value) != SUPPORTED_VERSION:
            raise RuntimeError(
                f"unsupported Keystone API version {major.value}.{minor.value}"
            )

    def _bind_api(self) -> None:
        self._ks_version = self._library.ks_version
        self._ks_version.argtypes = [
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        self._ks_version.restype = ctypes.c_uint

        self._ks_open = self._library.ks_open
        self._ks_open.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._ks_open.restype = ctypes.c_int

        self._ks_asm = self._library.ks_asm
        self._ks_asm.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self._ks_asm.restype = ctypes.c_int

        self._ks_close = self._library.ks_close
        self._ks_close.argtypes = [ctypes.c_void_p]
        self._ks_close.restype = ctypes.c_int

        self._ks_free = self._library.ks_free
        self._ks_free.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
        self._ks_free.restype = None

    def assemble(self, source: str, address: int) -> bytes:
        if not isinstance(source, str) or not source:
            raise ValueError("assembly source must be a non-empty string")
        if not isinstance(address, int) or not 0 <= address < 1 << 64:
            raise ValueError("assembly address must be a uint64")
        engine = ctypes.c_void_p()
        opened = self._ks_open(
            ARCH_ARM64,
            MODE_LITTLE_ENDIAN,
            ctypes.byref(engine),
        )
        if opened != 0 or not engine.value:
            raise RuntimeError(f"ks_open failed with error {opened}")
        encoding = ctypes.POINTER(ctypes.c_ubyte)()
        try:
            size = ctypes.c_size_t()
            count = ctypes.c_size_t()
            result = self._ks_asm(
                engine,
                source.encode("utf-8"),
                address,
                ctypes.byref(encoding),
                ctypes.byref(size),
                ctypes.byref(count),
            )
            if result != 0:
                raise RuntimeError(f"ks_asm failed with error {result}")
            if not encoding or size.value == 0:
                raise RuntimeError("ks_asm returned no instructions")
            return bytes(encoding[: size.value])
        finally:
            if encoding:
                self._ks_free(encoding)
            self._ks_close(engine)
