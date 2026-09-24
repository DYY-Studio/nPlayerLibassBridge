# nPlayer LibASS Bridge

Replace the bundled libass stack in your own **nPlayer 3.13.0** install with
libass 0.17.5 — no jailbreak, no inline hooks. The patched IPA gets one added
dylib and sixteen redirected call sites; the app keeps calling the same fifteen
libass entry points, which now dispatch into the new library.

## What this does

`npa-patch` takes a decrypted nPlayer IPA you own and writes a patched copy:

- two existing guards are turned into NOPs,
- the fifteen libass entry points the app calls are redirected through a small
  payload, which loads `LibASSBridge.dylib` on first use and falls back to the
  app's own libass if that ever fails,
- `Frameworks/LibASSBridge.dylib` is added. It statically links libass 0.17.5,
  FreeType, HarfBuzz, FriBidi, fontconfig and expat, with no third-party
  dynamic dependency,
- both binaries are pseudo-signed so the bundle loads.

The dylib is built from this repository; only the patch tooling and that dylib
are distributed. No nPlayer IPA is included.

## Requirements

- macOS or Linux, Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), `ldid`
  (`brew install ldid`), `zip` and `unzip`.
- Your own **decrypted** nPlayer 3.13.0 IPA. App Store packages are
  FairPlay-encrypted and are rejected on purpose; this project ships no IPA and
  no decryption.
- No Xcode, no iOS SDK, no jailbreak.

## Quick start

```sh
git clone <this repository> && cd nplayer-libass-bridge
# put LibASSBridge.dylib from the release assets next to this README
uv run npa-patch "/path/to/nPlayer_3.13.0.ipa"
```
The output is written next to the input as
`nPlayer_3.13.0-libass0.17.5.ipa`. Install it with your usual sideload tool or
LiveContainer; that tool re-signs the whole bundle, which is expected.

Three options exist: `-o/--output`, `--bridge` (default `./LibASSBridge.dylib`)
and `--manifests` (default `manifests/`). `./npa-patch` at the repository root
and `uv run python tools/patch.py` are equivalent entry points. The command
prints a JSON summary with the input hash, output hash, bridge hash and the
number of verification checks that passed.

## Supported versions

Exactly the nPlayer versions listed in `manifests/`. The input executable is
matched by SHA-256 before anything is written, so an unsupported version, an
already-patched IPA and an encrypted package all fail with a named reason
instead of producing a broken bundle.

Adding a version means adding one manifest (addresses, call sites, frozen iOS
ABI). That work needs the binary analyzed; see `dev/README.md`.

## Verification status

The patched artifact was verified on a device (iPhone SE 3rd generation, iOS
17.7.2, installed through LiveContainer 3.7.2): the dispatch reaches the new
library, SRT and embedded ASS subtitles render, Matroska embedded fonts and the
font cache work, and continuous playback stays correct. The standalone bridge
smoke app ends with `SMOKE: PASS`. `dev/acceptance.json` records the details and
`dev/plans/` holds the reverse-engineering plan behind the addresses.
Rendering differs pixel-wise from libass 0.13, which is expected.

## Troubleshooting

| Message | Cause | Fix |
| --- | --- | --- |
| `still FairPlay-encrypted` | the IPA comes straight from the App Store | provide a decrypted dump of your own purchase |
| `no manifest matches this main executable` | wrong nPlayer version, or the IPA already has the patch | use a supported, clean dump |
| `bridge.exports` / `bridge.install_name` failed | wrong or stale dylib | use the `LibASSBridge.dylib` from the matching release |
| `ldid is required to assemble the IPA` | ldid is not installed | `brew install ldid` |

## Rebuilding from source

`deps/sources.lock.json` pins every dependency (version, archive URL, SHA-256)
and `make bootstrap deps bridge` rebuilds the whole closure. That path needs
Xcode, cmake, ninja and meson; `dev/README.md` describes it, plus how to
re-check the frozen ABI and re-run the device acceptance.

## Legal

This toolchain is MIT licensed (see `LICENSE`); the third-party notices for the
statically linked libraries are in `THIRD-PARTY.md`. The project is not
affiliated with, or endorsed by, the nPlayer authors. You must own a licence
for nPlayer, and you should patch only your own copy.
