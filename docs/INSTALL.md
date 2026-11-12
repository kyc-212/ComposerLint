# Installation and local renderer setup

V0.2.0 renderer source-only distribution; the optional GUI includes ComposerLint’s own compiled launcher. Tested platform evidence is macOS Apple Silicon with
CPython 3.12. Other platforms remain unverified. Do not infer Windows compatibility
from the existence of upstream wheels: filesystem, process and approval adapters
still need work. Intel dependencies remain under investigation without a downgrade.

## Graphical installation (macOS Apple Silicon)

Recommended for ordinary users when the ZIP is available on the official
[GitHub Releases page](https://github.com/kyc-212/ComposerLint/releases):

1. Download `ComposerLint-Setup-macOS-arm64.zip` and its `.zip.sha256` file from the
   same release. The SHA-256 identifies the exact download; it does not prove safety
   or substitute for an Apple-verified developer identity. Optional Terminal check:
   `shasum -a 256 -c ComposerLint-Setup-macOS-arm64.zip.sha256` in the download folder.
2. Double-click the ZIP to extract **ComposerLint Setup.app**. Open that app.
3. This app is **not Developer ID signed or notarized**. If macOS blocks it because
   Apple cannot verify it, close the warning. Only if you trust this exact release,
   open **System Settings → Privacy & Security**, find the message naming
   **ComposerLint Setup**, and choose **Open Anyway**. Review the confirmation and
   choose **Open**; macOS may require your authentication. Apple makes this option
   available for a limited time after the blocked launch. If it is absent, retry
   opening the app once. See [Apple's current instructions](https://support.apple.com/en-us/102445).
   Do not override a warning that says the app is malicious or damaged; stop and
   report it. Never disable Gatekeeper or remove quarantine. ZIP extraction does
   not avoid security checks. Organization policies may disallow this exception.
4. Choose full setup (viewer/editing) or text analysis only. Review the pinned
   Python/uv download and stable source preparation plan. Approve or cancel.
   The app installs private tools, without replacing system Python or using Homebrew.
5. Full setup requires Apple Command Line Tools. If missing, **Install Apple Tools…**
   requests Apple's official process. Complete its licence/security dialogs yourself,
   then choose **Recheck**. First-time tool installation may require Apple's own
   administrator approval. Text-only setup does not compile the renderer.
6. Review the second plan covering locked packages, verified Verovio source, exact
   patch, local compilation and private configuration. Approve to proceed. Watch
   actual progress/logs; Cancel interrupts setup, and Retry rechecks retained work.
7. At completion, use the displayed absolute interpreter, arguments and renderer
   environment setting to connect Codex. Open the displayed stable source folder
   for project Skill discovery; reconnect MCP and start a fresh Codex session.
   No global client settings are edited automatically.

Files live under `~/Library/Application Support/ComposerLint/setup/`: private pinned
bootstrap tools, immutable verified source/Skills, and separate recipe environments.
Keep this stable tree in place. After completion, you may move or remove the downloaded
ZIP and setup app; installed operation does not depend on their location. Existing
configurations, scores, workspaces and exports stay untouched. No human MusicXML
export approval is automated. Full setup retains official Verovio 6.3.0 in the main
environment and builds the patched renderer separately, activating only after the
existing smoke and renderer controls pass.

Updates are manual: download a new reviewed release's ZIP and run it. Unchanged
verified materials can be reused; a changed recipe creates a separate environment
and asks for consent again. No silent update or removal of previous installations.
If setup fails, read the visible error/log location, resolve the stated prerequisite,
network or integrity issue, then retry. Hash failures must be investigated, never
ignored. The Terminal/manual fallback below remains available. Do not move virtual
environments or delete shared private configuration as an uninstall shortcut.

The launcher requires macOS 14+ on native Apple Silicon. Only exact environments
listed in the packaging verification evidence are demonstrated; Intel, Windows,
Linux and other macOS versions remain unverified. The ZIP contains no renderer
binaries, fonts, Python/uv binaries or dependency environments. Internet acquisition
and local compilation still occur after consent. See [packaging/release notes](../macos/README.md).

## Core environment

### Guided setup (macOS Apple Silicon)

Use a native ARM64 terminal and existing CPython 3.12, uv and Apple Command Line
Tools. Setup checks them before changing anything. The demonstrated prerequisite
versions are Python 3.12.13 and uv 0.12.3. Other macOS versions/toolchain combinations
still need actual verification; the renderer's deployment target 13 is not a promise
that the entire dependency set supports every macOS 13+ release.

If prerequisites are missing, install them yourself using the official
[Python](https://www.python.org/downloads/macos/) and
[uv](https://docs.astral.sh/uv/getting-started/installation/) instructions.
Apple developer tools can be requested with `xcode-select --install`; review its
system prompt yourself. No Homebrew, Python, uv or Apple-tool installer runs silently.
Select an existing interpreter explicitly if needed:

```sh
COMPOSERLINT_SETUP_PYTHON=/absolute/path/to/python3.12 ./install.sh
```

Normally just run `./install.sh`. Choose full setup, text analysis only, or cancel.
Setup displays its destinations, packages, pinned sources, compilation and manifest
changes, then asks for explicit approval. There is no automatic yes option.
EOF/cancellation grants no permission. Do not use `sudo`.

```sh
./install.sh --mode full
./install.sh --mode text
./install.sh --check                  # read-only full-install readiness/integrity check
./install.sh --mode text --check      # read-only text-install check
./install.sh --mode full --offline    # consent still required; no network acquisition
```

Environments, downloaded source, built wheel, private manifest and `setup.log` live
under `.composerlint-setup/<recipe-id>/` in the source tree, excluded from Git and
source packaging. The recipe binds source/test/Skill/setup files, dependency pins,
Python identity and host platform. A changed recipe selects a separate installation;
the previous one stays intact. Keep the source tree and installation at their
original absolute paths. Do not move virtual environments or delete referenced artifacts.

Use `--prefix /absolute/private/path` for an isolated installation; its parent must
exist, with no symlinked ancestors. An existing unrelated destination is refused.
For a custom prefix, choose a location outside Git checkouts to keep artifacts private.
Setup never touches an existing `.venv`, application configuration, scores, temporary
workspaces or exports. The printed absolute MCP command and explicit
`COMPOSERLINT_RENDERER_CONFIG` select this installation without changing defaults.
Keep any intentional configuration/data/export overrides in your MCP settings.

Full setup installs ComposerLint from unchanged `uv.lock`, official Verovio from
`requirements-viewer.txt`, and the eight pinned build packages (with all requirements)
from `requirements-renderer-build.txt`. It downloads the reviewed official source,
verifies its hash and the exact patch, builds locally, then installs the complete
patched package into a separate environment. Locked pytest/Hatchling are included
for validation and the ComposerLint build; no external Skill validator is installed.
The core build backend is provisioned from the lock before building without isolation.
Third-party packages require compatible wheels; there is no implicit source-build
fallback for missing prerequisite binaries.

Setup runs one smoke sequence: authored analysis with unchanged input, real stdio
MCP version/tool discovery, and the existing 10 official/patched renderer comparison
cases (ties, warning/refusal behavior and multipage source correspondence). It does
not run a broad analysis campaign or approve/export a candidate. Activation occurs
only after these checks pass. The manifest pins installed renderer resources and
code, the interpreter binary, retained wheel/source and exact patch.

Rerun the same command after a failure. Verified downloads and completed renderer
builds are reused; incomplete builds start from fresh extraction. Changed hashes
refuse, rather than silently generating replacement pins. An interrupted activation
or damaged managed environment may require a fresh `--prefix`; existing evidence
is preserved. A retry checks dependencies without removing the separately installed
official Verovio. `--check` reads integrity/identity records and package versions;
it does not rerun behavioral tests, install packages or access the network.

Strict offline setup requires the managed `uv-cache/` to contain all locked core
and build wheels and metadata. It also requires the exact source archive at
`<prefix>/verovio-6.3.0.tar.gz` and official ARM64 wheel at
`<prefix>/official/verovio-6.3.0-cp310-abi3-macosx_13_0_arm64.whl`.
Missing material fails with no online fallback.
Offline is not a promise that a fresh clone contains dependencies. On failure inspect
`<prefix>/setup.log`, resolve the reported prerequisite/cache/integrity issue, then
retry or follow the manual route below. Previously selected working installations
remain available. Do not copy a prebuilt patched renderer from an unreviewed source.

### Manual fallback

Start from the complete source file set and existing Python 3.12/uv executables.
Use your own absolute paths; keep the installation and cache separate from scores.

```sh
cd /absolute/path/to/ComposerLint
uv sync --locked --no-editable --python /absolute/path/to/python3.12 --no-python-downloads
.venv/bin/python -B examples/normalize.py tests/fixtures/basic.musicxml
.venv/bin/python -B examples/analyze.py tests/fixtures/basic.musicxml
```

`uv sync` installs locked dependencies and the local project. It may download
packages; obtain any required permission first. `--no-dev` omits development tools.
For a prepopulated cache, `uv sync --offline --locked ...` refuses missing artifacts
instead of downloading. No software is installed automatically by an MCP request.
Do not replace locked versions or use an old V0.1 wheel.

The package declares Python >=3.11, but the release baseline is CPython 3.12.
The optional external Skill validator requires PyYAML; it is not a runtime dependency
and is not installed by this project. Built-in Skill/discovery checks can be run
without that external validator. Source fixtures/examples require the source tree;
the Python wheel alone does not contain the Skills or complete tests.

## MCP and Skills

Configure your client to launch this interpreter and argument list as a local
stdio MCP server, with a server-visible source path:

```text
Command: /absolute/path/printed/by/installer/runtime/bin/python
Arguments: -B -m composerlint.server
Environment (full setup): COMPOSERLINT_RENDERER_CONFIG=/absolute/path/printed/by/installer/renderer.json
```

Keep diagnostic output on stderr. The `composerlint-mcp` console entry point is
also available. For manual setup substitute your `.venv/bin/python` and manifest.
No global Codex/client settings are changed by the installer.
Codex is the demonstrated client; generic stdio clients are not
automatically verified viewer/Skill clients. Inspect the negotiated server version
and actual tools after connecting. V0.2.0 currently exposes 18 tools, including
import, review, analysis/evaluation, viewer/selection and temporary editing tools.

Keep `skills/composer-import`, `skills/composer-review` and `skills/composer-edit`
and their relative `.agents/skills/` links. Open the extracted project in a fresh
Skill-capable session and verify discovery. The separate Python-generated sdist omits discovery links to preserve the real
Skill folders; recreate them when using that form. If an archive extractor does not retain
links, recreate each project-local relative link to `../../skills/<skill-name>`
using the installed OS tools, or use the client's supported manual Skill-loading
mechanism. Do not assume Windows symlink behavior has been verified. No global
Skill installation is required for the demonstrated project-local route.

For a missing link only, from the source root use (substitute each of the three
Skill names): `ln -s ../../skills/composer-review .agents/skills/composer-review`.
Create `.agents/skills` first if the sdist omitted that directory. Do not overwrite
an existing link/folder. Restart a Skill-capable session and verify actual discovery.

## Build the patched renderer separately

No renderer binary, font software or upstream source tree is included. Obtain the
[official 6.3.0 source release](https://pypi.org/project/verovio/6.3.0/#files) only
after approving its download. Verify the archive SHA-256:

```text
33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c
```

Verify the included patch SHA-256:

```text
10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60
```

Apply `patches/verovio-6.3.0-deferred-tie-stops.patch` to a fresh extracted source
with `patch --fuzz=0 -p1`. Preserve upstream notices. Only the intended
`MusicXmlInput::CloseTie` source change should result. The patch retains stopping
notes serialized before their starts in another voice; no warning filter is removed.

The demonstrated build baseline uses build 1.6.1, scikit-build-core 1.1.1,
CMake 4.4.4, SWIG 4.5.1, Ninja 1.13.2, packaging 26.3, pathspec 1.1.1 and
pyproject-hooks 1.3.3, plus an existing Apple C++20 compiler/SDK and Python 3.12.
Provision these separately with permission; upstream brew/choco hooks must not run
as an automatic project step. On the demonstrated Mac route set the following
using user-selected absolute source/build/output directories:

```sh
export VEROVIO_BUILD_VERSION=6.3.0+composerlint.tie1
export MACOSX_DEPLOYMENT_TARGET=13
export CMAKE_BUILD_PARALLEL_LEVEL=4
# PATH must include the separately provisioned build environment's bin directory.
/absolute/path/to/build-environment/bin/python -I -B -m build --no-isolation --wheel \
  -Cbuild-dir=/absolute/path/to/fresh-build-directory \
  --outdir /absolute/path/to/artifacts /absolute/path/to/patched-verovio-source
```

Use a separate renderer environment to install the resulting local wheel. It needs
no ComposerLint engine installation: the main server sends bounded JSON to its
renderer subprocess. The wheel must include its own Verovio resources. Preserve
that complete installed package; do not copy only the native extension.

The main ComposerLint environment must also have official `verovio==6.3.0` for the
existing base-version gate. `requirements-viewer.txt` pins the verified original
macOS ARM64 wheel hash and is not a cross-platform lock. Install it separately
with permission. The manifest below selects the patched child, while the official
package remains the main environment's base gate. No unverified fallback is introduced.

## Renderer manifest

Save a server-owner UTF-8 `renderer.json` under the configuration folder, or set
`COMPOSERLINT_RENDERER_CONFIG` to its absolute path in the MCP server environment.
The required structure is:

```json
{
  "package_version": "6.3.0+composerlint.tie1",
  "python": "/absolute/path/to/renderer-environment/bin/python",
  "files_sha256": {
    "/absolute/path/to/installed/verovio/_verovio.abi3.so": "ACTUAL_SHA256",
    "/absolute/path/to/installed/verovio/__init__.py": "ACTUAL_SHA256",
    "/absolute/path/to/installed/verovio/verovio.py": "ACTUAL_SHA256",
    "/absolute/path/to/built-renderer.whl": "ACTUAL_SHA256",
    "/absolute/path/to/verovio-6.3.0-deferred-tie-stops.patch": "ACTUAL_SHA256"
  }
}
```

Replace every placeholder with verified local paths/digests. Relative entries resolve
against the manifest directory, not the checkout. The child package identity is
checked as well as file hashes. Hashes do not establish musical equivalence or license
compliance: also run the authored renderer controls and source correspondence checks.
Keep this machine-specific manifest private. An explicit missing/changed build refuses.

User-controlled rebuilding/re-pinning does not authorize request callers to choose
executables. Keep the existing source/candidate/approval/export guards. Do not suppress
renderer warnings or simplify scores to pass a gate. Official 6.3.0 alone is not an
equivalent setup. Intel/Windows/Linux renderer builds require actual native validation.

## Formats, paths and troubleshooting

- Uncompressed partwise `.musicxml`/`.xml` uses existing admission. MIDI is analysis-only.
- `.mxl` uses the declared container primary root without archive extraction.
- `.mscz` requires separately installed MuseScore Studio 4. Configure an absolute
  `COMPOSERLINT_MUSESCORE` executable path if discovery fails. No converter is bundled.
- Normal conversion requires exit zero and admitted MusicXML. A narrowly verified
  macOS Studio 4.7.3 shutdown-abort recovery compares complete supported native/MusicXML
  facts and binds immutable diagnostics. It is not general acceptance of failed output.
  Unsupported structures, ambiguous logs or mismatches refuse. A different Studio
  release may behave differently and is not automatically verified.
- Recovery supports a conservative single-staff/single-voice native envelope; tuplets,
  multivoice/staff parts and ambiguous/cross-measure ties can refuse. Import success
  does not establish complete engraving/playback fidelity or editing eligibility.
- Core exact/creative editing has conservative structure, tie, lane and timing guards.
  Retain all independent verification; see the authoritative `composer-edit` Skill
  for supported operation families and bounds.
- Use absolute paths on the server host. Relative paths resolve against server cwd.
  Uploading an attachment does not itself make a local path available to the server.
- On macOS configuration/data default to `~/Library/Application Support/ComposerLint`.
  New exports go to `exports/`; only known application/final directories are created,
  and their parent must exist. Explicit export parents must already exist.
- Server-owner overrides: `COMPOSERLINT_CONFIG_DIR`, `COMPOSERLINT_DATA_DIR`,
  `COMPOSERLINT_EXPORT_DIR`, `COMPOSERLINT_TEMP_DIR`. All must be absolute. The temporary
  override must be an existing non-symlink directory; workspaces remain private/unique.
- Missing renderer: use text review or complete the explicit renderer setup. Invalid
  configured hashes are an error, not permission to fall back to an unpatched build.
- Viewer unavailable: check client local-browser/loopback permissions. Keep Host,
  Origin, capability and nonce checks; do not expose the local listener externally.
- Stale or expired source/proposal: refresh existing evidence or reimport as appropriate;
  obtain a new candidate and human approval. Temporary work is not a saved export.

For eligible M6 export, personally inspect and approve the exact proposal in the
viewer/native macOS dialog. Reject/timeout/unavailable confirmation saves no successful
export. M5-only pitch previews remain temporary. The AI must not operate approval
or copy temporary XML as a substitute. Existing independent staged-byte verification,
exclusive file creation and receipt validation remain mandatory.


## Experimental Windows x64 renderer checkpoint

The Windows text pilot verifies the 61 existing prerequisites and creates a
private NTFS runtime. The full pilot adds a pinned local renderer candidate and
requires native loading, all ten existing renderer equivalence controls and the
authored MCP/loopback/editing probe before publishing its completion receipt.
Windows Server and Windows 11 desktop results are separate gates.

### Build with already available inputs

Use an installed **x64 MSVC v143** developer environment. The first native
candidate is Visual Studio 2022 **17.14**, MSVC **19.44 / toolset 14.44** and
Windows SDK **10.0.26100.0**. Record the actual `_MSC_FULL_VER`, toolset directory
version and SDK on the runner. The capability probe compiles, links and runs x64
C++20 with STL and Win32 calls. It checks `cl.exe`, `link.exe`, `rc.exe` and
`mt.exe` in the explicit developer environment. Python needs headers and the
stable-ABI import library `python3.lib`. The renderer may require the compatible
x64 MSVC runtime DLLs alongside the UCRT. Native loading is the gate for those
dependencies. [Microsoft command-line build tools](https://learn.microsoft.com/en-us/cpp/build/building-on-the-command-line?view=msvc-170)
· [Windows runner inventory](https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-Readme.md)

From that developer shell, start with `tools/build_windows_renderer.py --probe-only`
using an existing trusted Python. To build, run the same script with private
CPython 3.12.13 and absolute `--inputs`, `--wheelhouse`, `--uv`
(the private uv 0.12.3 executable) and `--output` (a fresh local NTFS directory
with an existing parent). The script uses existing tools and local inputs. It
installs build dependencies into a private scratch environment from the
hash-pinned binary-only wheelhouse with index access and build isolation
disabled. The upstream CMake/SWIG stable-ABI build compiles just the reviewed
MusicXML change into the patched renderer. No renderer binary is distributed here.

| Input | Version or filename | SHA-256 |
| --- | --- | --- |
| Verovio upstream source | `verovio-6.3.0.tar.gz` | `33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c` |
| Official native wheel | `verovio-6.3.0-cp310-abi3-win_amd64.whl` | `cc8bab65570ccb38d6a440fa13d3e4820df34ee9ca483decdbe8371c4291b1a8` |
| CMake | `cmake-4.4.4-py3-none-win_amd64.whl` | `ad8e0a38b5707e27882701146bdfffecacd80e7703fa0fc36c77528518d545af` |
| Ninja | `ninja-1.13.2-py3-none-win_amd64.whl` | `1293f4078278b70d0ee4b6cc8f3a9e030656c9b2f59909970343c4fe76070118` |
| SWIG | `swig-4.5.1-py3-none-win_amd64.whl` | `0e9218aacd2f221bc6c29282cf9e0546ec68b6aab826239f3d383ff975565914` |

Use the universal wheels pinned in [requirements-renderer-build.txt](../requirements-renderer-build.txt)
for build 1.6.1, scikit-build-core 1.1.1, packaging 26.3, pathspec 1.1.1 and
pyproject-hooks 1.3.3. The wheelhouse and Verovio inputs require separate
acquisition approval. Prefer MSVC and the SDK already present on the runner.
Review the Microsoft component catalog and payload identities separately if
provisioning is needed. [Verovio](https://pypi.org/project/verovio/6.3.0/#files)
· [CMake](https://pypi.org/project/cmake/4.4.4/#files)
· [Ninja](https://pypi.org/project/ninja/1.13.2/#files)
· [SWIG](https://pypi.org/project/swig/4.5.1/#files)

### Full setup and native acceptance

Run `tools/setup_composerlint.py --windows-full-pilot --renderer-bundle ABSOLUTE_DIRECTORY --renderer-sha256 REVIEWED_WHEEL_SHA256`
with the usual Windows setup bootstrap. The bundle must be the locally built
output. Review its `build-provenance.json` and candidate digest before running
setup. Setup verifies the official wheel, source, reviewed patch and candidate
digest, then requests explicit consent for the same 61 pinned bootstrap/runtime
prerequisites. It installs official 6.3.0 in the base environment and
`6.3.0+composerlint.tie1` in a separate renderer environment. The local digest
binds the chosen wheel to the install and receipt. Build provenance is produced
by the reviewed local build and does not independently authenticate an external
wheel. Full setup prints the absolute MCP command and the renderer manifest
environment value. The renderer requires the private installation tree and keeps the verified
manifest, files, and directory handles open for the worker lifetime. Windows
permanent Approve & Export is disabled.

For native verification, start with the existing read-only host snapshot and
Windows tests. Probe and record the compiler and SDK. Build the exact patched
wheel, record the source/patch/postimage and candidate hashes, and exercise the
full setup once from a clean per-user location. Retain the ten-control counts
and the actual MCP probe output. The probe checks real stdio 18-tool discovery,
Unicode-path MusicXML and MXL import, review, analysis, style and persistent
passage reuse, and full-mode loopback notation and temporary edit/discard. In a
fresh text environment, run `tools/ci_windows_mcp.py` without `--full` for
renderer refusal. Exercise a missing/tampered renderer, worker launch refusal,
deadline, cancellation, nested jobs and cleanup. Compare the host snapshot
before and after. Follow up on Windows 11 x64 with actual browser navigation,
selection, AI-client reconnection and the authored editing workflow before
building a graphical installer.
