import os
import shutil
import tempfile
import unittest
import warnings
from unittest import mock
from pathlib import Path
from zipfile import ZipFile

from npabridge import package
from npabridge.macho import parse

from support import SOURCE_IPA

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "package"
MAIN = ROOT / "build" / "macho" / "main-phase-b"
BRIDGE = ROOT / "build" / "LibASSBridge.dylib"


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE_IPA.is_file():
            raise unittest.SkipTest("source IPA is not present")
        if not MAIN.is_file() or not BRIDGE.is_file():
            raise unittest.SkipTest("patch artifacts are not built")
        cls.artifact = BUILD / "test" / "patched.ipa"
        package.publish(SOURCE_IPA, cls.artifact, MAIN, BRIDGE)

    def test_frameworks_path_that_is_a_file_is_rejected(self):
        crafted = BUILD / "test" / "frameworks-file.ipa"
        crafted.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_IPA, crafted)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with ZipFile(crafted, "a") as archive:
                archive.writestr(f"{package.APP_DIR}/Frameworks", b"")
        output = BUILD / "test" / "frameworks-file-out.ipa"
        output.unlink(missing_ok=True)
        with self.assertRaises(ValueError) as caught:
            package.publish(crafted, output, MAIN, BRIDGE)
        self.assertIn("Frameworks", str(caught.exception))
        self.assertFalse(output.exists())

    def test_default_scratch_directory_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as isolated:
            with mock.patch.object(tempfile, "tempdir", isolated):
                output = BUILD / "test" / "scratch.ipa"
                package.publish(SOURCE_IPA, output, MAIN, BRIDGE)
                self.assertEqual(os.listdir(isolated), [])
                self.assertTrue(output.is_file())

    def test_artifact_carries_one_main_and_one_bridge(self):
        report = package.inspect_ipa(self.artifact)
        self.assertEqual(report, {"main_members": 1, "bridge_members": 1})
        with ZipFile(self.artifact) as archive:
            names = archive.namelist()
            self.assertIn(package.MAIN_MEMBER, names)
            self.assertIn(package.BRIDGE_MEMBER, names)
            main = archive.read(package.MAIN_MEMBER)
            bridge = archive.read(package.BRIDGE_MEMBER)
        for name, content in (("main", main), ("bridge", bridge)):
            target = BUILD / "test" / f"extracted-{name}"
            target.write_bytes(content)
            parsed = parse(target)
            with self.subTest(artifact=name):
                self.assertTrue(parsed.has_code_signature)
                self.assertEqual(parsed.build_version.minos[:2], [13, 0])


if __name__ == "__main__":
    unittest.main()
