import hashlib
import shutil
import struct
import tempfile
import unittest
import warnings
from pathlib import Path
from zipfile import ZipFile

from npabridge import package, patch
from npabridge.manifest import select_manifest
from npabridge.verify import VerificationError

from support import ROOT, SOURCE_IPA


MANIFESTS = ROOT / "manifests"
BRIDGE = ROOT / "build" / "LibASSBridge.dylib"
# the main member of the device-accepted bridge.ipa, signed under the name nPlayer
PACKAGED_MAIN_SHA256 = "e84ef5b5e10cb10940ecffe73c3509f932a4aa6d2cba053052a7d9e7549792fe"


class PatchFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE_IPA.is_file():
            raise unittest.SkipTest("source IPA is not present")
        if not BRIDGE.is_file():
            raise unittest.SkipTest("LibASSBridge.dylib is not built")
        cls.work = ROOT / "build" / "patch" / "test"
        cls.work.mkdir(parents=True, exist_ok=True)

    def test_patch_writes_the_default_name_and_the_known_packaged_main(self):
        expected = SOURCE_IPA.with_name(f"{SOURCE_IPA.stem}-libass0.17.5.ipa")
        expected.unlink(missing_ok=True)
        try:
            result = patch.patch_ipa(
                SOURCE_IPA, None, BRIDGE, MANIFESTS, work=self.work / "run"
            )
            self.assertEqual(result.output, expected.resolve())
            self.assertEqual(result.app_version, "3.13.0")
            self.assertEqual(result.libass_version, "0.17.5")
            self.assertEqual(result.state_initial, 0)
            self.assertGreater(result.checks_passed, 0)
            with ZipFile(result.output) as archive:
                main = archive.read(patch.MAIN_MEMBER)
            self.assertEqual(hashlib.sha256(main).hexdigest(), PACKAGED_MAIN_SHA256)
        finally:
            expected.unlink(missing_ok=True)

    def test_default_work_directory_is_reported_and_cleaned_up(self):
        output = self.work / "temp-work.ipa"
        output.unlink(missing_ok=True)
        temp_root = Path(tempfile.gettempdir())
        before = set(temp_root.glob("npa-patch-*"))
        result = patch.patch_ipa(SOURCE_IPA, output, BRIDGE, MANIFESTS)
        self.assertEqual(set(temp_root.glob("npa-patch-*")), before)
        self.assertEqual(result.packaged_main_sha256, PACKAGED_MAIN_SHA256)
        self.assertTrue(output.is_file())

    def test_refuses_to_overwrite_the_bridge_dylib(self):
        dylib = self.work / "LibASSBridge.dylib"
        shutil.copy2(BRIDGE, dylib)
        before = hashlib.sha256(dylib.read_bytes()).hexdigest()
        with self.assertRaises(ValueError) as caught:
            patch.patch_ipa(
                SOURCE_IPA, dylib, dylib, MANIFESTS, work=self.work / "overwrite"
            )
        self.assertIn("bridge", str(caught.exception).lower())
        self.assertEqual(hashlib.sha256(dylib.read_bytes()).hexdigest(), before)

    def test_unsupported_version_lists_the_supported_one(self):
        unknown = self.work / "unknown-main"
        unknown.write_bytes(b"\x00" * 16)
        with self.assertRaises(ValueError) as caught:
            select_manifest(MANIFESTS, unknown)
        message = str(caught.exception)
        self.assertIn("3.13.0", message)
        self.assertIn(hashlib.sha256(b"\x00" * 16).hexdigest(), message)

    def test_encrypted_input_is_reported_as_encrypted(self):
        encrypted = self.work / "encrypted.ipa"
        self._write_ipa_with_crypt_id(SOURCE_IPA, encrypted, 1)
        output = self.work / "encrypted-out.ipa"
        with self.assertRaises(ValueError) as caught:
            patch.patch_ipa(
                encrypted, output, BRIDGE, MANIFESTS, work=self.work / "encrypted-work"
            )
        self.assertIn("encrypted", str(caught.exception).lower())
        self.assertFalse(output.exists())

    def test_invalid_bridge_is_rejected_by_name(self):
        not_a_bridge = package.extract_for_verification(
            SOURCE_IPA, self.work / "bad-bridge"
        )["main"]
        output = self.work / "bad-bridge.ipa"
        with self.assertRaises(VerificationError) as caught:
            patch.patch_ipa(
                SOURCE_IPA, output, not_a_bridge, MANIFESTS, work=self.work / "bad-work"
            )
        self.assertIn("bridge.exports", caught.exception.codes)
        self.assertFalse(output.exists())

    def _write_ipa_with_crypt_id(self, source: Path, output: Path, crypt_id: int) -> None:
        """Flip crypt_id in place so the input looks like an App Store package."""

        main = self.work / "encrypted-main"
        with ZipFile(source) as archive:
            main.write_bytes(archive.read(patch.MAIN_MEMBER))
        raw = bytearray(main.read_bytes())
        offset = 32  # sizeof(struct mach_header_64)
        while offset < len(raw) - 24:
            command, size = struct.unpack_from("<II", raw, offset)
            if command == 0x2C and size >= 24:  # LC_ENCRYPTION_INFO_64
                struct.pack_into("<I", raw, offset + 16, crypt_id)
                break
            self.assertGreaterEqual(size, 8, "malformed load command")
            offset += size
        else:
            self.fail("LC_ENCRYPTION_INFO_64 is missing")
        main.write_bytes(bytes(raw))
        shutil.copy2(source, output)
        # zipfile.read() resolves the last entry with a given name, so the
        # appended copy wins; the duplicate entry is deliberate
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with ZipFile(output, "a") as archive:
                archive.write(main, patch.MAIN_MEMBER)


if __name__ == "__main__":
    unittest.main()
