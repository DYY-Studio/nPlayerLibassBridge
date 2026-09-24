"""Assemble and pseudo-sign a patched IPA.

One artifact exists: a patched main plus the LibASSBridge dylib. Every
artifact is assembled in a scratch tree, signed with ldid and only
published after its contents were checked.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path("Payload") / "nPlayer.app"
MAIN_MEMBER = (APP_DIR / "nPlayer").as_posix()
BRIDGE_MEMBER = (APP_DIR / "Frameworks" / "LibASSBridge.dylib").as_posix()
LINKEDIT = "ldid"
TOOL_HINTS = {
    "ldid": "brew install ldid",
    "zip": "macOS ships /usr/bin/zip; on Linux install zip",
    "unzip": "macOS ships /usr/bin/unzip; on Linux install unzip",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} is required to assemble the IPA ({TOOL_HINTS[name]})")
    return path


def _run(command: list[object], cwd: Path | None = None) -> None:
    subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd is not None else None,
        check=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sign(path: Path) -> None:
    _run([_tool(LINKEDIT), "-S", path])


def extract_bundle(source_ipa: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _run([_tool("unzip"), "-q", source_ipa, "-d", destination])
    app = destination / APP_DIR
    _require((app / "nPlayer").is_file(), f"source IPA has no {MAIN_MEMBER}")
    return app


def package_ipa(
    source_ipa: Path,
    output: Path,
    main: Path,
    bridge: Path,
    work: Path | None = None,
) -> dict[str, Any]:
    """Assemble, pseudo-sign and publish one patched IPA."""

    created_scratch = work is None
    scratch_root = Path(work) if work is not None else Path(tempfile.mkdtemp(prefix="npa-patch-"))
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
        scratch = scratch_root / f"tree-{output.name}"
        app = extract_bundle(source_ipa, scratch)
        executable = app / "nPlayer"
        shutil.copy2(main, executable)
        executable.chmod(0o755)
        sign(executable)
        frameworks = app / "Frameworks"
        if frameworks.exists() and not frameworks.is_dir():
            raise ValueError(
                f"source IPA carries {frameworks.name} as a file, not a directory"
            )
        frameworks.mkdir(exist_ok=True)
        target = frameworks / "LibASSBridge.dylib"
        shutil.copy2(bridge, target)
        target.chmod(0o755)
        sign(target)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".tmp-{output.name}")
        temporary.unlink(missing_ok=True)
        entries = sorted(path.name for path in scratch.iterdir())
        try:
            _run([_tool("zip"), "-q", "-r", "-y", temporary, *entries], cwd=scratch)
            report = inspect_ipa(temporary)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, output)
        shutil.rmtree(scratch)
    finally:
        if created_scratch:
            shutil.rmtree(scratch_root, ignore_errors=True)
    report.update(
        {
            "artifact": str(output),
            "main_sha256": _sha256(main),
            "bridge_sha256": _sha256(bridge),
        }
    )
    return report


def inspect_ipa(path: Path) -> dict[str, Any]:
    with ZipFile(path) as archive:
        names = archive.namelist()
    mains = [name for name in names if name == MAIN_MEMBER]
    bridges = [name for name in names if name == BRIDGE_MEMBER]
    _require(len(mains) == 1, f"{path.name} carries {len(mains)} main executables")
    _require(len(bridges) == 1, f"{path.name} carries {len(bridges)} bridge dylibs")
    return {"main_members": len(mains), "bridge_members": len(bridges)}


def extract_for_verification(ipa: Path, destination: Path) -> dict[str, Path]:
    """Extract the shipped executable and bridge from one packaged IPA."""

    with ZipFile(ipa) as archive:
        names = set(archive.namelist())
        if MAIN_MEMBER not in names:
            raise ValueError(f"{ipa.name} does not carry {MAIN_MEMBER}")
        destination.mkdir(parents=True, exist_ok=True)
        main = destination / "nPlayer"
        main.write_bytes(archive.read(MAIN_MEMBER))
        extracted = {"main": main}
        if BRIDGE_MEMBER in names:
            bridge = destination / "LibASSBridge.dylib"
            bridge.write_bytes(archive.read(BRIDGE_MEMBER))
            extracted["bridge"] = bridge
    return extracted


def publish(
    source_ipa: Path,
    output: Path,
    main: Path,
    bridge: Path,
) -> dict[str, Any]:
    return package_ipa(source_ipa, output, main, bridge)
