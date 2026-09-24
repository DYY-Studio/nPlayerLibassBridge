"""Patch one nPlayer IPA with the prebuilt LibASSBridge dylib.

The flow is linear and fails loudly: extract the executable, refuse an
encrypted dump, resolve the version by SHA-256, check the bridge contract,
freeze the layout, rewrite the call sites, assemble and pseudo-sign, then
verify the shipped pair before publishing it atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from . import macho, package, verify
from .manifest import select_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "manifests"
MAIN_MEMBER = package.MAIN_MEMBER


@dataclass(frozen=True)
class PatchResult:
    source: Path
    output: Path
    app_version: str
    libass_version: str
    source_main_sha256: str
    packaged_main_sha256: str
    bridge_sha256: str
    state_initial: int
    checks_passed: int

    def as_dict(self) -> dict:
        return {
            "source": str(self.source),
            "output": str(self.output),
            "app_version": self.app_version,
            "libass_version": self.libass_version,
            "source_main_sha256": self.source_main_sha256,
            "packaged_main_sha256": self.packaged_main_sha256,
            "bridge_sha256": self.bridge_sha256,
            "state_initial": self.state_initial,
            "checks_passed": self.checks_passed,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract_main(source: Path, destination: Path) -> Path:
    with ZipFile(source) as archive:
        if MAIN_MEMBER not in set(archive.namelist()):
            raise ValueError(f"{source.name} does not carry {MAIN_MEMBER}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.read(MAIN_MEMBER))
    return destination


def _reject_encrypted(main: Path) -> None:
    binary = macho.parse(main)
    if not binary.has_encryption_info or int(binary.encryption_info.crypt_id) != 0:
        raise ValueError(
            "this IPA is still FairPlay-encrypted; a decrypted dump of your own "
            "copy is required (crypt_id != 0)"
        )


def patch_ipa(
    source: Path | str,
    output: Path | str | None,
    bridge: Path | str,
    manifests: Path | str = MANIFESTS,
    work: Path | str | None = None,
) -> PatchResult:
    source = Path(source).resolve()
    bridge = Path(bridge).resolve()
    manifests = Path(manifests).resolve()
    for label, path in (("source IPA", source), ("bridge dylib", bridge)):
        if not path.is_file():
            raise FileNotFoundError(f"{label} is missing: {path}")
    if not manifests.is_dir():
        raise FileNotFoundError(f"manifest directory is missing: {manifests}")

    created_work = work is None
    work = Path(work) if work is not None else Path(tempfile.mkdtemp(prefix="npa-patch-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        source_main = _extract_main(source, work / "source-main")
        source_digest = _sha256(source_main)
        _reject_encrypted(source_main)
        manifest = select_manifest(manifests, source_main)
        macho.preflight(source_main, manifest)

        output_path = (
            Path(output).resolve()
            if output is not None
            else source.with_name(f"{source.stem}-libass{manifest.libass_version}.ipa")
        )
        if output_path == source:
            raise ValueError("refusing to overwrite the source IPA; pass -o")

        contract = verify.verify_bridge(bridge, manifest)
        contract.require()

        macho.phase_a(source_main, work / "main-phase-a", manifest)
        macho.phase_b(work / "main-phase-a", work / "main-phase-b", manifest)

        temporary = output_path.with_name(f".tmp-{output_path.name}")
        temporary.unlink(missing_ok=True)
        try:
            package.package_ipa(
                source, temporary, work / "main-phase-b", bridge, work=work / "package"
            )
            extracted = package.extract_for_verification(temporary, work / "shipped")
            report = verify.verify_artifact(
                source_main, extracted["main"], manifest, extracted["bridge"]
            )
            report.require()
            packaged_main_sha256 = _sha256(extracted["main"])
            bridge_sha256 = _sha256(extracted["bridge"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(output_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    finally:
        if created_work:
            shutil.rmtree(work, ignore_errors=True)

    return PatchResult(
        source=source,
        output=output_path,
        app_version=manifest.app_version,
        libass_version=manifest.libass_version,
        source_main_sha256=source_digest,
        packaged_main_sha256=packaged_main_sha256,
        bridge_sha256=bridge_sha256,
        state_initial=report.state_initial,
        checks_passed=len(report.checks),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="npa-patch",
        description="Patch a decrypted nPlayer IPA with the prebuilt LibASSBridge dylib.",
    )
    parser.add_argument("source", type=Path, help="your own decrypted nPlayer .ipa")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="where to write the patched IPA (default: next to the source)",
    )
    parser.add_argument(
        "--bridge",
        type=Path,
        default=Path.cwd() / "LibASSBridge.dylib",
        help="LibASSBridge.dylib from the release assets",
    )
    parser.add_argument("--manifests", type=Path, default=MANIFESTS)
    arguments = parser.parse_args(argv)
    try:
        result = patch_ipa(
            arguments.source,
            arguments.output,
            arguments.bridge,
            arguments.manifests,
        )
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    except Exception as error:  # noqa: BLE001
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
