import struct
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from npabridge.manifest import load_manifest
from npabridge.payload import (
    Payload,
    PayloadLayout,
    _BASENAME_SCAN_SIZE,
    _RESOLVE_DLADDR_CHECK,
    assemble_payload,
    measure_payload,
)
from npabridge.target_abi import TargetABI


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = load_manifest(ROOT / "manifests/nplayer-3.13.0.json")
ABI = TargetABI(
    platform="IOS",
    minos=(13, 0, 0),
    sdk=(26, 2, 0),
    dl_info_size=32,
    dl_info_fname_offset=0,
    dl_info_fbase_offset=8,
    dl_info_sname_offset=16,
    dl_info_saddr_offset=24,
    rtld_default_masked=(1 << 64) - 2,
)


def _layout(text_vmaddr=0x101200000, data_vmaddr=0x101210000):
    return PayloadLayout(
        text_vmaddr=text_vmaddr,
        data_vmaddr=data_vmaddr,
        state_rva=0,
        slots_rva=8,
    )


def _sign_extend(value, bits):
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def _all_branch_targets(code, layout):
    result = []
    for offset in range(0, len(code) - 3, 4):
        word = struct.unpack_from("<I", code, offset)[0]
        if word & 0xFC000000 in (0x14000000, 0x94000000):
            displacement = _sign_extend((word & 0x03FFFFFF) << 2, 28)
            result.append(
                (offset, layout.text_vmaddr + offset + displacement, word & 0xFC000000)
            )
    return result


class PayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = _layout()
        cls.payload = assemble_payload(cls.layout, MANIFEST, ABI)

    def test_public_dataclasses_are_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            self.payload.text = b"tampered"
        with self.assertRaises(FrozenInstanceError):
            self.layout.text_vmaddr = 1

    def test_measure_matches_text_and_is_va_independent(self):
        other = _layout(0x101300000, 0x101310000)
        self.assertEqual(measure_payload(self.layout, MANIFEST, ABI), len(self.payload.text))
        self.assertEqual(measure_payload(other, MANIFEST, ABI), len(self.payload.text))
        other_payload = assemble_payload(other, MANIFEST, ABI)
        self.assertEqual(len(other_payload.text), len(self.payload.text))
        self.assertEqual(other_payload.data, self.payload.data)

    def test_data_contains_only_state_padding_and_null_slots(self):
        self.assertEqual(len(self.payload.data), 128)
        self.assertEqual(self.payload.data, b"\x00" * 128)
        self.assertEqual(struct.unpack_from("<I", self.payload.data, 4)[0], 0)
        self.assertEqual(len(self.payload.data[8:]) // 8, 15)

    def test_symbols_cover_fifteen_veneers_and_sixteen_callsites(self):
        veneers = {
            name: address
            for name, address in self.payload.symbols.items()
            if name.startswith("veneer_")
        }
        self.assertEqual(
            set(veneers),
            {f"veneer_{api.symbol}" for api in MANIFEST.apis},
        )
        callsites = {
            name: address
            for name, address in self.payload.symbols.items()
            if name.startswith("callsite_")
        }
        self.assertEqual(len(callsites), 16)
        self.assertEqual(
            set(callsites),
            {f"callsite_{site:#x}" for api in MANIFEST.apis for site in api.call_sites},
        )
        process = self.payload.symbols["veneer_npa_ass_process_data"]
        self.assertEqual(self.payload.symbols["callsite_0x100a0482c"], process)
        self.assertEqual(self.payload.symbols["callsite_0x100a0529c"], process)

    def test_stub_addresses_are_exposed_without_relocation(self):
        self.assertEqual(self.payload.stubs["dlsym"], MANIFEST.dlsym_stub)
        self.assertEqual(self.payload.stubs["dladdr"], MANIFEST.dladdr_stub)

    def test_all_veneer_old_branches_and_stub_calls_are_in_range(self):
        branches = _all_branch_targets(self.payload.text, self.layout)
        old_targets = {api.old_target for api in MANIFEST.apis}
        self.assertEqual(
            sum(target in old_targets for _, target, _ in branches), len(MANIFEST.apis)
        )
        self.assertEqual(
            sum(target == MANIFEST.dlsym_stub for _, target, _ in branches), len(MANIFEST.apis)
        )
        self.assertEqual(
            sum(target == MANIFEST.dladdr_stub for _, target, _ in branches), len(MANIFEST.apis)
        )
        for offset, target, _ in branches:
            self.assertLessEqual(
                abs(target - (self.layout.text_vmaddr + offset)),
                1 << 27,
                f"branch at {offset:#x} targets {target:#x}",
            )

    def test_free_track_has_exact_old_target_branch(self):
        offset = self.payload.symbols["veneer_npa_ass_free_track"] - self.layout.text_vmaddr
        matches = [
            target
            for branch_offset, target, opcode in _all_branch_targets(
                self.payload.text, self.layout
            )
            if offset <= branch_offset < offset + 0x40 and opcode == 0x14000000
        ]
        self.assertIn(0x100C0A2F4, matches)

    def test_encoded_state_machine_shape(self):
        code_end = self.payload.symbols["string_npa_ass_library_init"] - self.layout.text_vmaddr
        words = [
            struct.unpack_from("<I", self.payload.text, offset)[0]
            for offset in range(0, code_end, 4)
        ]
        self.assertEqual(words.count(0x88DFFD10), 16)  # ldar w16, [x8]
        self.assertEqual(words.count(0x885FFD10), 1)  # ldaxr w16, [x8]
        self.assertEqual(words.count(0x88097D11), 1)  # stxr w9, w17, [x8]
        self.assertEqual(words.count(0x889FFD10), 2)  # stlr w16, [x8]
        self.assertEqual(words.count(0xD503203F), 1)  # yield

    def test_text_has_no_pointer_literal_or_literal_pool(self):
        code_end = self.payload.symbols["string_npa_ass_library_init"] - self.layout.text_vmaddr
        for offset in range(0, code_end, 4):
            word = struct.unpack_from("<I", self.payload.text, offset)[0]
            self.assertNotIn(
                word & 0xFF800000,
                (0xD2800000, 0xF2800000),
                f"64-bit MOVZ/MOVK literal at {offset:#x}",
            )
            self.assertNotIn(
                word & 0xFF000000,
                (0x18000000, 0x58000000),
                f"LDR literal pool access at {offset:#x}",
            )

    def test_generation_is_deterministic(self):
        again = assemble_payload(self.layout, MANIFEST, ABI)
        self.assertEqual(again.text, self.payload.text)
        self.assertEqual(again.data, self.payload.data)
        self.assertEqual(again.symbols, self.payload.symbols)
        self.assertEqual(again.stubs, self.payload.stubs)

    def test_out_of_range_branch_is_rejected_before_returning_bytes(self):
        far = _layout(0x201200000, 0x201210000)
        with self.assertRaises(ValueError):
            assemble_payload(far, MANIFEST, ABI)

    def test_payload_constructor_accepts_declared_interface(self):
        payload = Payload(text=b"text", data=b"data", symbols={}, stubs={})
        self.assertEqual(payload.text, b"text")
        self.assertEqual(payload.data, b"data")
        self.assertEqual(payload.symbols, {})
        self.assertEqual(payload.stubs, {})

    def test_resolve_check_treats_dladdr_success_as_success(self):
        """dladdr returns non-zero on success; a zero result is the fallback."""

        publish_old = self.payload.symbols["publish_old"]
        for api in MANIFEST.apis:
            with self.subTest(symbol=api.symbol):
                resolve = self.payload.symbols[f"resolve_{api.symbol}"]
                offset = resolve - self.layout.text_vmaddr + _RESOLVE_DLADDR_CHECK
                word = struct.unpack_from("<I", self.payload.text, offset)[0]
                self.assertEqual(word & 0xFF00001F, 0x34000000)  # cbz w0
                displacement = (word >> 5) & 0x7FFFF
                if displacement & (1 << 18):
                    displacement -= 1 << 19
                self.assertEqual(
                    resolve + _RESOLVE_DLADDR_CHECK + (displacement << 2),
                    publish_old,
                )

    def test_basename_scan_reads_the_whole_path(self):
        """The scan must reach the NUL, not stop at the first slash."""

        for api in MANIFEST.apis:
            with self.subTest(symbol=api.symbol):
                scan = self.payload.symbols[f"basename_scan_{api.symbol}"]
                suffix = self.payload.symbols[f"basename_suffix_{api.symbol}"]
                self.assertEqual(suffix - scan, _BASENAME_SCAN_SIZE)


if __name__ == "__main__":
    unittest.main()
