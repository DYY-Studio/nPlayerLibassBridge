"""Package the pseudo-signed IPA variants.

Four variants exist: baseline (clean IPA plus the two NOP guards),
weak-load-only (frozen layout, no dispatch redirects), fallback (full
dispatch without the bridge) and bridge (full dispatch plus the dylib).
Every artifact is assembled in a scratch tree, signed with ldid and only
published after its contents were checked.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_IPA = ROOT.parent / "nPlayer_3.13.0.ipa"
APP_DIR = Path("Payload") / "nPlayer.app"
MAIN_MEMBER = (APP_DIR / "nPlayer").as_posix()
BRIDGE_MEMBER = (APP_DIR / "Frameworks" / "LibASSBridge.dylib").as_posix()
WORK_ROOT = ROOT / "build" / "package"
DIST = ROOT / "dist"
LINKEDIT = "ldid"
VARIANTS = ("baseline", "weak-load-only", "fallback", "bridge")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


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
    tool = shutil.which(LINKEDIT)
    if tool is None:
        raise RuntimeError("ldid is required to pseudo-sign the artifacts")
    _run([tool, "-S", path])


def extract_bundle(source_ipa: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _run(["/usr/bin/unzip", "-q", source_ipa, "-d", destination])
    app = destination / APP_DIR
    _require((app / "nPlayer").is_file(), f"source IPA has no {MAIN_MEMBER}")
    return app


def package_ipa(
    source_ipa: Path,
    output: Path,
    main: Path,
    bridge: Path | None = None,
) -> dict[str, Any]:
    """Assemble, pseudo-sign and publish one IPA variant."""

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    scratch = WORK_ROOT / f"tree-{output.name}"
    app = extract_bundle(source_ipa, scratch)
    executable = app / "nPlayer"
    shutil.copy2(main, executable)
    executable.chmod(0o755)
    sign(executable)
    if bridge is not None:
        frameworks = app / "Frameworks"
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
        _run(["/usr/bin/zip", "-q", "-r", "-y", temporary, *entries], cwd=scratch)
        report = inspect_ipa(temporary, bridge is not None)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, output)
    shutil.rmtree(scratch)
    report.update(
        {
            "artifact": str(output),
            "variant": output.stem,
            "mode": "bridge" if bridge is not None else "fallback",
            "main_sha256": _sha256(main),
            "bridge_sha256": _sha256(bridge) if bridge is not None else "",
        }
    )
    return report


def inspect_ipa(path: Path, expect_bridge: bool) -> dict[str, Any]:
    with ZipFile(path) as archive:
        names = archive.namelist()
    mains = [name for name in names if name == MAIN_MEMBER]
    bridges = [name for name in names if name == BRIDGE_MEMBER]
    _require(len(mains) == 1, f"{path.name} carries {len(mains)} main executables")
    expected_bridges = 1 if expect_bridge else 0
    _require(
        len(bridges) == expected_bridges,
        f"{path.name} carries {len(bridges)} bridge dylibs instead of {expected_bridges}",
    )
    return {"main_members": len(mains), "bridge_members": len(bridges)}


def publish(
    variant: str,
    source_ipa: Path,
    output: Path,
    main: Path,
    bridge: Path | None,
) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    _require(output.stem == variant, f"output name {output.name} does not match {variant}")
    return package_ipa(source_ipa, output, main, bridge)
