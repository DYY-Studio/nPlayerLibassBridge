# nPlayer iOS Bridge

> A small tribute to nPlayer, an exceptionally well-designed player that has served us reliably for years.

Replace the bundled libass stack in your own **nPlayer 3.13.0** install with
**libass 0.17.5**, and its scaler/resampler with **FFmpeg 9.0.2** —
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
- the nineteen `sws_*`/`swr_*` entry points are redirected the same way to
  `LibFFmpegBridge.dylib` (a `--disable-everything` FFmpeg 9.0.2 build of
  libavutil + libswscale + libswresample), which falls back to the app's own
  FFmpeg 4.4 on any failure,
- `Frameworks/LibASSBridge.dylib` is added. It statically links libass 0.17.5,
  FreeType, **HarfBuzz**, FriBidi, fontconfig and expat, with no third-party
  dynamic dependency,
- `Frameworks/LibFFmpegBridge.dylib` is added when the FFmpeg part is
  selected. It statically links the three FFmpeg libraries above and nothing
  else,
- every binary is pseudo-signed so the bundle loads.

The patch is organised in **units**: one unit per library domain (libass,
libswscale, libswresample). Each unit arbitrates its own state at first call
and falls back on its own, so a failure in one never turns off another. A unit
is only installed when its dylib is selected, so `npa-patch --dylib libass`
produces an artifact that is byte-identical to the libass-only patch of the
same input.

The dylibs are built from this repository; only the patch tooling and those
dylibs are distributed. No nPlayer IPA is included.

Recommend to use with **nPlayerEnhance**, which unlock ASS/SSA animation framerate limits.

## Requirements

- macOS or Linux, Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), `ldid`
  (`brew install ldid`; on Linux use your distribution's ldid build), `zip` and
  `unzip`.
- Your own **decrypted** nPlayer 3.13.0 IPA. App Store packages are
  FairPlay-encrypted and are rejected on purpose; this project ships no IPA and
  no decryption.
- Two host files from the release assets: `LibASSBridge.dylib` (libass 0.17.5
  for iOS arm64) and `libkeystone.dylib` (the arm64 assembler used to encode the
  dispatch payload), plus `LibFFmpegBridge.dylib` (FFmpeg 9.0.2 for iOS arm64)
  when you want its units. All are host-side build products; `make bootstrap`
  builds the assembler and `make bridge` builds both dylibs locally if you
  prefer that.
- No Xcode, no iOS SDK, no jailbreak. `npa-patch` runs from the repository
  checkout, next to `manifests/`.

## Quick start

```sh
git clone <this repository> && cd nplayer-libass-bridge
# put LibASSBridge.dylib, LibFFmpegBridge.dylib and libkeystone.dylib
# from the release assets here
uv run npa-patch "/path/to/nPlayer_3.13.0.ipa"
```
The output is written next to the input as
`nPlayer_3.13.0-libass0.17.5-ffmpeg9.0.2.ipa`, one `<id><version>` segment per
installed dylib in manifest order. Install it with your usual sideload tool (
[TrollStore](https://github.com/opa334/TrollStore),
[SideStore](https://github.com/SideStore/SideStore),
[iloader](https://github.com/nab138/iloader) and more ) or [LiveContainer](https://github.com/LiveContainer/LiveContainer).

Three options exist: `-o/--output`, `--dylib <id>` (repeatable, default: every
dylib the manifest declares) and `--dylibs-dir <dir>` (default: the working
directory; each dylib is looked up as `<dir>/<basename>`). `--manifests`
(default `manifests/`) selects the manifest directory. `./npa-patch` at the
repository root and `uv run python tools/patch.py` are equivalent entry points.
The command prints a JSON summary with the input hash, output hashes, one hash
per shipped dylib, and the number of verification checks that passed.

Select exactly what you want:

```sh
uv run npa-patch --dylib libass "/path/to/nPlayer_3.13.0.ipa"   # subtitles only
uv run npa-patch --dylib ffmpeg "/path/to/nPlayer_3.13.0.ipa"   # scaler/resampler only
```
A missing or stale dylib is a hard error; a unit only ever falls back at runtime
when the dylib it needs fails to load or fails its identity check.

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
> basename scan stopped at the first slash, so each unit stayed on the app's own
> library. Those entries are void; see the `invalidated` section of
> `dev/acceptance.json`.

The dispatch fix (2026-09-26) makes the resolve block treat a `dladdr` success
as a success and compare the final path component of `dli_fname`. Both units are
now verified on a device (iPhone SE 3rd generation, iOS 17.7.2, LiveContainer
3.7.2): with the fixed payload every unit state word reads `NEW` and the `\kt`
probe renders libass 0.17 behaviour (`\kt` only exists from 0.17.0), where the
bundled 0.13.7 ignores it. The standalone bridge smoke app ends with
`SMOKE: PASS`. `dev/acceptance.json` records the details and `dev/plans/` holds
the reverse-engineering plan behind the addresses. Rendering differs pixel-wise
from libass 0.13, which is expected.

The FFmpeg unit needed one more fix before it worked. The app's FFmpeg 4.4
numbers three `AVPixelFormat` members that 9.0.2 removed, and because they sit
inside the enum every later value moved, so the scaler read `AV_PIX_FMT_P010LE`
as `AV_PIX_FMT_GBRAP12LE` and P010/HEVC thumbnails either crashed (iOS) or
rendered garbage (macOS, lower half green). The swscale shims now translate the
legacy format before forwarding (commit `05423b2`). On the device P010/HEVC
thumbnails, playback, H.264/AVC and audio are all normal. HDR tone mapping
differs slightly, which is expected: the app never calls
`sws_setColorspaceDetails` and routes its Color Space setting only to the
display layer, so HDR conversion uses swscale 9.0.2's own defaults instead of
4.4.5's.

For the same input the libass-only patch produces a main whose SHA-256 is
`e84ef5b5e10cb10940ecffe73c3509f932a4aa6d2cba053052a7d9e7549792fe`, the
FFmpeg-only patch
`a5243f0a36baf5ef5209d51f312bd9d6f0c8d3b05d4053fcbbaa48735339f83b`, and the
default selection
`3bee29d20c4cc6e5979f594dc8df34a6c0fd96e48240e1c2d6a0065fe69be810`.

## Troubleshooting

| Message | Cause | Fix |
| --- | --- | --- |
| `still FairPlay-encrypted` | the IPA comes straight from the App Store | provide a decrypted dump of your own purchase |
| `no manifest matches this main executable` | wrong nPlayer version, or the IPA already has the patch | use a supported, clean dump |
| `bridge.exports` / `bridge.install_name` failed | wrong or stale dylib | use the dylib from the matching release |
| `bridge dylib for <id> is missing` | the selected dylib is not in `--dylibs-dir` | copy it there or point `--dylibs-dir` at it |
| `unknown dylib ids: <id>` | typo in `--dylib` | the ids are the manifest's `dylibs[].id` values |
| `ldid is required to assemble the IPA` | ldid is not installed | `brew install ldid` |
| `host assembler library is missing` | `libkeystone.dylib` is not in the checkout | download it from the release assets, or run `make bootstrap` |

## Rebuilding from source

`deps/sources.lock.json` and `deps/ffmpeg.lock.json` pin every dependency
(version, archive URL, SHA-256) and `make bootstrap deps bridge` rebuilds both
closures and both dylibs. `make deps` verifies each closure and refuses a
surprise fourth archive. That path needs Xcode, cmake, ninja and meson;
`dev/README.md` describes it, plus how to re-check the frozen ABI and re-run the
device acceptance.

## Legal

This toolchain is MIT licensed (see `LICENSE`); the third-party notices for the
statically linked libraries are in `THIRD-PARTY.md`. 

The project is not
affiliated with, or endorsed by, the nPlayer authors. You must own a licence
for nPlayer, and you should patch only your own copy.
