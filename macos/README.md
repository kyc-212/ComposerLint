# Native ComposerLint Setup — ZIP distribution

The native SwiftUI application uses the existing standard-library Python installer
as its only dependency/renderer installation backend. `./install.sh` remains the
Terminal interface. No musical, renderer-patch, editing or export-approval code is
changed by this frontend. The frontend has no score export approval interface.

The native window provides a five-step checklist, separate consent screens,
installation choices, expandable logs and connection instructions. Checkmarks follow
actual setup events; the final Codex connection remains a user action. The original
music/checkmark app icon is generated from `MakeIcon.swift` using macOS AppKit and
iconutil during the build. No downloaded artwork, bundled fonts or additional UI
frameworks are required.

## User journey and storage

Download `ComposerLint-Setup-macOS-arm64.zip`, double-click to extract it, then open
ComposerLint Setup.app. Choose full or text-only setup. Review
and approve prerequisite acquisition/source preparation, then review and approve
the backend's exact dependency/build plan. Progress reports actual stages and byte
counts where available; compilation details are in the expandable log. Cancel
interrupts the installation process; retry revalidates retained materials.

The app manages private pinned Python/uv copies rather than changing system tools.
Their archives come from the official Astral GitHub releases after consent. Their
exact URLs, SHA-256 values and two independent maintainer evidence references are
committed in `bootstrap-pins.json`. Versions: uv 0.12.3; CPython 3.12.13, standalone
build 20260807, ARM64 install-only stripped archive. No floating latest downloads,
Homebrew, shell-profile changes or automatic updates. Existing verified private
copies are reused. Package acquisition continues to use the unchanged uv lock,
official renderer pin and renderer-build requirements.

The app detects Apple Command Line Tools. Install Apple Tools invokes only Apple's
`xcode-select --install` workflow; the user must operate licence/security dialogs.
It never accepts Apple terms, obtains administrator credentials, replaces the
selected toolchain or bypasses Gatekeeper. Text-only setup does not require renderer
compilation. Unsupported platforms or failed prerequisites receive explicit errors.

Default files live under `~/Library/Application Support/ComposerLint/setup/`:

- `bootstrap/<archive-sha256>/`: verified archives, private tools and file receipts.
- `sources/<manifest-id>/`: verified source and project-local Skill discovery links.
- `installations/<recipe-id>/`: backend-managed runtime/build/renderer environments,
  retained source/wheel, private renderer manifest, verification receipts and log.

The app's bundled resources are read-only. No environment is created inside the app,
ZIP extraction or Downloads folder. Moving/removing the setup app must not affect
installed operation. Do not move the stable installation tree: interpreter/manifest
paths intentionally bind it. Existing renderer.json, configurations, workspaces,
scores and exports stay untouched. Copy the completion screen's absolute command,
arguments and explicit renderer environment into your MCP client's configuration.
Open the displayed source folder in a Skill-capable session. Global Codex settings
are never modified. The `--data-root /absolute/private/path` app argument is an
explicit isolated-testing location; it does not change the normal default.

## Frontend protocol

`tools/setup_composerlint.py --json --mode full|text --installation-base /absolute/path`
uses JSON-lines protocol 1 on stdout and diagnostic text on stderr. Before mutation
it emits `plan` with `plan_id`, mode, prefix and description. The caller must send
exactly `{"action":"approve","plan_id":"<reviewed-id>"}` on stdin. EOF or `cancel`
grants no permission; a changed or mismatched recipe refuses. Subsequent events are
`progress`, `complete`, `error`, or `cancelled`. Completion includes interpreter,
arguments, optional manifest, stable source folder and log path. SIGINT/SIGTERM
cancel through the same backend cleanup as the Terminal route. Consent here covers
software installation only, never human approval for a MusicXML export.

## Reproducible local build recipe

Use native Apple Silicon, the installed Apple Swift compiler/SDK and the project's
locked Python development environment (Hatchling is already in uv.lock). No new
frontend frameworks/packages are needed. Build into a fresh output directory:

```sh
.venv/bin/python -I -B tools/build_macos_setup.py --output /absolute/local/output
```

The script builds the allowlisted sdist, restores its three project Skill links,
records bundled source hashes, compiles a native ARM64 app, verifies its local ad
hoc signature and makes `ComposerLint-Setup-macOS-arm64.zip`. The archive contains
only the complete app bundle (plus macOS metadata). macOS `ditto` preserves bundle
metadata, relative Skill links and executable permissions. Packaging verifies CRCs,
extracts with `ditto`, compares all file bytes/links/permissions, and verifies the
extracted app signature. A `.zip.sha256` checksum file is written alongside it. It never downloads build software, commits, uploads
or publishes. Review `build.json`, including source-manifest and ZIP hashes.
The source/recipe is reproducible; bit-for-bit ZIP identity across toolchains or
signing timestamps is not promised. macOS 14 is the launcher deployment floor,
not evidence that the entire locked runtime works on every macOS 14+ release.
Actual end-to-end platform acceptance must name the exact tested OS/toolchain.

## Unsigned distribution and first launch

The current distribution is **not Developer ID signed or notarized**. The native
ARM64 executable has an ad hoc signature for local code integrity; that establishes
no verified developer identity and no Apple malware review. ZIP is a packaging
choice, not a Gatekeeper workaround. No Apple Developer Program membership is
required to create the ZIP. Signing/notarization work is deferred by user decision.

Use the [user installation instructions](../docs/INSTALL.md#graphical-installation-macos-apple-silicon)
and [Apple's security guidance](https://support.apple.com/en-us/102445). On a blocked
first launch, a user who trusts this exact release may approve **ComposerLint Setup**
through System Settings → Privacy & Security → Open Anyway, then Open. Authentication
may be required. Stop for malicious/damaged-app warnings or a managed-device policy
that disallows an exception. Never remove quarantine or disable Gatekeeper.

## GitHub Release installation text

Use this text when a separately approved release is actually published:

> **macOS Apple Silicon:** Download `ComposerLint-Setup-macOS-arm64.zip` and its
> `.zip.sha256` checksum from this release. Double-click the ZIP, then open
> **ComposerLint Setup.app**. This app is **not Developer ID signed or notarized**;
> macOS may block it. If you trust the release, use **System Settings → Privacy &
> Security → Open Anyway** for **ComposerLint Setup**, then confirm Open. Never
> disable Gatekeeper or remove quarantine. See the linked installation instructions
> before deciding whether to grant that exception. The ZIP does not bypass security.
>
> Choose full or text-only installation. Review and approve both plans. Private
> pinned Python/uv tools and locked dependencies are downloaded after consent;
> full setup compiles the verified renderer locally. Apple Command Line Tools may
> require you to complete Apple's installation/licence prompts. Internet access and
> compilation time are needed. The completion screen gives Codex MCP and Skill
> instructions. Keep the installed Application Support tree; the downloaded app/ZIP
> can be removed afterward. No global Codex settings or existing scores/exports are
> changed. No third-party renderer binaries, fonts or environments are bundled.
>
> The launcher minimum is macOS 14 on native ARM64; this is not a promise that every
> macOS 14+ environment has been tested. Consult the release's exact verification
> evidence. Intel Mac, Windows and Linux remain unverified. Developers/AI agents
> can use the source-based `./install.sh` or manual installation instructions.

Attach only the reviewed ZIP and its generated checksum (plus approved source
assets). Record the exact tested OS, checksum and remaining limitations in the
release. These instructions and local artifacts do not authorize publication.

## Optional future DMG, signing and notarization

The existing `--dmg` build option and explicit `--identity` option remain available.
The default ZIP build does not create a DMG, contact a notary service or use an
Apple identity. No further DMG/signing development is part of the current scope.

For a future explicitly authorized distribution, obtain an Apple Developer ID
Application identity and protect its private key in Keychain. Rebuild with
`--identity 'Developer ID Application: ...'`; the script enables hardened runtime
and a secure signing timestamp. Do not add broad runtime exceptions or disable
library validation to work around a failure. The app launches separate tools;
it does not load the downloaded renderer into its own signed process.

After inspecting the exact candidate, the maintainer can use Apple's supported
workflow (not executed by this build script):

```sh
xcrun notarytool submit /path/ComposerLint-Setup-arm64.dmg --keychain-profile PROFILE --wait
xcrun notarytool log SUBMISSION_ID --keychain-profile PROFILE
xcrun stapler staple /path/ComposerLint-Setup-arm64.dmg
xcrun stapler validate /path/ComposerLint-Setup-arm64.dmg
spctl --assess --type open --context context:primary-signature --verbose=2 /path/ComposerLint-Setup-arm64.dmg
```

Review Apple's notary log even on success. Verify signatures and the downloaded,
quarantined DMG on a fresh Mac, including offline ticket assessment, subprocess
execution and post-ejection operation. Never remove quarantine or disable Gatekeeper
to obtain a passing result. Notarization covers the submitted payload, not blanket
approval of files subsequently downloaded or built locally. Developer account and
notarization credentials are not bundled, saved in the repo or requested in chat.

A public release requires separate approval of the exact candidate, verified
prerequisites, user-facing unsigned-app disclosure and published source/checksums.
Updates are manual: download a new reviewed setup app. Existing installations stay
available; a changed recipe gets a separate directory and fresh consent. No silent
updater or unrequested cleanup. Uninstall/cleanup UI is outside this first scope;
never delete the shared configuration/data/exports directory as an uninstall step.

## Distribution boundary

The new GUI artifact contains ComposerLint's own native launcher plus reviewed
source, Skills, tests, lock/pin files and notices. It is therefore a compiled
launcher distribution with a source-delivered backend. It contains no renderer
binaries, dependency environments, Python/uv binaries, fonts or upstream resources.
The user obtains prerequisites/upstream materials directly and compiles the patched
renderer locally. All renderer source-only licensing limits and notices remain in
force. Future redistribution of those third-party artifacts needs separate review.

## Focused verification

Run `tests/test_installer.py` and `tests/test_installer_protocol.py` for the existing
Terminal route, JSON consent binding/changed-plan refusal and real child-process
cancellation. `tests/MacOSSetupCore.swift` is a **headless** native-core test target:
compile it together with the frontend using `-D SETUP_CORE_TEST -parse-as-library`
inside a disposable app with the same Resources. Supply a fresh `--data-root` and
`--archive-cache` containing the verified uv.tar.gz/python.tar.gz. It checks native
source preparation/reuse, extraction, execution, installed-file tamper refusal,
escaping-link refusal, unexpected-source refusal and cancellation. It does not
operate the window or establish graphical acceptance.

Before release approval, also download the ZIP through a normal browser with
Gatekeeper enabled, confirm quarantine, extract it using Archive Utility, and operate
the actual app without the development checkout on an unlocked Mac: cancel/retry, both consent screens, real progress/errors, completion,
MCP discovery and the existing renderer controls. Move the downloaded installer, quit the setup app
and verify the printed MCP connection still works from an unrelated directory.
Existing-Mac Gatekeeper evidence is not a fresh-Mac test. Apple-tool installation checks are separate from
existing-toolchain development tests. Never claim the headless tests cover them.
