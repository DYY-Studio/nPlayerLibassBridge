# ffmpeg-core enable-set comparison

The core unit replaces the FFmpeg the app links statically, so its component
set has to cover the app's own build: a container, codec, protocol or bitstream
filter that is present in the app and absent from the unit keeps running the
app's 4.4.5, and one that is present in the unit and absent from the app can
change what `avcodec_find_decoder`/`av_guess_format` resolve to.

Nothing records the `configure` options of the app's FFmpeg, so the comparison
is made on the component names themselves: the `AVCodec`/`AVInputFormat`/... name
literals the app's main binary carries against the ones the built
`LibFFmpegCoreBridge.dylib` carries. Regenerate with

```sh
uv run python dev/tools/enable_set_diff.py
```

## Tool output

```text
- candidates: 773
- present in the app: 682
- present in the unit: 706

## In the app, not in the unit (would stay on 4.4.5)

- `sftp`
- `urlprotocol bio`

## In the unit, not in the app (capability the unit adds)

- `a64multi`
- `a64multi5`
- `asf_stream`
- `avm2`
- `fifo_test`
- `framecrc`
- `framehash`
- `framemd5`
- `libmp3lame`
- `mkvtimestamp_v2`
- `mp2fixed`
- `mxf_d10`
- `mxf_opatom`
- `prores_aw`
- `prores_ks`
- `rtp_mpegts`
- `singlejpeg`
- `smoothstreaming`
- `stream_segment,ssegment`
- `streamhash`
- `svcd`
- `uncodedframecrc`
- `vcd`
- `version_major`
- `version_minor`
- `webm_chunk`
```

## Item by item

**In the app, not in the unit**

| name | verdict |
|---|---|
| `sftp` | not an FFmpeg component here. The app's 14 occurrences are its own network UI (`sftp://%@`, `_sftp-ssh._tcp`, the host/port/path plist) and libssh2 (`libssh2` appears 18 times). The four distinctive `libavformat/libssh.c` literals (`Authentication successful with password.`, `Error initializing sftp session: %s`, `Cannot stat remote file.`, `Authentication successful with auto selected key.`) are absent from **both** binaries, so neither build has FFmpeg's sftp protocol. |
| `urlprotocol bio` | the OpenSSL BIO method name in `libavformat/tls_openssl.c`, and three more `tls_openssl.c` literals (`SSL_CTX_load_verify_locations %s`, `Unable to load cert file %s: %s`, `Certificate Authority data`, `Unable to negotiate TLS/SSL session`) are in the app: the app's FFmpeg has the **OpenSSL TLS** backend. The unit builds no OpenSSL, so the literal still shows up here, but the capability is no longer missing - see the finding below. What remains is a backend difference, not a gap. |

**In the unit, not in the app** — capability the unit adds, plus extractor noise:

| entry | verdict |
|---|---|
| `a64multi`, `a64multi5`, `mp2fixed`, `prores_aw`, `prores_ks` | encoders the app's build did not compile (the app records with the codecs its site table shows, not these). |
| `asf_stream`, `avm2`, `fifo_test`, `framecrc`, `framehash`, `framemd5`, `mkvtimestamp_v2`, `mxf_d10`, `mxf_opatom`, `rtp_mpegts`, `singlejpeg`, `smoothstreaming`, `stream_segment,ssegment`, `streamhash`, `svcd`, `uncodedframecrc`, `vcd`, `webm_chunk` | muxers the app's build did not compile. Every muxer the app's site table does use is present in the unit (`hlsenc`, `mpegtsenc`, `movenc`, `matroskaenc`, `flvenc`, `oggenc`, `nutenc`). |
| `libmp3lame` | extractor noise: a log string inside `libavformat/mp3enc.c` (`Lavc libmp3lame`), not the encoder. The unit has no libmp3lame. |
| `version_major`, `version_minor` | extractor noise: AVOptions of the `argo_asf` muxer, not component names. |

The extractor takes every `.name` literal from a file that also defines a
component, so AVOption names in those files can appear. They are the only
entries here that are not component names.

## Finding: the unit had no TLS protocol (closed 2026-09-26)

The app's FFmpeg was built with OpenSSL TLS; the unit was built with
`--disable-autodetect` and no TLS option, so it had none:

```text
config.h before:         CONFIG_TLS_PROTOCOL 0   CONFIG_HTTPS_PROTOCOL 0
                         CONFIG_SECURETRANSPORT 0  CONFIG_OPENSSL 0
                         CONFIG_LIBSSH_PROTOCOL 0
                         CONFIG_HTTP_PROTOCOL 1  CONFIG_CRYPTO_PROTOCOL 1
libavformat.a before:    no tls*.o member, no ff_tls_protocol
protocol_list.c:         async cache concat crypto data ffrtmphttp file ftp
                         gopher hls http httpproxy icecast mmsh mmst md5 pipe
                         prompeg rtmp rtmpt rtp srtp subfile tee tcp udp
                         udplite unix
```

`ff_https_protocol` lives in `http.c` behind `CONFIG_HTTPS_PROTOCOL`, so with no
backend `avio_open2("https://...")` takes the `libavformat/avio.c:289` path,
logs `https protocol not found, recompile FFmpeg with openssl/gnutls/
securetransport...` and fails, where the app's 4.4.5 succeeds. The same applies
to `rtmps`. `crypto` (AES-128 HLS) is present and unaffected.

**Confirmed live (2026-09-26, device).** On the three-unit selection an https
M3U8 HLS does not play at all: the hardware decoder fails and falls back, the
software path shows no duration or other stream data, and the QuickTime engine
(the Apple stack, which has its own TLS) still connects. The same selection
plays the same HLS over **http**, and plays an **https direct URL** (that path
goes through the app's curl stack, not FFmpeg). The libass + swscale/swresample
selection plays the https HLS correctly because it keeps the app's own FFmpeg.
That isolates the failure to the missing TLS backend and nothing else.

An earlier "settled: latent, not live" reading of an https HLS pass was wrong:
that run happened on a payload whose dispatch never activated (the fix landed on
main as `e257582`), so it measured the app's own FFmpeg, as `dev/acceptance.json`
now records.

**Fixed in the build.** The closure is configured with
`--enable-securetransport` and the dylib links `Security.framework`:

```text
config.h now:            CONFIG_TLS_PROTOCOL 1   CONFIG_HTTPS_PROTOCOL 1
                         CONFIG_SECURETRANSPORT 1  CONFIG_OPENSSL 0
libavformat.a now:       tls.o, tls_securetransport.o,
                         ff_tls_protocol, ff_https_protocol
```

`make verify` passes 21/21 and the dylib's dependency list stays on system
frameworks. The backend differs from the app's by design (Apple's deprecated
SecureTransport API, protocol version negotiated by the system, certificates
from the system trust store), so https/rtmps still need a device re-run; the
user chose this over adding an iOS OpenSSL closure to the build.

## Not covered by this comparison

- parsers (no name literal; keyed by codec id),
- hardware accelerators (name literals exist but the app uses none),
- the exact `configure` options of the app's build, which are not recorded,
- decoder/encoder *selection order* when several names match one codec id. The
  unit adds encoders and muxers the app never references, so order can only
  matter on the recording path, which is not device-tested (see
  `dev/acceptance.json`).
