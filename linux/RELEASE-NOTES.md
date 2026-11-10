## Linux GTK visual refresh — review candidate

The sidebar identifies ComposerLint / SETUP · LINUX x86-64. This does not imply
universal Linux compatibility. Native automated verification targets Ubuntu 24.04
x86-64 under GTK/X11 with a virtual display; real Arch Linux, Niri and Wayland
acceptance remains pending. Installation still requires the documented capability
checks. Linux permanent MusicXML export remains disabled.

The interface now uses the existing original ComposerLint icon as an integrity-
checked PNG, responsive persistent actions, readable selection cards and an
expandable connection section. Exact paths and commands remain copied from the
shared backend. Local AI client setup is described neutrally; actual client Skill
and live-selection discovery must still be tested per client.

# Draft — ComposerLint Linux graphical setup volunteer preview

**Unpublished candidate. Native GUI build/verification and final ZIP/checksum are
pending. These notes do not authorize publication or claim that a package exists.**

The proposed primary download is `ComposerLint-Setup-Linux-x86_64.zip`,
containing a native GTK4 application, original artwork, reviewed source/Skills,
resource manifest, source notices and build provenance. The SHA-256 sidecar is
generated only after native compilation and an independent ZIP extraction check.

The interface provides full/text-only choices, prerequisite review, separate
installation consent, real progress, local logs, safe cancellation/retry and
stable MCP/Skill instructions. Installation uses the existing shared backend.
The Verovio patch, dependency lock and ten renderer controls are preserved.


**Initial native result / diagnostic authorization:** run
[42754958771](https://github.com/kyc-212/ComposerLint/actions/runs/42754958771)
failed at prerequisite HTTPS download (`RemoteDisconnected`) before package
installation, GTK/renderer compilation, native controls or packaging. No artifact
was uploaded. Kenneth approved exactly one new diagnostic run (#2, attempt 1),
with unchanged pins and added per-package progress logging. No Linux support or
graphical package success is claimed from the failed first run.

## Evidence and limitations

**First build authorized 2026-11-10:** Kenneth approved the feature-branch push,
workflow-only disabled registration on main, reviewed prerequisites and exactly
one initial native Ubuntu build. The feature job/catalog are enabled, with
owner/ref/commit/first-run guards retained. Repository-readable Actions artifacts
are approved for review; this does not authorize a GitHub Release or support claim.
Native results and the final ZIP checksum remain pending execution.

**2026-11-10 native build preparation (not executed):** the exact intended filename
is now `ComposerLint-Setup-Linux-x86_64.zip`. A separate manual Ubuntu workflow,
reviewed system-prerequisite hash catalog, native GTK callback/protocol harness
and bounded build/check driver are prepared. The workflow's job remains explicitly
false and the prerequisite catalog remains unapproved. No package artifacts were
acquired, no CI run started, and no native compilation/ZIP/checksum is claimed.
See README's native CI approval packet for exact commands, package versions,
SHA-256 values, permissions, storage and remaining real-desktop checks.

Final preparation regression on Mac: **307 passed / one optional MuseScore skip
in 47.52 seconds**, warnings as errors, including all 10 existing renderer controls.
This supersedes the local counts below for the current preparation. The new GTK
harness and system-package provisioner remain natively unexecuted. A helper's
parent-death watch now also covers prerequisite/compiler/display probes; production
installation/export consent boundaries and macOS branches are preserved.

- **Verified earlier backend baseline only:** GitHub Ubuntu 24.04.5 x86-64 run
  [42608626902](https://github.com/kyc-212/ComposerLint/actions/runs/42608626902),
  commit `7a90cc6ceebdc11bdf0d6903e0a86cbdbb54b6af`: native patched renderer compiled;
  10/10 equivalence controls, fresh MCP discovery (18 tools), 187 focused passes
  and one optional MuseScore skip. This evidence does not cover this newer GUI or
  Arch recipe and must not be presented as a GUI installation pass.
- **Pending:** native compilation/link checks of this GTK4 frontend, Ubuntu GUI
  interactions and actual clean full/text installations from the final ZIP.
- **Unverified:** this candidate's native Linux compilers, Arch Linux,
  Niri/Wayland, other desktops/filesystems,
  actual client Skill discovery, browser/live selection and optional MuseScore.
- **Disabled:** production Linux human-approved permanent MusicXML export.
  Installation approvals never grant score-export permission.
- The macOS graphical release remains unchanged. No Linux full-support claim,
  binary renderer redistribution, system package installation or root requirement.

Before replacing this draft with publication-ready notes, record the exact native
GUI build candidate, ZIP/hash, linked dependencies, cancellation/retry evidence,
stable-path/removal checks, test results and remaining real-desktop gaps. Obtain
Kenneth's explicit publication approval. Do not mark the prerelease as latest.

## Local candidate evidence (macOS, not Linux GUI acceptance)

**2026-11-10 portability corrections:** removed the exact Arch GCC requirement.
The GUI, shared backend and frontend builder use one capability-report schema.
Renderer admission requires real C++20 compile/link capability; frontend admission
uses a separate C11 compile/link probe. Compiler path/version/SHA-256 participate
in plan identity, preserving launcher names such as `clang++`. No toolchain is
acquired. Ubuntu CI's historical compiler provenance and all artifact pins remain
unchanged. GNU ABI, dynamic-loader/library and isolated actual GTK connection
diagnostics precede installation; headless backend use does not depend on GTK.
The connected GDK backend is reported instead of inferred from environment hints.
GUI and Terminal setup exercise one shared conservative ext4/Btrfs filesystem
probe after consent. These corrections still require native Linux verification.

Final corrected-candidate macOS regression: **285 passed / one optional MuseScore
skip in 41.37 seconds**, with warnings treated as errors. All 10 renderer controls
passed on macOS. The final capability-focused subset passed **167 checks**
(overlapping coverage). Actual C11/C++20 probes compiled and linked with the
existing Mac compiler; Linux compiler/ABI/display branches have authored unit
evidence, not a new native Linux run. Early temporary-directory and test-regex
issues were corrected before this passing result. The native packager correctly
refused this Mac without creating output; **no final Linux ZIP or checksum exists**.
The GTK C frontend, including safe diagnostic fields, remains uncompiled here.

Earlier local candidate evidence: affected installer, renderer, export-safety and worker regression **236 passed,
one optional MuseScore skip in 40.55 seconds**, warnings as errors. After final
bootstrap/cache changes, **43 graphical-controller/archive checks passed in 1.86
seconds** (overlapping coverage). These include matching/rejected consent, real
shared-backend cancellation and child reaping, changed/escaping archives, atomic
bootstrap publication/retry, refused filesystems, retained-source integrity,
removal of the downloaded source folder, real Skill links and independent ZIP
extraction with executable permissions. ZIP tests use authored archive fixtures;
they are not evidence of a built Linux executable or graphical interaction.

One early positive protocol fixture closed stdin prematurely and therefore received
the expected cancellation status instead of completion. The fixture was corrected
to keep its simulated GUI connection open; final checks pass. No failed evidence
was counted as a successful Linux or GUI run.

235 selected protected baseline files retain their hashes, including macOS release
files, patch, lockfiles, authoritative Skills and fixtures. Musical algorithms and
export eligibility are unchanged. No new native CI run, software acquisition,
commit, push, tag, release, repository setting, AGENTS.md or memory change occurred.
