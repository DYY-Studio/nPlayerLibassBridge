"""Package the standalone BridgeSmoke app.

Developer tool: only the smoke app needs this, and it needs Xcode plus the
iOS SDK, so it lives outside the public package.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from npabridge import macho
from npabridge.package import sign


ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = ROOT / "build" / "patch"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} is required to assemble the IPA")
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


def publish_app_bundle(bundle: Path, output: Path) -> dict[str, Any]:
    """Pseudo-sign, package and inspect a standalone app bundle."""

    binary = bundle / bundle.stem
    _require(binary.is_file(), f"app binary is missing: {binary}")
    _require((bundle / "Info.plist").is_file(), "app bundle has no Info.plist")
    embedded = bundle / "Frameworks" / "LibASSBridge.dylib"
    _require(embedded.is_file(), f"app bundle has no {embedded.name}")
    parsed = macho.parse(binary)
    _require(macho.enum_name(parsed.header.cpu_type).lower() == "arm64", "app is not arm64")
    minos = macho.version_tuple(parsed.build_version.minos)
    _require(minos[:2] == [13, 0], f"app target is not iOS 13: {minos}")
    sign(binary)
    sign(embedded)

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    scratch = WORK_ROOT / f"tree-{output.name}"
    if scratch.exists():
        shutil.rmtree(scratch)
    (scratch / "Payload").mkdir(parents=True)
    shutil.copytree(bundle, scratch / "Payload" / bundle.name, symlinks=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".tmp-{output.name}")
    temporary.unlink(missing_ok=True)
    entries = sorted(path.name for path in scratch.iterdir())
    try:
        _run([_tool("zip"), "-q", "-r", "-y", temporary, *entries], cwd=scratch)
        report = inspect_app_ipa(temporary, bundle.name, binary.name)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, output)
    shutil.rmtree(scratch)
    report.update(
        {
            "artifact": str(output),
            "variant": output.stem,
            "main_sha256": _sha256(binary),
            "bridge_sha256": _sha256(embedded),
        }
    )
    return report


def inspect_app_ipa(path: Path, bundle_name: str, binary_name: str) -> dict[str, Any]:
    app = (Path("Payload") / bundle_name).as_posix()
    main_member = f"{app}/{binary_name}"
    bridge_member = f"{app}/Frameworks/LibASSBridge.dylib"
    with ZipFile(path) as archive:
        names = archive.namelist()
    _require(names.count(main_member) == 1, f"{path.name} does not carry {main_member}")
    _require(names.count(bridge_member) == 1, f"{path.name} does not carry {bridge_member}")
    _require(f"{app}/Info.plist" in names, f"{path.name} does not carry Info.plist")
    return {"bundle": app, "main_members": 1, "bridge_members": 1}
