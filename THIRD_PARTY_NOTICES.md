# Third-party notices — V0.2.0 source candidate

ComposerLint-owned code uses the root MIT LICENSE. This source-only candidate
contains an independently licensed Verovio patch and original third-party license/
notice documents. It contains no renderer binary, font software, vendor renderer
source archive/tree, dependency environment or restricted repertoire collection.
No binary-redistribution compliance pass is claimed.

## Verovio patch

Upstream: [Verovio 6.3.0](https://github.com/rism-digital/verovio/tree/version-6.3.0).
The modified `src/iomusxml.cpp` identifies Laurent Pugin and Klaus Rettinghaus and
states “Copyright (c) Authors and others. All rights reserved.” The exact included
patch changes `MusicXmlInput::CloseTie`; ComposerLint's local modification is dated
2026-11-02. The patch is **LGPL-3.0-only**, not MIT and not an official upstream release.

Preserve the [local modification notice](LICENSES/third-party/verovio/LOCAL-TIE-FIX-NOTICE.txt),
full [GPLv3](LICENSES/third-party/verovio/COPYING) and
[LGPLv3](LICENSES/third-party/verovio/COPYING.LESSER) texts. The patch and source
instructions retain provenance and disclaimers. No endorsement is implied.

## Collected component and font notices

Original license texts under `LICENSES/third-party/` are retained as notice/reference
material; their presence does not mean the corresponding packages are bundled.
Runtime/build dependencies are obtained separately. Their own distributions retain
their license obligations. The existing lock specifies versions and artifact hashes;
these are not evidence of license compliance for a different distribution.

The exact-renderer investigation collected original notices for humlib, JSON++,
pugixml, MINIZ-CPP/miniz, tuning-library and CRC under
`LICENSES/third-party/verovio/`. `SOURCE-FONT-METADATA.txt` preserves copyright/license
statements from source fonts. These are notices only, not font glyphs or binaries.
Other retained package/tool notices identify their respective authors and licenses;
they do not relicense those components under ComposerLint's MIT license.

The current private renderer's font/resource, MidiFile, generated LibMEI/SWIG and
other artifact-specific notice pairings remain incomplete. Compressed WOFF2 metadata
was not decoded by the raw text audit. Do not distribute the private wheel or infer
compliance from matching hashes. See [source/binary licensing scope](docs/licensing/renderer-redistribution.md)
and [artifact evidence](docs/licensing/renderer-artifact-audit.json).

## Source publication versus binaries

The reviewed source candidate publishes the LGPL source modification with the
applicable texts, dates and attribution. It excludes renderer software/resources
requiring additional reconciliation. Instructions describe separately obtaining
upstream source, applying the patch and building locally; no artifact is downloaded
automatically by this distribution.

Future covered binary distribution additionally requires an applicable complete
Corresponding Source/build-material delivery mechanism, original per-component/font
notices and any applicable LGPL replacement/recombination and installation-information
requirements. A patch or upstream homepage alone is not complete binary clearance.
See the [release checklist](docs/V0.2-M5-release-checklist.md).
