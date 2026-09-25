# Developer material

Everything here exists to build or re-verify the bridge, not to patch an IPA.
The public flow (`npa-patch`) never touches this directory.

- `smoke/` + `tools/smoke.py` — the BridgeSmoke app that proves the 15-export
  ABI contract on a device. Requires Xcode and the iOS SDK.
- `smoke_package.py` — pseudo-signs and packages that app bundle.
- `tools/phase_a.py`, `tools/phase_b.py` — run the two layout stages separately
  to isolate a failure. `npa-patch` runs the same functions in one pass.
- `abi_probe.py` + `target_abi_probe.c` — compile a probe against the iOS SDK
  and print the constants that `manifests/*.json` freezes in `target_abi`.
  Run it after an SDK change and compare with the manifest.
- `acceptance.json` — the recorded device acceptance (iPhone SE 3rd gen,
  iOS 17.7.2, LiveContainer 3.7.2).
- `plans/` — the research plan that produced this toolchain.
- `tests/` — developer tests (dependency closure, Keystone/probe, relinking a
  bridge with an extra export). Run them explicitly: `uv run pytest dev/tests`.
  The default `uv run pytest` only covers the public suite.

## Rebuild everything from source

```sh
uv sync --frozen --all-groups
make bootstrap deps bridge      # host Keystone, iOS dependency closure, bridge dylib
make verify test
```

`deps/sources.lock.json` pins every dependency (version, archive URL, SHA-256);
the closure is built with `deps/ios-arm64.cross` and `deps/macos-arm64.native`.
`deps/` is optional for users: the release ships the built `LibASSBridge.dylib`.

## Re-run the device acceptance

1. `make bridge smoke`, then install `dist/smoke.ipa` on a device and read the
   on-screen log; the last line must be `SMOKE: PASS`.
2. Patch your own decrypted IPA with the release dylib and walk the subtitle
   matrix in `plans/2026-09-24-libass-bridge-prototype.md` (Task 11, Step 2-4):
   SRT, embedded ASS, Matroska embedded fonts, the font cache, seek/flush and
   continuous playback.
3. Append the device, iOS version, install method and both results to
   `acceptance.json`.

The expected packaged main hashes after the 2026-09-26 fixes are
`e84ef5b5e10cb10940ecffe73c3509f932a4aa6d2cba053052a7d9e7549792fe` for
`--dylib libass`, `a5243f0a36baf5ef5209d51f312bd9d6f0c8d3b05d4053fcbbaa48735339f83b`
for `--dylib ffmpeg` and
`3bee29d20c4cc6e5979f594dc8df34a6c0fd96e48240e1c2d6a0065fe69be810` for the
default selection. The pre-fix value
`19d3447193bcd66e03b850876a1281c4bceac087dd50cf6db534e0527fb3a887` is void: that
payload never activated the bridge (see `acceptance.json`).

## Release checklist

1. `make bridge` and `make verify`.
2. Publish `build/LibASSBridge.dylib` and `libkeystone.dylib` as release assets
   together with their SHA-256, plus `LICENSE` and `THIRD-PARTY.md`. The two
   binaries are host-side products; `make bootstrap` reproduces the assembler.
3. When any pinned dependency version changes, update `THIRD-PARTY.md` and the
   matching `dylibs[].library_version` in the manifest in the same commit.
4. Run `uv run python dev/abi_probe.py` after an iOS SDK change and re-check
   the manifest's `target_abi` block.
