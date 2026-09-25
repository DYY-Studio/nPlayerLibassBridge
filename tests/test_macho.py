import unittest
from pathlib import Path
from zipfile import ZipFile

from npabridge.macho import (
    DATA_BLOB_SIZE,
    IPA_MEMBER,
    SEGMENT_DATA,
    SEGMENT_TEXT,
    parse,
    phase_a,
    phase_b,
    section_bytes,
    snapshot,
)
from npabridge.manifest import load_manifest
from npabridge.payload import PayloadLayout, assemble_payload


from support import SOURCE_IPA

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = load_manifest(ROOT / "manifests/nplayer-3.13.0.json")
BUILD = ROOT / "build" / "macho"


class MachOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE_IPA.is_file():
            raise unittest.SkipTest("source IPA is not present")
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.baseline = BUILD / "test-clean-main"
        with ZipFile(SOURCE_IPA) as archive:
            cls.baseline.write_bytes(archive.read(IPA_MEMBER))
        cls.layout = BUILD / "test-phase-a"
        cls.patched = BUILD / "test-phase-b"
        cls.phase_a_report = phase_a(cls.baseline, cls.layout, MANIFEST)
        cls.phase_b_report = phase_b(cls.layout, cls.patched, MANIFEST)

    def test_phase_a_freezes_the_payload_segments(self):
        report = self.phase_a_report
        self.assertEqual(report["reserved_text"], 7536)
        self.assertEqual(report["dylib_ordinals"][-1][0], MANIFEST.bridge_path)
        after = parse(self.layout)
        text = after.get_segment(SEGMENT_TEXT)
        data = after.get_segment(SEGMENT_DATA)
        self.assertEqual(int(text.virtual_address), report["text_vmaddr"])
        self.assertEqual(int(data.virtual_address), report["data_vmaddr"])
        self.assertEqual(after.segments[-1].name, "__LINKEDIT")

    def test_phase_a_preserves_semantics(self):
        before = snapshot(parse(self.baseline))
        after = snapshot(parse(self.layout))
        self.assertEqual(before.section_vas, after.section_vas)
        self.assertEqual(before.entrypoint, after.entrypoint)
        self.assertEqual(before.bind_targets, after.bind_targets)
        self.assertEqual(before.lazy_targets, after.lazy_targets)
        self.assertEqual(before.export_symbols, after.export_symbols)
        self.assertEqual(before.dylib_ordinals, after.dylib_ordinals[:-1])
        moved = {
            name
            for name, address in before.segment_vas.items()
            if after.segment_vas.get(name) != address
        }
        self.assertEqual(moved, {"__LINKEDIT"})

    def test_phase_b_changes_only_the_frozen_sites(self):
        self.assertEqual(self.phase_b_report["patched_call_sites"], 16)
        before = parse(self.layout)
        after = parse(self.patched)
        self.assertEqual(snapshot(before).segment_vas, snapshot(after).segment_vas)
        self.assertEqual(snapshot(before).section_vas, snapshot(after).section_vas)
        self.assertEqual(self.layout.stat().st_size, self.patched.stat().st_size)
        old_sections = section_bytes(before)
        new_sections = section_bytes(after)
        changed = 0
        for name, old in old_sections.items():
            if name == "__TEXT,__text":
                changed += sum(
                    1
                    for index in range(0, len(old) - 3, 4)
                    if old[index : index + 4] != new_sections[name][index : index + 4]
                )
            else:
                self.assertEqual(old, new_sections[name], name)
        self.assertEqual(changed, 18)

    def test_phase_b_writes_the_assembled_payload(self):
        layout = parse(self.layout)
        text = layout.get_segment(SEGMENT_TEXT)
        data = layout.get_segment(SEGMENT_DATA)
        payload = assemble_payload(
            PayloadLayout(
                text_vmaddr=int(text.virtual_address),
                data_vmaddr=int(data.virtual_address),
                state_rva=0,
                slots_rva=8,
            ),
            MANIFEST,
            MANIFEST.target_abi,
        )
        raw = self.patched.read_bytes()
        start = int(text.file_offset)
        self.assertEqual(raw[start : start + len(payload.text)], payload.text)
        blob = raw[int(data.file_offset) : int(data.file_offset) + DATA_BLOB_SIZE]
        self.assertEqual(blob, payload.data)


if __name__ == "__main__":
    unittest.main()
