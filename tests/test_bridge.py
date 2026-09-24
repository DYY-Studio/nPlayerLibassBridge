import unittest
from pathlib import Path
from unittest import mock

from npabridge import macho
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

    def test_metadata_helpers_match_the_built_bridge(self):
        if not OUTPUT.is_file():
            self.skipTest("LibASSBridge.dylib is not built")
        with mock.patch.object(
            macho.subprocess, "run", side_effect=AssertionError("shelled out")
        ):
            self.assertEqual(macho.install_name(OUTPUT), "@rpath/LibASSBridge.dylib")
            self.assertEqual(
                macho.dependency_lines(OUTPUT),
                [
                    "@rpath/LibASSBridge.dylib",
                    "/usr/lib/libiconv.2.dylib",
                    "/usr/lib/libz.1.dylib",
                    "/usr/lib/libSystem.B.dylib",
                ],
            )

    def test_bridge_passes_every_artifact_check(self):
        if not OUTPUT.is_file():
            self.skipTest("LibASSBridge.dylib is not built")
        report = verify_bridge(OUTPUT)
        codes = [check["code"] for check in report["checks"]]
        self.assertEqual(
            codes,
            [
                "bridge.target",
                "bridge.exports",
                "bridge.install_name",
                "bridge.dependencies",
                "bridge.initializers",
                "bridge.host_paths",
                "bridge.callback",
            ],
        )
        self.assertTrue(all(check["ok"] for check in report["checks"]))


if __name__ == "__main__":
    unittest.main()
