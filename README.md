# nPlayer iOS LibASS Bridge

> A small tribute to nPlayer, an exceptionally well-designed player that has served us reliably for years.

Replace the bundled libass stack in your own **nPlayer 3.13.0** install with
**libass 0.17.5** — 
no jailbreak, no inline hooks, bring modern ASS/SSA rendering to this great player.

> [!Warning]
>
> **Vibe Coding Project**

## What this does

`npa-patch` takes a decrypted nPlayer IPA you own and writes a patched copy:

- two existing guards are turned into NOPs, which fixes that only ASS/SSA of 
  the first video in the playback sequence can use font attachments in Container (e.g. Matroska).
- the fifteen libass entry points the app calls are redirected through a small
  payload, which loads `LibASSBridge.dylib` on first use and falls back to the
  app's own libass if that ever fails, 
- `Frameworks/LibASSBridge.dylib` is added. It statically links libass 0.17.5,
  FreeType, **HarfBuzz**, FriBidi, fontconfig and expat, with no third-party
  dynamic dependency,
- both binaries are pseudo-signed so the bundle loads.

The dylib is built from this repository; only the patch tooling and that dylib
are distributed. No nPlayer IPA is included.

Recommend to use with **nPlayerEnhance**, which unlock ASS/SSA animation framerate limits.

## Requirements

- macOS or Linux, Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), `ldid`, `zip`
  and `unzip`. Install ldid with `brew install ldid` on macOS; on Linux it is
  not always packaged (Ubuntu 24.04 does not ship it), so use a
  [prebuilt binary](https://github.com/ProcursusTeam/ldid/releases) or build it.
- Your own **decrypted** nPlayer 3.13.0 IPA. App Store packages are
  FairPlay-encrypted and are rejected on purpose; this project ships no IPA and
  no decryption.
- Two host files from the release assets: `LibASSBridge.dylib` (libass 0.17.5
  for iOS arm64) and `libkeystone.dylib` (the arm64 assembler used to encode the
  dispatch payload, macOS arm64 only). Both are host-side build products; on
  Linux, `make bootstrap` builds the assembler as `libkeystone.so` instead.
- No Xcode, no iOS SDK, no jailbreak. `npa-patch` runs from the repository
  checkout, next to `manifests/`.

## Quick start

```sh
git clone <this repository> && cd nplayer-libass-bridge
# put LibASSBridge.dylib and libkeystone.dylib from the release assets here
# (on Linux, run `make bootstrap` to build libkeystone.so instead)
uv run npa-patch "/path/to/nPlayer_3.13.0.ipa"
```
The output is written next to the input as
`nPlayer_3.13.0-libass0.17.5.ipa`. Install it with your usual sideload tool (
[TrollStore](https://github.com/opa334/TrollStore),
[SideStore](https://github.com/SideStore/SideStore),
[iloader](https://github.com/nab138/iloader) and more ) or [LiveContainer](https://github.com/LiveContainer/LiveContainer).

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

> [!Warning]
> Every result recorded before 2026-09-26 was produced with a dispatch payload
> that never activated: a successful `dladdr` was read as a failure and the
> basename scan stopped at the first slash, so the unit stayed on the app's own
> library. Those entries are void; see the `invalidated` section of
> `dev/acceptance.json`.

The dispatch fix (2026-09-26) makes the resolve block treat a `dladdr` success
as a success and compare the final path component of `dli_fname`. It is
verified under PlayCover on a Mac: with the fixed payload the unit state word
reads `NEW` and the `\kt` probe shows libass 0.17 behaviour (`\kt` only exists
from 0.17.0), where the bundled 0.13.7 ignores it. The standalone bridge smoke
app ends with `SMOKE: PASS`. `dev/acceptance.json` records the details and
`dev/plans/` holds the reverse-engineering plan behind the addresses.
Rendering differs pixel-wise from libass 0.13, which is expected.

The iOS-device gate for the fixed artifact is still open.

## Troubleshooting

| Message | Cause | Fix |
| --- | --- | --- |
| `still FairPlay-encrypted` | the IPA comes straight from the App Store | provide a decrypted dump of your own purchase |
| `no manifest matches this main executable` | wrong nPlayer version, or the IPA already has the patch | use a supported, clean dump |
| `bridge.exports` / `bridge.install_name` failed | wrong or stale dylib | use the `LibASSBridge.dylib` from the matching release |
| `ldid is required to assemble the IPA` | ldid is not installed | `brew install ldid`, or a prebuilt Linux ldid |
| `host assembler library is missing` | the host assembler is not in the checkout | run `make bootstrap`, or drop the `libkeystone.dylib` release asset on macOS |

## Rebuilding from source

`deps/sources.lock.json` pins every dependency (version, archive URL, SHA-256)
and `make bootstrap deps bridge` rebuilds the whole closure. That path needs
Xcode, cmake, ninja and meson; `dev/README.md` describes it, plus how to
re-check the frozen ABI and re-run the device acceptance.

## Legal

This toolchain is MIT licensed (see `LICENSE`); the third-party notices for the
statically linked libraries are in `THIRD-PARTY.md`. 

The project is not
affiliated with, or endorsed by, the nPlayer authors. You must own a licence
for nPlayer, and you should patch only your own copy.
