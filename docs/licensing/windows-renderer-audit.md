# Windows x64 renderer distribution audit

This file records the Run #30 wheel audit and the subsequent wheel remediation.
The [standalone Windows renderer record](../../windows/RENDERER-RELEASE.md)
tracks the current build and Windows proof.

**Open. Windows renderer binaries are not cleared for distribution.** The
published Run #26 text-only installation is outside this audit. The earlier
[renderer artifact audit](renderer-artifact-audit.json) covers a macOS wheel
and supplies leads for the Windows inspection. The retained upstream archive
matches the published SHA-256 and contains both upstream license texts, 2,680
regular files under `data/` and 27 under `fonts/`. That inventory is a source-side
baseline. The two Windows wheel resource maps still need independent comparison.

## Exact candidate evidence

| Member | Reviewed input or evidence still needed |
| --- | --- |
| Official Verovio 6.3.0 Windows x64 abi3 wheel | 5,741,661 bytes, SHA-256 `cc8bab65570ccb38d6a440fa13d3e4820df34ee9ca483decdbe8371c4291b1a8`; acquire from the pinned PyPI origin and inspect these exact bytes |
| Patched Verovio 6.3.0+composerlint.tie1 Windows x64 abi3 wheel | Pending native run, exact size and SHA-256, complete member/RECORD and PE import inspection |
| Upstream source archive | 6,158,246 bytes, SHA-256 `33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c`; inventory its source, generated data, bundled components and font source rights |
| Deferred-tie patch | SHA-256 `10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60`; preserve dated change notice, exact source preimage and postimage |
| Native build provenance | Pending native run, exact size/digest and matching source, official and patched wheel, patch, build requirements, Python, MSVC and SDK identities |
| Windows Full manifest | Pending native run, exact manifest digest, commit, run ID/number/attempt, runner image/version and all five file identities |

The expected `src/iomusxml.cpp` preimage is
`0def2542a241989f2368fe9b54e67db474242fc0d24b27c48af8dac5ee499ab5` and the
postimage is
`4235d47c8cf9939ca09858a0d7d15c56eb1b9debdd8ceb6f067bdce536605ad9`.

After authorized CI, record the exact GitHub run and artifact digests and
independently check the decrypted files. Record each wheel's full inventory,
ZIP CRCs, RECORD hashes and native extension SHA-256. Compare each resource
byte to corresponding source for each wheel. Compare PE import and runtime
DLL loading evidence on clean Windows 11 x64, including Python Stable ABI,
UCRT and any required or bundled MSVC runtime. Record licenses and corresponding
source requirements for each non-system dependency. An undeclared runtime
installer is outside the MateBook setup scope.

## Notice, resource and source checks

- Identify original versions and notices for humlib, JSON++, pugixml,
  MINIZ-CPP/miniz, tuning-library, CRC and MidiFile in each exact artifact.
  Reconcile generated LibMEI interfaces/schema and SWIG binding notices and
  verify whether additional Windows native code is linked or bundled.
- Inventory Bravura, Leland, Gootville, Leipzig, Petaluma and Liberation,
  including WOFF2 data embedded in CSS and other font variants. Tie each
  shipped font to the original source, copyright, complete terms, conversion
  history and applicable reserved font names. Check Times bounding-box
  resources, footer SVG and remaining non-font resources individually.
- Prepare the exact corresponding source for both covered binaries, including
  modified sources, interface and generated/resource inputs, and scripts
  controlling build and installation. Verify the source kit rebuilds the
  applicable Windows target using separately provisioned tools. Compare the
  official wheel’s build/source metadata against the upstream 6.3.0 tag and
  build recipe, including generated version or commit headers. Supply
  equivalent source access alongside the binaries. Keep the full GPLv3 and
  LGPLv3 texts, dated modification notice, upstream attribution and complete
  per-component notices with the delivered materials.
- Review LGPLv3 section 4 applicability, replacement or recombination,
  reverse-engineering terms and any required Installation Information.
  Demonstrate an owner-controlled compatible rebuild and manifest re-pinning
  with runtime hash verification still active. See the repository's
  [renderer redistribution requirements](renderer-redistribution.md).

The five-file renderer ZIP is the install input. License texts and the
corresponding-source kit and instructions travel alongside it, with their own
inventories and hashes. Private review retention grants no distribution
clearance. Record evidence and remaining gaps for the exact candidate before
approving any device handoff.

## Run #30 and the remediation branch

The private baseline is Run #30, attempt 1, artifact 14193222489, commit
`f7410648ca7b2bd0109429ffac3f533749ea8817`. Binary distribution remains blocked. The
retained wheels, original archive, tie patch and build provenance have fixed
hashes. Keep their private location out of any package. The two wheels each
carry 2,680 resources and the same nine embedded fonts. The patched wheel data
matches the retained source bytes. The official wheel includes a signed
14.40.33810.0 runtime renamed by delvewheel and some CRLF resource differences.
The patched wheel imports ordinary `MSVCP140.dll` externally. Its compiler was
MSVC 14.51.36231. The old DLL is insufficient evidence for the patched build's
runtime requirement.

| Exact stylesheet payload | Disposition for a Full release candidate |
| --- | --- |
| Bravura `bbc4fcd9b9fc` | Steinberg 1.392 OTF, OFL 1.1, reserved Bravura, 886 of 3,690 characters. Match the OTF and SVG origin, then use a properly named subset or a verified original. Include the applicable Steinberg notice and original credits. |
| Leipzig `f3cf12eac566` | 5.2.102 TTF, OFL 1.1, 658 of 774 characters. The font project reserves Leipzig. Pair its notice with this revision before renaming or substituting its original webfont. |
| Leland `5d5a7d31154b` | MuseScore BVBA 0.80 OTF, OFL 1.1, reserved Leland, 329 of 492 characters. Rename the subset or use a verified original. Include the MuseScore notice. |
| Petaluma `e804492f4b8b` | Steinberg SVG, OFL 1.1, reserved Petaluma. Pair the original desktop font with the SVG before renaming or use an authenticated original asset. |
| Gootville `5edadae0f5d3` | 1.3 OTF, 207 of 313 characters. Match the exact OFL grant and copyright notice, including any applicable Gonville and Bravura template terms. A missing reserved-name declaration in the OTF alone is insufficient evidence. |
| Liberation Regular `4053734bfb32` | Replace Ascender 1.04 with publisher 2.1.5 Regular. |
| Liberation Bold `61b473882d51` | Replace Ascender 1.04 with publisher 2.1.5 Bold. |
| Liberation Italic `aa0cf7a55ac3` | Replace Ascender 1.04 with publisher 2.1.5 Italic. |
| Liberation Bold Italic `a1856fa909fb` | Replace Ascender 1.04 with publisher 2.1.5 Bold Italic. |

All five music payloads are modified versions. Keeping a modified subset under its
original reserved name needs written permission. Record the actual internal
family and PostScript names, CSS references, SVG and glyph-tree names and
upstream notices in the resource manifest. Use the originals from
[Liberation 2.1.5](https://github.com/liberationfonts/liberation-fonts/releases/tag/2.1.5)
and its [OFL notice](https://github.com/liberationfonts/liberation-fonts/blob/2.1.5/LICENSE)
for all four styles. The archived 1.04 license lineage is
[GPLv2 with exceptions and Red Hat trademark terms](https://fedoraproject.org/wiki/Licensing:LiberationFontLicense).
[OFL webfont guidance](https://openfontlicense.org/webfonts-and-reserved-font-names/)
applies to format changes. Resolve the four Times metric files, footer SVG, any
standalone fonts, and every referenced derived glyph resource separately. A
matching wheel hash or an unused runtime mode does not clear a bundled asset.

### Runtime entitlement and clean-device test

Record the signed file's exact origin in an eligible, released x64 Microsoft
REDIST set and the intended distributor's grant, together with hash, version,
signer and the applicable terms. Confirm the renamed 14.40 copy is covered
before including it. Prefer a documented, toolset-compatible application-local
set using its original filenames and an update owner. If the grant remains
open, the proposed fallback is a user-run Microsoft x64 prerequisite. Rebuild
the comparison wheel so both renderers import the ordinary runtime names and
remove the old renamed DLL from the delivery inventory. In a read-only preflight,
check the x64 runtime version against the final compiler floor and probe the
required imports before staging a new installation. On failure, show the
Microsoft download and minimum version and let the user or their administrator
run it separately. Verify the same checks after the user returns. The installer
performs no runtime download, elevation or system configuration write on this
route. This fallback requires review before implementation.
[Microsoft x64 download](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
[Visual Studio 2026 REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2026/redistribution)
[Microsoft runtime compatibility](https://learn.microsoft.com/en-us/lifecycle/faq/visual-c-faq)

Run `tools/probe_windows_renderer_runtime.py --expect <approved-pins.json>` with
each installed Windows interpreter in isolated mode after installing in a fresh
standard-user NTFS generation. The pins file maps lowercased runtime base names
to an absolute expected path and SHA-256. Include the loaded Verovio extension,
Python ABI and versioned DLLs, the selected C++ runtime files and the UCRT host.
Use Windows Authenticode validation, including trust and revocation checks, on
the exact retained runtime files. Record versions and both loaded-module
receipts on clean Windows 11 x64 without Visual Studio. Compare preflight and
post-install system runtime, registry and PATH state.

### Source and release evidence

A formal licensing review needs the exact release wheel and matching preferred
source, interfaces, generator/resource inputs, notices and build/install
scripts for each delivered renderer. Preserve both upstream GNU license texts
and the dated local tie-fix notice under LICENSES. Resolve the imported MidiFile
revision against its upstream notice and the generated LibMEI and MEI schema
inputs against their original notices. Record the actual SWIG output notices.
Keep the original archive private. Assemble the kit from the reconciled build
tree with disputed streams excluded, and provide equivalent source access at
no additional charge alongside the final installer or binary. Review the
actual LGPL arrangement, including compatible replacement and installation
information. [Verovio LGPLv3](https://github.com/rism-digital/verovio/blob/version-6.3.0/COPYING.LESSER)
[GPLv3](https://www.gnu.org/licenses/gpl-3.0.html)

For the private Run #30 rebuild, use the pinned archive and patch listed above
and the exact `requirements` order from `tools/ci_windows_renderer.py`. Its
`build.txt` contains the eight build wheels from
`tools/windows-renderer-assets.json` in manifest order, then Colorama 0.4.6
from `tools/windows-setup-assets.json`. It has 859 bytes and SHA-256
`ac9df1114217ab2fe5aac50eef639e943752b88f4e78f5a4b3ffb61af05a73c1`.
Use CPython 3.12.13 x64, uv 0.12.3, MSVC 14.51.36231 and SDK 10.0.26100.0.
Verify compiler and tool hashes against the private provenance. The native
recipe uses a fresh NTFS workspace, a hash-pinned wheelhouse, exact patch
application and the `cp310-abi3-win_amd64` build settings in
`tools/build_windows_renderer.py`. For the new candidate pin the staged resource
changes and DLL origins. Issue new wheel and manifest identities and record
observed native and resource digests. Resource-only repack can preserve native
bytes where their rights, build correspondence and loader behavior are proven.
Native source, link or import changes require a new native build. A matching
build recipe alone does not establish byte-for-byte reproduction.

### Owner replacement and native release checks

In a private Full setup, build the compatible owner-modified renderer from
source, using wheel version `6.3.0+composerlint.tie1` and the documented Windows
ABI. Review the changed source and compute the replacement wheel's SHA-256.
Run the reviewed launcher with absolute local paths.

```powershell
.\Setup.ps1 -Mode Full -RendererBundle $verifiedPrivateBundle `
  -OwnerRendererWheel $ownerWheel -OwnerRendererSha256 $ownerSha256
```

The owner verifies the plan and types `REPLACE`. Setup installs in a new
private generation, verifies RECORD and the installed bytes, checks the
native identity and runs the ten comparison controls and MCP smoke. It
prints the new MCP executable and `COMPOSERLINT_RENDERER_CONFIG`. The owner
chooses whether to switch the client to those values and restart. Keep the
previous command and configuration for rollback by restoring them together
and restarting. Windows Approve & Export remains disabled.

After separate native-run approval, verify the frozen candidate on a reviewed
Windows x64 runner. Run the existing preflight, bootstrap-deadline, bundle
manifest, distribution, renderer identity and native NTFS controls. Use the
candidate's exact DLL pins for both interpreter probes. Run
`tools/audit_windows_renderer_resources.py` on each wheel with the staged
`--source-data` directory and representative `--svg` outputs. Verify offline
music and text, glyph coverage, CSS references, pagination and score mapping.
Require all ten two-renderer comparisons with zero skips and full installed MCP
analysis, notation and preview/discard. Exercise a genuinely different
owner-built wheel, verify the prior install after a cancelled selection, prove
activation and rollback and reject changed wheel bytes, missing or unexpected
files and hostile paths. Repeat install and DLL checks on a clean Windows 11
desktop. Keep Run #30 and the restricted one-run workflow as historical evidence.

Before future macOS or Linux binary delivery, repeat the exact resource
inventory check for each wheel/architecture and inspect both delivered and
setup-acquired assets and generated SVGs. Match shared evidence by digest.
Check platform-specific dependencies, licenses, equivalent source access and
owner replacement on the actual target. The retained macOS ARM64 wheels
contain all nine baseline streams. The Linux per-target inventory is open.

## Liberation 2.1.5 private candidate evidence (2026-11-16)

The four historical Liberation Serif 1.04 CSS payloads have verified
replacements. Use `tools/prepare_liberation_renderer_resources.py` with the
publisher's separate TTF and source archives:

```sh
python3.12 -I -B tools/prepare_liberation_renderer_resources.py \
  --ttf-archive /private/input/liberation-fonts-ttf-2.1.5.tar.gz \
  --source-archive /private/input/liberation-fonts-2.1.5.tar.gz \
  --output /private/output/liberation-overlay
```

The tool verifies size and SHA-256 before reading archive members. Both archives
match the independent Gentoo size and SHA-512 records. The TTF archive also
matches Buildroot's SHA-256. All 23 regular source-archive members match the
Git blob IDs in publisher commit `4b0192046158094654e865245832c66d2104219e`.
The archives carry identical LICENSE and AUTHORS files. The four TrueType
files match the publisher's face identities, version and OFL copyright
metadata, and their table checksums pass. Use the original TTF bytes in CSS,
with the existing Verovio CSS alias `Liberation`. This leaves internal family
and PostScript names untouched and preserves all font metadata. Include the
publisher's OFL 1.1 text and authors with the actual distribution. Bundle the
font as OFL Font Software, separate from Verovio's LGPL and ComposerLint's MIT
terms. The OFL reserves Liberation as well as Arimo, Tinos and Cousine.
[Publisher release](https://github.com/liberationfonts/liberation-fonts/releases/tag/2.1.5)
[Gentoo manifest](https://github.com/gentoo/gentoo/blob/master/media-fonts/liberation-fonts/Manifest)
[Buildroot checksum](https://lists.buildroot.org/pipermail/buildroot/2021-October/688705.html)

| Face | Exact publisher TTF SHA-256 | Legacy / replacement Unicode cmap sizes | Missing legacy entries |
| --- | --- | --- | --- |
| Regular | `058ea80864aef09a23f45cbec2bb5400bc3dfbdea01c3f10538a21fcb497fb74` | 663 / 2,321 | U+2011, U+F001, U+F002, U+F005 |
| Bold | `d754ba427cfe0bca54ae052384baa8f842da5bd6550ad4da024ac441e7a7d5ce` | 662 / 2,321 | U+2011, U+F001, U+F002, U+F005 |
| Italic | `0e3dea9f8d613e006ccfa62201f33e265d19167bd0907725c3e145368b04fc2e` | 663 / 2,329 | U+2011 |
| Bold Italic | `f17db8af71e24d2066b587546021d4f0b296be389512b658dec3c09affeb11a7` | 662 / 2,322 | U+2011, U+F001, U+F002 |

Printable ASCII advances and the three horizontal line metrics are equal for
all faces. The figure dash U+2012 increases by 342 font units in all faces.
Other common-width changes are U+00B7 in Regular and Bold and U+012F in Italic.
Offline Chrome loaded all eight baseline and candidate faces. Five sample
strings, including Latin accents, Cyrillic, kerning pairs and a nonbreaking
hyphen, matched in measured advance and bounds in all four styles on this
macOS host. The figure dash exposed a 6.6797 CSS pixel difference at 40 px in
each style. U+2011 falls back in the browser with the new font. Legacy
private-use characters and platform-specific fallback still need consideration.

The unchanged macOS native renderer produced the same page counts and SVG
geometry for three authored scores with and without Liberation enabled. It
produced byte-identical SVG with Liberation disabled. With Liberation enabled,
only embedded styles changed. The MEI comparison ignores Verovio's generated
application timestamp. Embedding the four original TTFs grows the CSS from
393,379 to 2,022,040 bytes per affected page. The largest test page was 2,162,278
bytes, below the existing 8 MiB limit. If this optional mode is enabled in the
product, verify the total output budget and fallback on representative long
scores and native Windows. Keep the current default with Liberation off.

The private Windows wheel copies use the build tag `1liberation215`. Only
Liberation.css, WHEEL, RECORD and the addition of two notice files differ.
CRC and full RECORD validation passed, and every .pyd and .dll hash matches
its parent. The historical Liberation payloads are absent in both copies.
These copies still contain the other five legacy font streams. The comparison
wheel still contains the old renamed runtime. The copies are for local resource
verification. A release requires a new reviewed inventory, accepted Windows
loading evidence and separate distribution approval.

### Music font and source notice provenance

| Asset | Evidence obtained | Remaining issue |
| --- | --- | --- |
| Bravura | Retained OTF, SVG and SMuFL metadata each match the Steinberg `bravura-1.392` tree. The tagged OFL text matches its Git blob and reserves Bravura. Preserve the 2021 notice in the original binaries as well as the tagged notice. | The embedded subset is modified. Use a verified original or rename a verified derivative and update references. |
| Leland | Retained OTF and metadata match publisher commit `d91cf5d21045e2c294541676ad386b7ffb4ac881`. The exact publisher license reserves Leland. | The retained SVG and embedded subset are distinct from the publisher OTF. Validate derivation before renaming or use the verified original. |
| Leipzig | Historical commit `8de1ebfc102db2dee283381971f89a502ee29a55` matches the retained SMuFL JSON and has a verified OFL text reserving Leipzig. | The retained TTF, SVG and standalone WOFF2 differ from the publisher files checked. Pair exact glyph sources before treating those bytes as publisher originals. |
| Petaluma | The retained SVG embeds Steinberg's OFL notice reserving Petaluma. Its creation date aligns with the publisher's 1.065 log. | Date alignment identifies a likely release. Exact original binary and SVG derivation remain open. |
| Gootville | The retained 1.3 OTF and SMuFL metadata match MuseScore `v4.7.5` exactly. The font declares OFL. Its publisher ships the older 1.2 README crediting the Bravura 1.18 template. Gonville's author permits unrestricted use of the font outputs. | Reconcile the exact 1.3 copyright and inherited Bravura terms before retaining the modified subset. |
| MidiFile | Nine of ten retained MidiFile C++ sources/headers match upstream `51eec11fe4f8e05bf4ce36ccc7d7e179f174291a`. Its license matches the publisher's Git blob and is retained in LICENSES. | Pair the differing `MidiMessage.cpp` and its changes before marking the component complete. |
| LibMEI and MEI | The exact retained MEI schema reference resolves to publisher commit `6da4dd5071303ac53d73d7b5ea0399f9cfe7bdba`. | Pin the affected schema files and their terms, the generated LibMEI code and the actual SWIG output notices. |
| Times and footer | The newest Times metrics commit adds the space metric. A local comparison found advances shared with both original Liberation 2.1.5 and installed Times New Roman, so the advances cannot identify the origin. | Establish the source of the outlines and metric XML, and the footer terms or justified exclusion. |

Relevant upstream references are [Bravura](https://github.com/steinbergmedia/bravura/tree/bravura-1.392/redist),
[Leland](https://github.com/MuseScoreFonts/Leland/tree/d91cf5d21045e2c294541676ad386b7ffb4ac881),
[Leipzig](https://github.com/rism-digital/leipzig/blob/8de1ebfc102db2dee283381971f89a502ee29a55/LICENSE.txt),
[Petaluma](https://github.com/steinbergmedia/petaluma/blob/master/redist/FONTLOG.txt),
[Gootville](https://github.com/musescore/MuseScore/tree/v4.7.5/fonts/gootville),
[Gonville](https://www.chiark.greenend.org.uk/~sgtatham/gonville/),
[MidiFile](https://github.com/craigsapp/midifile/blob/51eec11fe4f8e05bf4ce36ccc7d7e179f174291a/LICENSE.txt),
and [MEI](https://github.com/music-encoding/music-encoding/commit/6da4dd5071303ac53d73d7b5ea0399f9cfe7bdba).

### Microsoft decision after the private font candidate

The Microsoft 2026 diagnostic and Build Tools supplement provides a limited
right to compile qualifying open-source dependencies. Its build-use terms
do not establish ComposerLint's right to bundle the redistributable files.
The 2026 REDIST list conditions use on a valid license and the applicable
terms. Microsoft's deployment documentation describes how an eligible
distributor can place unmodified runtime DLLs beside the executable. Before
selecting this route, record the distributor and its actual grant, exact
eligible x64 REDIST origin and version, the terms that accompany the runtime,
and the security update owner. Verify that the conditions coexist with the
LGPL replacement and debugging rights for the actual arrangement. Windows
loads must confirm the expected private copies, signature trust and version.
[2026 Build Tools supplement](https://visualstudio.microsoft.com/license-terms/vs2026-ga-diagnostic-buildtools/)
[2026 REDIST list](https://learn.microsoft.com/visualstudio/releases/2026/redistribution)
[Application-local deployment](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files)

If the entitlement cannot be documented, the proposed delivery uses a
user-managed Microsoft x64 v14 prerequisite. After separate approval, build
the comparison wheel without the renamed runtime so both renderers request
the ordinary DLL names. Inventory the bootstrap and transitive wheels
separately. Setup checks the installed x64 version and actual imports and
points users to Microsoft's official installer if needed. A user or
administrator obtains and runs it separately. No system runtime is bundled
or run by ComposerLint's launcher on this route.
[Microsoft supported runtime downloads](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
