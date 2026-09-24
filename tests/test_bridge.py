import unittest
from pathlib import Path

from npabridge.build_bridge import OUTPUT, nm_exports, verify_bridge
from npabridge.manifest import load_manifest


MANIFEST = load_manifest(Path(__file__).resolve().parents[1] / "manifests/nplayer-3.13.0.json")
EXPECTED_BRIDGE_SYMBOLS = tuple(api.symbol for api in MANIFEST.apis)


class BridgeTests(unittest.TestCase):
    def test_exports_use_macho_underscore(self):
        if not OUTPUT.is_file():
            self.skipTest("LibASSBridge.dylib is not built")
        self.assertEqual(
            set(nm_exports(OUTPUT)),
            {f"_{name}" for name in EXPECTED_BRIDGE_SYMBOLS},
        )

    def test_dlsym_manifest_has_no_underscore(self):
        for name in EXPECTED_BRIDGE_SYMBOLS:
            with self.subTest(symbol=name):
                self.assertFalse(name.startswith("_"))

    def test_bridge_is_ios13_arm64_without_initializers(self):
        if not OUTPUT.is_file():
            self.skipTest("LibASSBridge.dylib is not built")
        report = verify_bridge(OUTPUT)
        self.assertEqual(report["minos"][:2], [13, 0])
        self.assertEqual(report["mod_init_size"], 0)
        self.assertEqual(report["mod_term_size"], 0)
        self.assertEqual(report["global_initializers"], [])
        self.assertTrue(report["dependencies"])
        for dependency in report["dependencies"]:
            self.assertTrue(dependency.startswith("/usr/lib/"), dependency)


if __name__ == "__main__":
    unittest.main()
