import hashlib
import unittest
from pathlib import Path
from zipfile import ZipFile

from npabridge.manifest import encode_bl, load_manifest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "manifests" / "nplayer-3.13.0.json"
BASELINE_IPA = REPOSITORY_ROOT.parent / "nPlayer_3.13.0.ipa"
MAIN_MEMBER = "Payload/nPlayer.app/nPlayer"
EXPECTED_API_SYMBOLS = frozenset(
    {
        "npa_ass_library_init",
        "npa_ass_set_extract_fonts",
        "npa_ass_set_message_cb",
        "npa_ass_renderer_init",
        "npa_ass_set_frame_size",
        "npa_ass_set_fonts_dir",
        "npa_ass_new_track",
        "npa_ass_process_codec_private",
        "npa_ass_process_data",
        "npa_ass_free_track",
        "npa_ass_flush_events",
        "npa_ass_render_frame",
        "npa_ass_renderer_done",
        "npa_ass_library_done",
        "npa_ass_set_fonts",
    }
)
EXPECTED_CALL_SITES = frozenset(
    {
        0x100A03F50,
        0x100A03FA0,
        0x100A03FB4,
        0x100A03FBC,
        0x100A03FC8,
        0x100A04680,
        0x100A047E8,
        0x100A04800,
        0x100A0482C,
        0x100A0529C,
        0x100A03B40,
        0x100A062F8,
        0x100A06408,
        0x100A035EC,
        0x100A035F4,
        0x100A0394C,
    }
)


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest(MANIFEST_PATH)
        with ZipFile(BASELINE_IPA) as archive:
            cls.main_bytes = archive.read(MAIN_MEMBER)

    def test_domain_has_fifteen_unique_apis(self):
        self.assertEqual(len(self.manifest.apis), 15)
        self.assertEqual(len({api.symbol for api in self.manifest.apis}), 15)

    def test_domain_contains_exact_api_symbol_set(self):
        symbols = {api.symbol for api in self.manifest.apis}
        self.assertEqual(symbols, EXPECTED_API_SYMBOLS)

    def test_domain_has_sixteen_callsites(self):
        sites = [site for api in self.manifest.apis for site in api.call_sites]
        self.assertEqual(len(sites), 16)

    def test_domain_contains_exact_unique_callsite_set(self):
        sites = [site for api in self.manifest.apis for site in api.call_sites]
        self.assertEqual(len(sites), len(set(sites)))
        self.assertEqual(set(sites), EXPECTED_CALL_SITES)

    def test_process_data_has_two_callsites(self):
        api = self.manifest.api("npa_ass_process_data")
        self.assertEqual(api.call_sites, (0x100A0482C, 0x100A0529C))

    def test_free_track_has_expected_callsite(self):
        api = self.manifest.api("npa_ass_free_track")
        self.assertEqual(api.call_sites, (0x100A03B40,))
        self.assertEqual(api.old_target, 0x100C0A2F4)

    def test_symbol_spellings_are_distinct(self):
        api = self.manifest.api("npa_ass_library_init")
        self.assertEqual(api.dlsym_name, "npa_ass_library_init")
        self.assertEqual(api.macho_name, "_npa_ass_library_init")

    def test_binary_baseline_metadata(self):
        self.assertEqual(self.manifest.imagebase, 0x100000000)
        self.assertEqual(
            self.manifest.main_sha256,
            "28e4a62ca87642338deeedbaf144bb8e4b3a801963abcdb59434aae88369b2b8",
        )
        self.assertEqual(self.manifest.dlsym_stub, 0x1011362CC)
        self.assertEqual(self.manifest.dladdr_stub, 0x10113629C)

    def test_bridge_and_callback_metadata(self):
        self.assertEqual(
            self.manifest.bridge_path,
            "@executable_path/Frameworks/LibASSBridge.dylib",
        )
        self.assertEqual(self.manifest.expected_bridge_basename, "LibASSBridge.dylib")
        self.assertEqual(self.manifest.callback.app_callback, 0x100A033C4)
        self.assertEqual(
            self.manifest.callback.prototype,
            "void(int, const char *, va_list, void *)",
        )
        self.assertEqual(self.manifest.callback.va_list_size, 8)
        self.assertEqual(self.manifest.callback.ignored_argument_register, "x3")

    def test_ipa_member_matches_manifest_hash(self):
        digest = hashlib.sha256(self.main_bytes).hexdigest()
        self.assertEqual(digest, self.manifest.main_sha256)

    def test_every_original_bl_word_matches_ipa(self):
        for api in self.manifest.apis:
            for call_site in api.call_sites:
                with self.subTest(symbol=api.symbol, call_site=call_site):
                    file_offset = call_site - self.manifest.imagebase
                    actual = int.from_bytes(
                        self.main_bytes[file_offset : file_offset + 4], "little"
                    )
                    self.assertEqual(actual, encode_bl(call_site, api.old_target))


if __name__ == "__main__":
    unittest.main()
