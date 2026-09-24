# Third-party notices

`LibASSBridge.dylib` (published as a release asset) statically links the
libraries below. Each one is built from the pinned source recorded in
`deps/sources.lock.json`; `dev/README.md` documents how to rebuild the same
closure from those sources, which is what satisfies the source-availability
requirement of the LGPL component.

No nPlayer code is included or redistributed by this project.

| Library | License | Version | Source |
| --- | --- | --- | --- |
| libass | ISC | 0.17.5 | https://github.com/libass/libass |
| FreeType | FTL (FreeType License) or GPL-2.0 | 2.14.3 | https://github.com/freetype/freetype |
| HarfBuzz | Old MIT | 14.2.1 | https://github.com/harfbuzz/harfbuzz |
| FriBidi | LGPL-2.1-or-later | 1.0.16 | https://github.com/fribidi/fribidi |
| fontconfig | MIT-style (Keith Packard) | 2.17.1 | https://gitlab.freedesktop.org/fontconfig/fontconfig |
| expat | MIT | 2.8.5 | https://github.com/libexpat/libexpat |

The authoritative license texts are the `COPYING`/`LICENSE` files inside each
upstream source tree. The versions above are the ones pinned by
`deps/sources.lock.json`; when a pin changes, this table and the manifest's
`libass_version` change in the same commit.
