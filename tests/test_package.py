import unittest
from pathlib import Path
from zipfile import ZipFile

from npabridge import package
from npabridge.macho import parse


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "package"
MAIN = ROOT / "build" / "macho" / "main-phase-b"
BRIDGE = ROOT / "build" / "LibASSBridge.dylib"


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MAIN.is_file() or not BRIDGE.is_file():
            raise unittest.SkipTest("patch artifacts are not built")
        cls.artifact = BUILD / "test" / "bridge.ipa"
        package.publish("bridge", package.SOURCE_IPA, cls.artifact, MAIN, BRIDGE)

    def test_bridge_variant_carries_one_main_and_one_bridge(self):
        report = package.inspect_ipa(self.artifact, expect_bridge=True)
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

    def test_main_only_expectation_is_rejected(self):
        with self.assertRaises(ValueError):
            package.inspect_ipa(self.artifact, expect_bridge=False)


if __name__ == "__main__":
    unittest.main()
