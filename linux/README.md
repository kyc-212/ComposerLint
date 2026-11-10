# ComposerLint Setup — Linux graphical preview

This is the native GTK4 setup application, with the existing shared Python setup
backend. The primary Linux download is intended to be a graphical x86-64 ZIP.
The native executable and ZIP have **not yet been built or visually verified** for
this candidate. Do not publish a source-only stand-in as the graphical download.


**GTK visual refresh prepared (2026-11-10).** The Linux interface follows the
macOS setup hierarchy with the same original ComposerLint artwork, a teal/navy
sidebar, five live step indicators and full/text selection cards. Narrow windows
collapse the sidebar and stack the persistent footer actions; body and advanced
content scroll independently. Completion uses neutral “Connect your AI assistant”
wording with copyable, expandable exact backend instructions. No client settings
are edited. Fonts follow the GTK font setting; keyboard focus and choice labels
are explicit. The bundled PNG is extracted from the existing original macOS icon
and SHA-256 verified before loading, without an SVG-loader dependency.

Native Ubuntu verification is gated to Run #6 / attempt 1 on the feature branch.
It captures initial, tools review, installation review, progress, cancellation and
completion screens, plus small-window/font-scaling/long-path/advanced-scroll cases.
Error-layout screenshots are explicitly authored presentation checks; they are
not evidence of an actual failed installation. No real desktop or screen-reader
acceptance is inferred from virtual-display checks. All prior downloader retries,
source and artifact pins, consents, backend and export restrictions are retained.
The main registration remains disabled. No merge/release/tag/publication.

## Intended volunteer journey

1. Download `ComposerLint-Setup-Linux-x86_64.zip` and its `.zip.sha256` file
   from the separately approved GitHub prerelease. Verify the checksum and extract.
2. Open **ComposerLint Setup** inside the extracted folder. Some file managers
   require “Allow executing as a program” or a launcher configured to execute files.
   Niri itself is a compositor, not a graphical archive/file manager or application
   launcher; the tester should use their normal file manager/launcher.
3. Read the prerequisites and choose full setup or text analysis only.
4. Approve private Python/uv preparation; then review and approve the separate
   backend installation plan. Watch actual progress and expand the local log.
5. Recheck/retry after resolving a disclosed problem. Existing environments and
   unrelated files are preserved; retained work is reverified, never blindly reused.
6. Copy the displayed MCP command, arguments and renderer environment into your
   local AI client. Open the stable source folder for `.agents/skills` discovery.
   Reconnect and check ComposerLint 0.2.0 / 18 tools and the three Skills.

The source and private environments live under
`$XDG_DATA_HOME/ComposerLint/setup` (normally `~/.local/share/ComposerLint/setup`).
They do not depend on the downloaded setup folder after successful installation.
Do not move or delete that stable installation directory. No existing score,
export, AI-client configuration or shell profile is overwritten.

## Runtime prerequisites and limits

- Linux x86-64, a normal user account, and a graphical Wayland or X11 session.
- System GTK4 **4.12+**, JSON-GLib **1.6+**, GLib/GIO and system Python **3.12+**.
  System Python is used only to prepare the pinned private Python 3.12.13 runtime;
  PyGObject and a Python GUI framework are not required.
- Internet access to the disclosed Astral releases and hash-pinned Python package
  artifacts. Python 3.12.13 and uv 0.12.3 are installed privately after approval.
- Full setup on either target: an existing system C compiler, a C++ compiler
  that compiles **and links** the shared C++20 probe, renderer runtime libraries,
  and `patch`. GCC and Clang are discovered by capability, without an Arch version
  requirement. The probe exercises concepts, span and filesystem; the actual
  pinned renderer build and all 10 unchanged controls remain mandatory.
  Ubuntu CI's original GCC/G++ 13.3.0 provenance is retained separately.
  Text mode requires no compiler. No compiler is acquired or upgraded automatically.
- Building the graphical frontend requires a separate **C11** compile/link check
  plus GTK4/JSON-GLib development files and `pkg-config`; it does not require C++20.
- One shared capability report feeds the graphical bootstrap, installation backend
  and frontend builder. It checks x86-64, GNU/glibc, the running interpreter's ELF
  loader, loadable runtime libraries, existing tools and (GUI only) an actual
  isolated GTK display connection. Environment hints alone are insufficient.
  These are early checks, not proof that every pinned binary or desktop works;
  verified private executable/import checks and desktop acceptance remain required.
- A local **ext4 or Btrfs** per-user destination and existing `findmnt` (util-linux).
  Setup probes Unix ownership, mode 0700, no-follow
  regular-file access, exclusive locks and atomic publication. Network, FAT and
  symlinked installation paths are unsupported; safeguards never fall back.

The computer owner must review/install missing system prerequisites separately.
The app never invokes sudo, pacman, apt or an administrator helper. On Arch the
relevant runtime packages are `gtk4`, `json-glib` and `python`; full setup also needs
a capability-checked compiler and `patch`. These are **instructions**, not permission for
an agent to install packages. A missing GTK runtime can prevent any GTK application
from opening; check the loader's message and these prerequisites.

GTK supports Wayland directly; XWayland is not required for this frontend. The
actual Arch **Niri/Wayland** session remains an acceptance target, not a claimed
passing platform. Verify window sizing, focus, keyboard navigation, clipboard,
fractional scaling and dialogs with the tester's actual session/portal setup.
No compositor, portal, GPU or session settings are changed automatically.

## Editing, export and optional conversion

Full setup retains verified temporary MusicXML editing and the unchanged patched
renderer checks. **Production Linux permanent export remains disabled** because
native human approval has not passed desktop acceptance. Installation consent is
not score-export approval. Text mode has no notation or editing preview.

MuseScore conversion is optional, separately installed and unverified on this
desktop. MusicXML/MXL operation does not require MuseScore. Actual AI-client Skill
discovery and browser/live-selection integration require separate real-client tests.

## Sharing a problem

Use **Copy safe diagnostics**: it includes a small allowlist of platform, session,
package identity and setup status. It excludes credentials, paths, environment
dumps, scores and raw logs. No diagnostics are uploaded automatically. The expanded
local log may contain local paths; review it before sharing any excerpt.

## Advanced users and developers

The existing Terminal backend remains available:
`python3.12 -I -B tools/setup_composerlint.py --mode full` (or `--mode text`) from a
reviewed source checkout with the pinned uv on PATH. It is a secondary developer
interface; the GUI calls the same backend and plan-bound JSON-lines v1 protocol.

Build on native Linux as a normal user with existing `cc`, `pkg-config`, GTK4 and
JSON-GLib development headers, `unzip`, and the already locked Hatchling environment:
`python tools/build_linux_setup.py --output /absolute/new/output-folder`.
No build command installs prerequisites, uploads artifacts or publishes a release.
See [release notes](RELEASE-NOTES.md) and [third-party boundaries](THIRD-PARTY.md).

## Maintainer verification gates before publication

The Mac has no GTK4 headers, JSON-GLib headers, Linux linker or Linux desktop.
The new frontend has not been compiled or visually inspected. Do not count the
earlier backend-only Ubuntu run as verification of this candidate.

Native build requires a separately approved Linux build environment. The no-push
instruction prevents delivering this new local candidate to Actions now. The
separate `.github/workflows/composerlint-linux-setup.yml` and
`tools/ci_linux_setup.py` are prepared and authorized for one feature-branch run. The registration
copy on main remains explicitly disabled. Do not reuse or rerun the completed single-attempt backend pilot. Before acquiring any missing
GTK4/JSON-GLib development or display-test packages, resolve and review their exact
distribution versions, signed repository metadata and artifact hashes. The current
Mac's installed tools were checked and cannot replace these Linux prerequisites.
Any new action or GUI-test environment image also needs a reviewed commit/digest.

Required checks on that native candidate:

- Compile/link the GTK4 frontend; check its x86-64 ELF identity and runtime ABI
  dependencies. Audit the allowlisted source, resources, licences and ZIP members.
- Independently extract the ZIP, retain executable bits, use paths containing spaces
  and non-ASCII characters, and exercise the actual extracted executable.
- Check both choices, prerequisite recheck, both approvals, useful live logs,
  rejection, window-close cancellation, child reaping and interrupted-download/build
  retries. No successful state before verified backend completion.
- Install full and text modes into fresh private user locations. Full mode must
  compile the exact patch and pass all ten equivalence/source-mapping controls
  without skips. Check real MCP 0.2.0 / 18 tools and text-only renderer refusal.
- Delete/move the extracted app, restart the installed MCP runtime from an unrelated
  working directory, and verify source manifests and all three Skill resources.
- On a real **Arch/Niri/Wayland** desktop, verify linked GTK/runtime libraries,
  actual GCC build, ext4/Btrfs behavior, focus, window sizing/tiling, scaling,
  keyboard access, clipboard and the tester's actual AI client/browser selection.
  Ubuntu/X11 or headless Wayland checks cannot establish this desktop acceptance.
- Confirm production Linux export still refuses, originals stay unchanged and
  safe diagnostics contain no private paths, raw logs or credentials.

If returning a CI-built ZIP, upload only the audited public package, checksum,
test report and intended screenshots after reviewing the pinned upload action,
retention and account storage allowance. Never upload a full installation tree,
raw home directory or user configuration. Keep publication a separate approval.

GTK documents [native Wayland support](https://docs.gtk.org/gtk4/wayland).
The [Niri project](https://github.com/niri-wm/niri) documents compositor/session
integration; this installer does not install GNOME, force XWayland or rewrite
portal/GPU settings. It uses GTK clipboard access rather than a file-picker portal
for connection instructions and safe diagnostics.


## Native CI approval packet — first execution authorized

The primary filename is **ComposerLint-Setup-Linux-x86_64.zip**, with its matching
`.zip.sha256`. No native ZIP/hash exists until the approved run succeeds.

The new workflow is owner-only, public-repository-only, manual-only, feature-branch
only, and restricted to the first run/attempt. It checks the supplied reviewed
40-character commit against both GitHub's dispatch SHA and the actual checkout.
Kenneth authorized one initial execution on 2026-11-10, including the reviewed
prerequisite catalog, branch push and workflow-only default-branch registration.
The feature-branch job and catalog are enabled. The registration copy on main is
explicitly disabled (`false &&`); Linux application changes stay on the feature branch. GitHub's
manual-dispatch registration requirement must be resolved without merging Linux
application changes into main. No existing workflow or Mac release is changed.

Reviewed actions:

- checkout v7.0.1: `3d3c42e5aac5ba805825da76410c181273ba90b1` (existing reviewed pin).
- upload-artifact v7.0.2: `cf430e030ddbb5b0abf93d22962f4752f3646cd9`, checked against
  the official release/commit and action metadata; Node 24 on the hosted runner.

The upload is a temporary Actions artifact, **not a GitHub Release**. Repository
permission remains `contents: read`; no custom secrets, deployment, signing, OIDC,
cache, automatic triggers or automatic job reruns. Successful output is allowlisted:
ZIP, checksum, verification JSON and two authored setup screenshots, at most 20 MiB,
retained for 7 days. Failed checks do not upload a candidate as verified. Logs and
the report may be publicly visible; only reviewed source/authored fixtures and
ordinary ephemeral runner paths are used. No private files are copied from Mac.

Existing runner tools must supply a capability-verified C11/C++20 compiler,
`patch`, `pkg-config`, `ldd`, `unzip`, `Xvfb`/`xvfb-run`, `dbus-run-session`, APT and
Debian metadata tools. Missing tools **refuse**, rather than acquiring compilers,
emulators or additional test frameworks. The Mac's existing tools cannot compile
or run GTK/Linux; nothing new will be installed on the Mac.

Proposed **runner-only** system prerequisites:

| Package | Exact version | SHA-256 |
| --- | --- | --- |
| libgtk-4-dev | 4.14.5+ds-0ubuntu0.10 | 8b5d4994b4882922a11e41d6b45d1cb5cfdc6b5e2ffececa9f1f3ffc1bd9bc69 |
| libjson-glib-dev | 1.8.0-2build2 | e28dfc12339565a48b5ed115f3933b3b0f7b860364a527a4ac2a333ca9426bff |

The 329-entry `linux/build-prerequisites.json` is the reviewable possible required
closure from official noble/main, noble-updates/main and noble-security/main
metadata, read 2026-11-10. Its catalogued package bytes total 136,625,850 bytes;
most base/runtime packages should already exist, but the actual acquisition list
is unknown until the runner's APT simulation. This is **not** a request to install
329 packages. APT selects only required dependencies; recommendations are disabled.
Every selected download must match an allowlisted name/version/architecture/path,
size and SHA-256, plus Ubuntu-keyring-authenticated APT metadata and the internal
Debian package identity. Unlisted changes, removals, downgrades, more than 160
selected archives or more than 160 MiB refuse before installation. No package
artifacts have been downloaded to prepare this catalog; only official metadata
was read. Repository updates may invalidate these pins and require fresh review.

Exact entry command after approval:

```sh
python3 -I -B tools/ci_linux_setup.py
```

The driver builds the following APT option array using its private job directory
(`CI_ROOT=$RUNNER_TEMP/composerlint-linux-setup`, `RUNNER_USER` is the current user):

```sh
APT_OPTIONS=(
  -o "Dir::Etc::sourcelist=$CI_ROOT/apt/ubuntu.sources"
  -o Dir::Etc::sourceparts=-
  -o "Dir::State::lists=$CI_ROOT/apt/lists"
  -o "Dir::Cache::archives=$CI_ROOT/apt/archives"
  -o APT::Get::List-Cleanup=0
  -o "APT::Sandbox::User=$RUNNER_USER"
)
ROOT_PACKAGES=(libgtk-4-dev=4.14.5+ds-0ubuntu0.10 libjson-glib-dev=1.8.0-2build2)
apt-get "${APT_OPTIONS[@]}" update
apt-get "${APT_OPTIONS[@]}" --simulate --no-remove --no-install-recommends install "${ROOT_PACKAGES[@]}"
apt-get "${APT_OPTIONS[@]}" --print-uris --download-only --yes --no-remove --no-install-recommends install "${ROOT_PACKAGES[@]}"
```

The isolated sources contain only HTTPS archive.ubuntu.com (`noble`,
`noble-updates`) and security.ubuntu.com (`noble-security`), main/amd64, using the
runner's existing `/usr/share/keyrings/ubuntu-archive-keyring.gpg`. Global source
files are untouched. Before each bounded HTTPS fetch, the driver runs
`apt-cache "${APT_OPTIONS[@]}" show NAME=VERSION`, then checks the archive's SHA-256
and `dpkg-deb --show '--showformat=${Package} ${Version} ${Architecture}' FILE`.
Only after **all** selected bytes verify, the sole administrative command is:

```sh
sudo apt-get "${APT_OPTIONS[@]}" --no-download --yes --no-remove --no-install-recommends install "${ROOT_PACKAGES[@]}"
```

That command provisions the disposable CI runner only. The volunteer installer
never invokes it, requires root or installs system packages. There is no general
system upgrade, unauthenticated package option or network-download fallback.

The driver next uses the unchanged reviewed Python/uv bootstrap pins and the
shared backend's locked development environment, invokes
`tools/build_linux_setup.py --output CI_ROOT/build`, and independently unzips the
result into a path with spaces and non-ASCII characters. Under the existing runner
`dbus-run-session -- xvfb-run -a -s '-screen 0 1280x1024x24'`, a separate C harness
compiles the production frontend callbacks and tests both modes through the real
helper/JSON-lines backend, explicit matching installation consents, pre-download
cancellation, dependency-plan cancellation, retry, real progress and completion.
The harness is **not** included as an executable in the ZIP. The ordinary packaged
launcher is also opened under the virtual display; its entrypoint and resource
resolution are checked separately. Parent-death watching covers prerequisite
probes as well as installation.

Full mode must build the exact patched renderer and pass all 10 controls without
skips. Fresh MCP initialization/18-tool discovery and all three Skill resources
are checked in both modes after removing the extracted app. Focused filesystem,
process, consent, renderer/export-refusal and package checks use the existing
authored fixtures. Actual AI-client Skill loading and browser/live selection stay
unverified. The virtual X11 display exercises native GTK automation; it proves no
real Ubuntu or Arch/Niri/Wayland desktop, scaling, clipboard or human export flow.

Expected cold-run usage: roughly 20–40 hosted minutes; **45-minute hard job limit**,
one authorized initial execution and no automatic rerun. Standard public Ubuntu
runner compute is expected to use the existing public-repository allowance;
confirm current visibility, Actions permissions and storage/billing headroom before
execution. The requested artifact retention is at most 20 MiB for 7 days. No paid
runner, release storage, repository visibility change or Mac toolchain is proposed.


Local preparation evidence (2026-11-10): **307 passed / one optional MuseScore
skip in 47.52 s**, warnings as errors, including all ten renderer controls on Mac.
Native GTK compilation, actual APT selection/install and the new virtual-display
harness have **not** run. 235 protected baseline files retain their recorded
hashes. The reviewed catalog before the approval flag changed had SHA-256
`a26c2037cb3164bf7a1ec0fa87fef8fd12a8116a0044564aa18f22ca437417c2`.
The approved catalog has SHA-256
`677271c9e10b43bb9b74fe2b0049a137a7fdb123e88e1f8ea9463ad6ab7a7302`.
All 329 package identities are unchanged. Acquisition is authorized only in the
single approved CI run. Repository-readable Actions artifacts were explicitly
approved for this review; they are not private to the repository owner.
