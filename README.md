# ComposerLint

ComposerLint V0.2.0 provides local, evidence-based score analysis and bounded,
independently verified editing through MCP tools and companion Skills. Discuss
notes, harmony, melodies, motifs, interacting parts and stylistic intentions with
an AI client, inspect the supporting score evidence, and keep artistic decisions
under human control.

This is a **source-only distribution**. It includes ComposerLint, three Skills,
authored examples/tests and an LGPL-licensed Verovio patch. It does not include
renderer binaries, fonts, dependency environments or repertoire collections.

## What works

- Review uncompressed partwise MusicXML and Standard MIDI, with explicit coverage,
  uncertainty and source references. MIDI remains analysis-only.
- Import `.mxl` through its declared MusicXML container root. Import `.mscz`
  conditionally through a separately installed MuseScore Studio 4 converter.
- Browse complete notation pages, zoom and select measures. Natural page layout
  has no eight-measure display limit; conversational selections cover 1–8 measures.
- Analyze chords and ambiguity, solo melodies, rhythmic/motif relationships and
  source-lane ensemble relationships. Discuss qualified style characteristics and
  goal-relative evaluation without composer attribution or universal quality scores.
- Preview exact pitch replacement, spelled intervals, atomic pitch batches and
  explicit whole-part shifts within 1–8 contiguous measures of one part.
- Prepare constrained expressive alternatives involving eligible pitch/register,
  dynamics and articulation within 1–4 measures. Rhythm alternatives and new
  pitches/rhythms in 1–4 completely resting measures use separate bounded policies.
- Compare Original and Proposed notation, inspect actual changes and independent
  verification, and discard temporary proposals. Eligible M6 creative candidates
  support separate-file export after personally operated macOS confirmation.

Exact M5 pitch previews remain temporary and are not eligible for that export route.
Automatic theory repairs, unrestricted composition, arbitrary notation restructuring,
PDF/audio input, playback and a general-purpose notation editor are unavailable.
Structural verification does not prove artistic improvement or a requested mood.

## Quick start

The demonstrated runtime is **CPython 3.12 on macOS Apple Silicon**. Intel Mac and
Windows are intended targets, and Linux is under investigation; none has completed
real installation/workflow verification. The code still has platform-specific
filesystem/process/confirmation requirements. Source availability is not a claim
of cross-platform support.

Use the complete source archive or repository file set, including `skills/`,
`.agents/skills/`, tests and license materials. Do not install an old V0.1 delivery.
From an extracted source directory, using an existing Python 3.12 and `uv`:

```sh
uv sync --locked --no-editable --python /absolute/path/to/python3.12 --no-python-downloads
.venv/bin/python -B examples/analyze.py tests/fixtures/basic.musicxml
```

The sync command installs dependencies and may access the package index. Review
and approve installations as required in your environment. Core text analysis does
not require the optional renderer or MuseScore. See [installation](docs/INSTALL.md)
for reproducible setup, renderer prerequisites, MCP connection and troubleshooting.

Connect the local stdio server with the installed interpreter:

```sh
/absolute/path/to/ComposerLint/.venv/bin/python -B -m composerlint.server
```

Codex is the demonstrated client. Other MCP clients need local stdio and filesystem
access; their configuration conventions and interactive viewer behavior have not
been independently verified. Keep stdout reserved for MCP traffic.

The three authoritative Skills live in `skills/composer-import`,
`skills/composer-review` and `skills/composer-edit`. Relative links under
`.agents/skills` expose them in the project. Keep all three folders together because
of their cross-links. Reconnect the server and start a fresh client session after
updating; verify the actual tool inventory rather than assuming discovery succeeded.

Try these requests with an absolute path visible to the server:

| Request | Result |
| --- | --- |
| “Review `/path/to/piece.musicxml`.” | Evidence-backed musical review |
| “Convert `/path/to/piece.mxl` to MusicXML.” | Verified temporary working path and provenance; no unsolicited analysis |
| “Open `/path/to/piece.mscz` and analyze measures 9–12.” | Conditional import, then the requested viewing/analysis |
| “Raise the horn in measures 9–10 one octave.” | Clarify ambiguous targets, then an eligible temporary verified preview |
| “Try a quieter accompaniment here; preserve the melody.” | Discuss supported constraints/brief before generating an eligible alternative |
| “Create a short idea in these four empty measures.” | Bounded single-lane note creation if all prerequisites pass |

## Renderer prerequisite

The full verified viewer/editing workflow requires the patched native renderer
`6.3.0+composerlint.tie1`. Official Verovio 6.3.0 failed the required deferred
cross-voice tie controls and is not an equivalent substitute. This project retains
warning refusal, source correspondence and independent candidate checks.

Obtain the official Verovio 6.3.0 source yourself, verify the published input hash,
apply the included patch and build a separate local renderer environment using
[the build instructions](patches/README.md). No prebuilt patched renderer is supplied
or downloaded automatically. The current adapter also requires official 6.3.0 in
the main environment as its base-version gate; the patched build runs separately.
Installing only the custom build into the main environment does not satisfy that gate.

Select the verified renderer with an explicit server-owner manifest; never let a
score or tool request choose executable code. Building/provisioning the renderer is
an advanced setup step. [Installation](docs/INSTALL.md) describes the manifest and
its integrity checks. The text-only review route remains available without it.

## Files, privacy and human approval

Inputs are local server-visible files. URLs/file URIs, external score assets and
unsafe archives refuse. Working sources/import receipts live together in private
OS-temporary folders and may expire or be removed by the OS. They are not exports.
Source, working file and receipt identities are checked before reuse.

On macOS, configuration/data default to `~/Library/Application Support/ComposerLint`;
new exports use its `exports/` child. Absolute server-owner overrides are documented
in [installation](docs/INSTALL.md). Existing files are never moved automatically.
Original bytes stay unchanged. Stale sources, missing receipts, changed candidates,
unsupported notation and failed verification refuse rather than silently simplify.

For eligible export, inspect Original/Proposed and actual changes, personally click
Approve & Export, and personally approve the native confirmation. The AI must not
operate that dialog. No MCP approval flag substitutes for the human decision.
The exact candidate is checked again before exclusive creation of a new MusicXML
file and receipt. Rejection, timeout or unavailable confirmation produces no
successful export. M5-only previews cannot use this export route.

## Development, licenses and release scope

The tests use locally authored synthetic MusicXML/MIDI controls and independent
expectations; fixture provenance is documented under `tests/fixtures/`. Preserve
those sources and expectations. Do not regenerate fixtures to hide failures.

ComposerLint-owned code is MIT licensed; see [LICENSE](LICENSE). The Verovio patch
is **LGPL-3.0-only**, with [local attribution](LICENSES/third-party/verovio/LOCAL-TIE-FIX-NOTICE.txt)
and full GPL/LGPL texts kept separately. Third-party components retain their own
terms; see [notices](THIRD_PARTY_NOTICES.md) and [distribution scope](docs/licensing/renderer-redistribution.md).
Hashes establish identity, not licensing compliance. Renderer binaries/font
resources remain excluded and uncleared for redistribution.

The [publication manifest](docs/licensing/public-source-manifest.json) records the
exact proposed file set. [Release readiness](docs/V0.2-M5-release-checklist.md) tracks
remaining gates. This candidate has not been published; release preparation does
not authorize GitHub creation, pushing, tagging or publication.
