import hashlib
import shutil
import struct
import unittest
from pathlib import Path
from zipfile import ZipFile

from npabridge import package, patch
from npabridge.manifest import select_manifest
from npabridge.verify import VerificationError

from support import ROOT, SOURCE_IPA


MANIFESTS = ROOT / "manifests"
BRIDGE = ROOT / "build" / "LibASSBridge.dylib"
# the main member of the device-accepted bridge.ipa, signed under the name nPlayer
PACKAGED_MAIN_SHA256 = "19d3447193bcd66e03b850876a1281c4bceac087dd50cf6db534e0527fb3a887"


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
        # appended copy wins
        with ZipFile(output, "a") as archive:
            archive.write(main, patch.MAIN_MEMBER)


if __name__ == "__main__":
    unittest.main()
