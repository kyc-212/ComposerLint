# V0.2.0 source-only RC3 candidate review

Scope: the reviewed manifest plus the installation guide, this report and the
manifest itself. Candidate preparation does not authorize publication. Exact paths
and hashes are in public-source-manifest.json; distribution archives are separate
from the private verification environment.

## Flag decisions

- Include the four flagged test modules unchanged except the separately documented
  renderer-configuration test adaptation. Their file URIs are deliberately invalid
  fixtures (`file:///a.xml`, `file:///tmp/score.musicxml`, `file:///private`,
  `file:///etc/passwd`), not personal paths or embedded private contents.
- Include the 15 MIDI fixtures. Existing authored provenance/expectations and their
  standard-library source encoders document synthetic construction; metadata inspection
  found no personal attribution or acquired-score content. No fixture was regenerated.
- Include the fixture READMEs; their authorship and known-answer statements are useful
  provenance, not claims that a repertoire collection is included.
- Include the public README, Skill loading guide, patch guide and third-party notices
  after replacing private setup/history links with source-distribution instructions.
- Include original license/notice texts as attribution documents. Font metadata excerpts
  contain license/copyright statements only, not font binaries or glyph/resource data.

No flagged file required permission to redistribute a private score or binary. All
renderer wheels/resources/vendor trees, research archives, personal workspaces/exports,
private manifests/environments, historical local work logs and AGENTS remain excluded
from this candidate and preserved locally. The manifest's exclusions are mandatory;
absence of a warning from an automated scan is not independent licensing clearance.

## Source licensing scope

ComposerLint-owned implementation, Skills, examples and locally authored controls
are MIT licensed. The exact Verovio patch is separately LGPL-3.0-only and carries
upstream author attribution, the dated local modification notice and full GPL/LGPL
texts. Original third-party notice texts keep their own terms. No dependency binaries,
font software, renderer source archive or patched renderer binary is redistributed.
Binary-specific renderer obligations remain pending for any future binary distribution.

## RC1 verification (retained evidence)

Completed for RC1 on the Apple Silicon host; these operations were not rerun for RC2:

- Fresh non-editable install in a separate environment from the selected source
  candidate, using unchanged locked dependencies. An offline attempt stopped on a
  missing cached artifact; the user then approved fetching missing exact locked
  artifacts. They are stored only in the private verification cache/environment.
- Package, lock and negotiated MCP version are 0.2.0; imports resolve to the fresh
  environment's installed package, not the working checkout.
- Independently rebuilt the patched renderer from the retained official archive,
  exact patch and existing pinned tools; installed it into a separate private runtime.
  Its authored tie/refusal/multipage checks pass. This build is not distributed.
- 50 distinct focused candidate cases pass: the main scoped run had 49 passes and
  one environment-directory-name assumption in a test. The test now checks the
  actual configured interpreter and retained tamper refusal; that case passed on
  recheck. An intervening copy command used the wrong cwd and reran the old test;
  the corrected copy/recheck passed. Eight unrelated cases were deselected. The
  earlier 24 renderer/path passes overlap these results and are not added again.
- An actual fresh stdio MCP session discovered all 18 tools and completed authored
  MXL import, review, verified M5 pitch preview, Original/Proposed SVG retrieval,
  verification-receipt retrieval and discard. Both source and compressed input bytes
  remained unchanged. A helper initially used an incorrect proposal URL (404);
  correcting the helper required no application/safeguard change.
- Export safeguard tests use mocked native decisions; they do not claim personal
  approval, artistic acceptance or a fresh GUI export trial. This complete M5
  workflow is preview/discard and does not export.
- Wheel inspection verifies V0.2.0 metadata, MIT license inclusion, four viewer assets,
  absence of private renderer artifacts and no restricted identity in author metadata.
  The canonical source archive retains three relative discovery links. Python sdist
  file selection was corrected to retain real Skill folders without dereferencing
  those links; recreating its omitted discovery links is documented.
- Candidate local Markdown links resolve within the selected file set. A bounded
  credential/private-machine-path scan found no matches; this supplements the
  per-file/provenance review and does not establish general security certification.

No full musical-development campaign, performance benchmark or platform port ran. The release baseline is macOS
Apple Silicon, CPython 3.12. Additional platforms and conda dependency options remain
unverified. Schema/policy versions remain unchanged; only package/lock/MCP product
metadata changes to 0.2.0. A test's renderer configuration lookup uses the installed
per-user/explicit manifest instead of requiring a private checkout file.

## RC2 documentation-only refresh — 2026-11-07

RC2 retains the exact RC1 publication allowlist: 546 regular files and three relative
Skill discovery links. The only non-metadata change is the approved export-location
sentence in `skills/composer-edit/SKILL.md`: exports use the configured per-user
directory, with the macOS default and server-owner override reference stated.
Application code, musical logic, renderer behavior, dependencies, fixtures, licenses
and unrelated documentation are unchanged.

The other two changed files are this candidate review record and
`public-source-manifest.json`, which records the corrected hashes and RC2 lineage.
The manifest self-entry retains its non-recursive hash convention; the external
archive checksum covers the complete manifest. Product/package version stays 0.2.0.

RC1 remains preserved in its original archive and prepared source directory,
including its original manifest and review report. Its private verification records
and initial Git commit `0387bc8639b3a3059aac44fed782c3ea801b19ed` remain unchanged.
RC1 archive SHA-256:
`ddc1457761378da92f658e9cd857af5845ac85a5ce31d30c896c758d540374cf`.
RC1 manifest SHA-256:
`0d0ef906017b6006990024621d613d1e2d7499ca248be9fd8e5ddc15faa8895b`.

Focused RC2 checks compare archive contents, file modes and link targets with RC1,
the refreshed manifest and intended Git-tracked contents. Documentation references,
the corrected export destination, unchanged licensing notices, privacy exclusions
and the restricted copyright-identity location are checked. No musical-analysis,
editing, installation, renderer-build, native approval/export or performance tests
are rerun. RC1 evidence above is reused because those implementation bytes are
unchanged; its platform, mocked-export and personal-acceptance limits still apply.
The older private Python sdist is not the RC2 publication candidate.

RC2 archive: `ComposerLint-0.2.0-source-rc2.tar.gz`, SHA-256
`bd69b03c86ec589e98389c05088ea0d4f886ade143bfbe78713a9ed6b6dd2144`.
Its original manifest, report and source directory remain preserved privately.

## RC3 historical-path privacy correction — 2026-11-07

RC3 changes only the historical baseline path metadata in
`docs/V0.2-M4-evidence/baseline.json`, this review record and the publication
manifest relative to RC2. Fourteen personal custody-folder components are replaced
with the neutral component `custodian`. The path keys are privacy-normalized
historical identifiers, not literal paths to a redistributed archive. All 1,902
file/hash entries, score identifiers, hashes, baseline date and ten tool contracts
are retained. No application, musical, renderer, fixture, dependency or license
content is changed. The approved RC2 export-location correction is retained.

The original baseline is preserved byte for byte in private local evidence and
the historical RC1/RC2 archives. A verified private Git bundle preserves the original
initial commit. No private backup, original archive or old Git history belongs to
this publication candidate. Only the exact 546 regular files and three relative
Skill links in the manifest are intended for a new parentless public initial commit.

Focused verification covers reversible normalization of exactly those 14 keys,
unchanged contract schemas and historical hash values, identifier/path scans over
the complete public allowlist, archive identity, licensing materials, documentation
links and Skill discovery links. Existing RC1 implementation evidence is reused;
there is no new musical-analysis/editing campaign or native-export acceptance.
Other first-name mentions are public author attribution, instructions and fixture
provenance; the distinct third-party author's required notice remains unchanged.
The restricted copyright identity remains confined to the root MIT license.

Local accidental-staging safeguards live only in Git's private exclude file. They
exclude private development material while keeping every manifest path eligible;
the public `.gitignore` is unchanged. These safeguards do not replace exact
allowlist checks or grant publication authority.

RC3 archive: `ComposerLint-0.2.0-source-rc3.tar.gz`; its SHA-256 is in the adjacent
external `SHA256SUMS`. This report does not embed its own archive checksum.

## Remaining approval boundary

The initial local Git repository still points to the original private commit.
RC3 preparation does not execute Git history rewriting or create a replacement
commit. The exact proposed ref changes and private-history retention procedure
must receive separate explicit approval first. GitHub repository creation, a remote,
push, tag, visibility changes and release publication remain separately unauthorized.
