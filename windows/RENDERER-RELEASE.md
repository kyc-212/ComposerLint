# Standalone Windows x64 renderer

The patched and comparison builds use LLVM-MinGW and import only Kernel32
and the Windows Universal C Runtime. Both passed the standalone rendering
and loaded-module checks on the Huawei MateBook 13 with Windows 11 x64.
Use the [standalone build and proof instructions](../tools/windows_renderer_native/README.md).

## Source and font preparation

Run `tools/stage_windows_renderer_source.py` with the retained Verovio 6.3.0
source archive and the two verified Liberation Serif 2.1.5 archives. The
tool needs an existing Python 3.12 environment with FontTools 4.66.1 and
`patch`. It works offline in a fresh output directory. Example:

```sh
python -B tools/stage_windows_renderer_source.py \
  --archive /reviewed/verovio-6.3.0.tar.gz \
  --liberation-binary /reviewed/liberation-fonts-ttf-2.1.5.tar.gz \
  --liberation-source /reviewed/liberation-fonts-2.1.5.tar.gz \
  --output /private/renderer-source
```

The source trees carry the [renderer notices](../LICENSES/third-party/verovio/WINDOWS-RENDERER-NOTICES.txt),
generated LibMEI files, compiled ODD inputs, build scripts, and font inputs. The archived Leipzig
publisher sources are identified by both the upstream Verovio archive
SHA-256 and their Git blob IDs. RISM documents continued development of
Leipzig inside Verovio. The retained glyph path files match those from
that release's Leipzig SVG. The published Bravura SVG and OTF match the
Steinberg 1.392 tree and the Leland OTF and anchors match the MuseScore
publisher commit. The original OTF bytes are embedded offline. Leland
path geometry comes directly from its OTF. The prepared Liberation font
bytes and publisher sources ship with the source kit. The regenerated
tables use only codepoints both the legacy layout tables and the new
fonts cover. The text font option defaults to off. Gootville, Petaluma,
old embedded font streams and footer artwork are excluded. ComposerLint
renders its viewer with the Verovio automatic footer off.

[Bravura 1.392](https://github.com/steinbergmedia/bravura/tree/bravura-1.392/redist)
· [Leland publisher commit](https://github.com/MuseScoreFonts/Leland/tree/d91cf5d21045e2c294541676ad386b7ffb4ac881)
· [Leipzig project](https://github.com/rism-digital/leipzig)
· [Liberation 2.1.5](https://github.com/liberationfonts/liberation-fonts/releases/tag/2.1.5)
· [OFL naming and webfonts](https://openfontlicense.org/webfonts-and-reserved-font-names/)

## Windows proof and follow-up

The proof ZIP contains both executables, their shared resources, two
MusicXML fixtures, the test driver, notices and a hash inventory. Its
matching source ZIP is published in the same prerelease. Follow the
[PowerShell setup and native acceptance instructions](../docs/INSTALL.md#windows-x64-full-setup)
to test the integrated installer on clean Windows 11 x64.

The [Run #30 wheel audit](../docs/licensing/windows-renderer-audit.md) records
the earlier Python extension build. Its wheels, Microsoft runtime findings,
and install manifest are historical evidence and do not belong in the
standalone test kit. The staged source and notices remain inputs to the exact
candidate review for any later delivery.
