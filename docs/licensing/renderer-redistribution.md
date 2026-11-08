# Renderer licensing and first source release

2026-11-07 historical source-candidate review: ComposerLint source,
Skills, authored fixtures, the exact Verovio patch, notices and build instructions.
No prebuilt renderer, vendored renderer tree/archive, font software or copied
dependency environments. Kenneth subsequently confirmed V0.2.0 was uploaded to GitHub.
The installer additions described below remain local changes awaiting review;
no new publication is authorized by their implementation.

## Distribution boundary

| Material | Source-only proposal | If later redistributed |
| --- | --- | --- |
| ComposerLint-owned Python/UI/Skills/docs | Include selected files under chosen project license | Verify ownership and any copied portions, retain applicable notices |
| Verovio tie patch | Include unchanged, explicitly LGPL-3.0-only | Preserve upstream attribution, date/local notice, full GPLv3/LGPLv3 texts |
| Original build instructions and hashes | Include | Keep precise provenance, commands, dependency/tool versions and limitations |
| Full Verovio source/archive | Exclude; identify official source by URL/hash | Preserve LGPL and all embedded/font rights/notices for every shipped file |
| Renderer wheel/native libraries/fonts/resources | Exclude | Complete artifact-specific license/source/replacement review first |
| Python dependency binaries/build environments | Exclude; declare dependencies | Distribution of installed packages has its own license/source obligations |
| Generated notation SVGs, source snapshots and research archives | Exclude unless individually reviewed | May contain embedded font software, personal scores or restricted source material |

Publishing a patch is publication of source modifications, not a grant to relicense
Verovio under the project's MIT license. The local notice and both full license
texts already exist under `LICENSES/third-party/verovio/`. ComposerLint's own license
is MIT; its copyright notice is finalized in the root LICENSE. The approved
identity is restricted to that notice and must not populate documentation or
package author fields. A source-only repository does
not redistribute packages merely because installation instructions name them.
Any actual bundling, vendoring or automated acquisition/distribution changes the scope.

### Approved local installer acquisition — 2026-11-08

Kenneth approved a consent-first source installer for native macOS Apple Silicon.
The repository distributes its launcher, standard-library setup utility, hash pins,
authored checks and documentation. After explicit consent, the user's machine obtains
official dependencies and Verovio source directly from reviewed PyPI/pythonhosted
locations, applies the unchanged LGPL patch and compiles a local wheel. Automated
acquisition is now included in this narrowly defined installation scope.

This does not add renderer archives, trees, wheels, fonts/resources or dependency
environments to the distributed source. Local downloads/builds/manifests are private,
ignored by Git and excluded from the sdist allowlist. Preserve complete upstream
resources/notices and the local modification notice; the installer retains the exact
upstream archive and repository patch/notice materials. No prebuilt patched renderer
service, public cache or downloadable binary release is authorized. Uploading local
artifacts would require the separate artifact-specific redistribution review below.
Existing binary/font clearance remains pending. Installation hashes and behavioral
checks establish identity/required behavior, not additional licensing rights.

The relevant source conditions are GPLv3 sections 4–5 as incorporated/supplemented
by LGPLv3; the upstream [GPL text](https://github.com/rism-digital/verovio/blob/version-6.3.0/COPYING)
expressly permits conveying source modifications with the applicable conditions.
The additional binary Corresponding Source delivery mechanisms in GPLv3 section 6
apply when conveying covered non-source forms. Do not require the uncleared font
wheel to be cleared as a prerequisite to a repository that does not contain it.
Likewise do not claim binary compliance just because the repository contains a patch.

## Exact renderer reconciliation

The [artifact record](renderer-artifact-audit.json) binds the existing wheel/source/
patch hashes and notice evidence. All **2,680 wheel resource files match the retained
source tree byte for byte**. This establishes correspondence, not redistribution rights.

| Component/resource | Evidence obtained or identified | Status for the existing binary |
| --- | --- | --- |
| Verovio 6.3.0 + tie1 | Exact source/patch/wheel hashes; original COPYING/COPYING.LESSER; source author notice; dated local notice | Main texts present in wheel; local modification notice must accompany distribution |
| humlib | Original redistribution/disclaimer block copied from exact humlib.h | Collected; reconcile whole compiled component before clearing |
| JSON++ | Exact include/json/LICENSE copied verbatim | Collected |
| pugixml | Original complete permission/disclaimer block from exact pugixml.hpp | Collected |
| MINIZ-CPP and embedded miniz | Original permission blocks from exact zip_file.hpp, including additional copyright holders | Collected; review all nested component obligations |
| tuning-library | Exact MIT license copied verbatim | Collected |
| CRC | Exact public-domain statement with required preserved notice | Collected |
| MidiFile | Source origin identified; upstream LICENSE.txt is available | Exact imported revision/license pairing still pending |
| Generated LibMEI | Source README declares MIT; AUTHORS/generated headers identified | Complete original license/copyright pairing pending |
| Generated SWIG binding | Existing SWIG LICENSE-SWIG text retained in development-build notices | Map actual generated/shipped binding notices; tool GPL is not automatically output GPL |
| Bravura, Leland | Full OFL/copyright/RFN declarations extracted from exact source OTF metadata | Collected as source-font evidence; converted resource pairing still pending |
| Gootville, Leipzig | Exact source-font creator/license statements extracted | Full original per-font terms/RFN mapping pending |
| Petaluma, Liberation | CSS contains compressed font software; source assets/generation references identified | Original attribution, full terms and conversion provenance pending |
| Times bounding-box XML/footer SVG/remaining resources | Exact wheel/source correspondence established | Individual provenance/notice review pending |

Collected originals are in `LICENSES/third-party/verovio/*-LICENSE.txt`,
`*-NOTICES.txt` and `SOURCE-FONT-METADATA.txt`; source hashes/locations are in the
artifact record. These contain notices only, not copied renderer code or font binaries.
The local modification notice is `LOCAL-TIE-FIX-NOTICE.txt`.

Correction to the early audit: no separately readable OFL/local notice was found
in the wheel's raw text scan, but WOFF2 fonts are compressed/base64-embedded in CSS.
That scan cannot rule out license metadata inside the fonts. The existing FontTools
installation cannot decode those WOFF2 streams without Brotli; nothing was installed.
The license result remains **pending**, rather than a claim that all font metadata
is absent. The [OFL conditions](https://openfontlicense.org/open-font-license-official-text/)
require applicable copyright/license notices and address modified-font reserved names;
a generic OFL text alone cannot substitute for per-font attribution/provenance.

## Build information to publish with the patch

Use the original official source archive identified in patches/README.md, SHA-256
`33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c`.
Retain patch SHA-256
`10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60`.
Provide a source URL, upstream author/license attribution and modification date.

Build instructions must let a recipient, using separately provisioned prerequisites:

1. Verify the archive and patch, stage fresh upstream source, apply with
   `patch --fuzz=0 -p1`, and verify only the intended source modification.
2. Reuse the documented C++20/native toolchain and pinned build frontend/backend/
   CMake/SWIG/Ninja baseline; set `VEROVIO_BUILD_VERSION=6.3.0+composerlint.tie1`.
   On the demonstrated Mac route retain deployment target 13 and native architecture.
3. Run `python -I -B -m build --no-isolation --wheel -Cbuild-dir=<fresh-directory>
   --outdir <artifacts> <staged-source>` using the provisioned build interpreter.
   No instruction authorizes executing downloads or installing tools automatically.
4. Run the authored deferred-tie, forward/open-tie, refusal and source-mapping checks.
   Provision a separate renderer runtime and its server-owner hash manifest only
   after verification; an invalid manifest must refuse rather than fall back.

Exact baseline versions and source/resource requirements are in the
[foundation strategy](../V0.2-cross-platform-foundation.md). The existing local
Mac wheel is not an independently reproduced release build. Intel/Windows build
instructions are provisional until demonstrated; no platform support is implied.

## Additional requirements before shipping renderer binaries

Provide the complete corresponding modified renderer source, interface definitions,
necessary generated/resource sources and build/install/control materials for that
exact binary. An unmodified-upstream link plus patch is not automatically a complete
binary source-compliance package. Prefer co-located source/material downloads with
equivalent access under GPLv3 section 6(d), avoiding an open-ended written-offer plan.
Do not include private scores, used build caches or virtual environments. Identify
general-purpose tools/system libraries separately from required corresponding source.

Preserve applicable per-component/font texts, copyright/change notices and disclaimers.
For any applicable Combined Work, assess LGPLv3 section 4's source/recombination or
suitable shared-library mechanism, modification/reverse-engineering permissions and
conditional Installation Information. A separate renderer process alone does not
prove an exemption. Demonstrate owner-controlled compatible renderer rebuilding and
manifest re-pinning without a proprietary signing key or maintainer permission.
Retain all score/candidate/approval/export validation. Full license texts, not this
summary, control the conditions for the actual arrangement.

## Publication readiness

**The reviewed source-only candidate excludes the uncleared binary materials.**
The exact file set and verification are now in the [candidate review](source-candidate-review.md)
and [manifest](public-source-manifest.json). Final user approval/publication actions
remain open. Binary font/embedded/source-compliance work remains deferred because
those software/resources/vendor trees are excluded.

Clearly advertise that the full verified viewer/editing path requires a separately
provisioned patched renderer. Official 6.3.0 failed required deferred-tie checks;
do not promise an equivalent prebuilt experience or weaken verification. Source-only developer setup is an explicit distribution limitation. Intel dependency
work remains documented and unverified; no conda installation or new platform work.
