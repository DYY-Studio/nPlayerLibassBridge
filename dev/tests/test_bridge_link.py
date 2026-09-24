import unittest
from pathlib import Path

from npabridge import build_bridge, macho
from npabridge.manifest import load_manifest
from npabridge.verify import VerificationError, verify_bridge


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = load_manifest(ROOT / "manifests/nplayer-3.13.0.json")


class BridgeLinkTests(unittest.TestCase):
    """Developer test: it needs clang and the iOS SDK to relink a dylib."""

    @classmethod
    def setUpClass(cls):
        try:
            macho.sdk_path()
        except Exception as error:  # noqa: BLE001
            raise unittest.SkipTest(f"iOS SDK is not available: {error}")
        if not build_bridge.load_closure()[0]:
            raise unittest.SkipTest("dependency closure is not built")

    def test_extra_export_is_rejected(self):
        exports = (ROOT / "bridge" / "bridge.exports").read_text(encoding="utf-8")
        mutated_exports = ROOT / "build" / "macho" / "mutated.exports"
        mutated_exports.parent.mkdir(parents=True, exist_ok=True)
        mutated_exports.write_text(exports + "_ass_library_init\n", encoding="utf-8")
        archives, link_args = build_bridge.load_closure()
        mutated = ROOT / "build" / "macho" / "mutated-bridge.dylib"
        build_bridge.link_bridge(
            macho.sdk_path(),
            archives,
            link_args,
            mutated,
            export_list=mutated_exports,
        )
        self.assertEqual(len(macho.exported_symbols(macho.parse(mutated))), 16)
        report = verify_bridge(mutated, MANIFEST)
        with self.assertRaises(VerificationError) as caught:
            report.require()
        self.assertIn("bridge.exports", caught.exception.codes)


if __name__ == "__main__":
    unittest.main()
