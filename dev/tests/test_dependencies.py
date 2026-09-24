import unittest
from pathlib import Path

from deps import build_deps


EXPECTED_CLOSURE = ("expat", "freetype", "harfbuzz", "fribidi", "fontconfig", "libass")


class DependencyLockTests(unittest.TestCase):
    def test_lock_covers_the_libass_closure_without_gperf(self):
        lock = build_deps.load_lock()
        self.assertNotIn("gperf", lock["sources"])
        self.assertNotIn("gperf", lock["build_order"])
        self.assertEqual(set(lock["build_order"]), set(EXPECTED_CLOSURE))
        closure = build_deps.dependency_closure(lock, "libass")
        self.assertEqual(
            closure,
            ["libass", "fontconfig", "freetype", "expat", "harfbuzz", "fribidi"],
        )
        self.assertEqual(
            build_deps.system_link_args(lock, "libass"),
            ["-liconv", "-lz"],
        )

    def test_cross_file_targets_ios13_arm64(self):
        build_deps.validate_cross_file()

    def test_built_closure_is_ios13_static_arm64(self):
        if not build_deps.LIB_ROOT.is_dir():
            self.skipTest("dependency closure is not built")
        report = build_deps.verify_closure()
        self.assertEqual(set(report["archives"]), set(EXPECTED_CLOSURE))
        self.assertEqual(report["path_hygiene"], "passed")
        for name, entry in report["archives"].items():
            path = build_deps.LIB_ROOT / Path(entry["path"]).name
            with self.subTest(archive=name):
                self.assertTrue(path.is_file())
                self.assertEqual(build_deps.sha256_file(path), entry["sha256"])


if __name__ == "__main__":
    unittest.main()
