# Linux GitHub Actions pilot — implementation handoff

Prepared 2026-11-09. The workflow is implemented for review and local commit;
**no GitHub Actions run or native Linux verification has occurred**. No push,
release, visibility/settings change, Linux GUI or human-export support is included.
The prerequisite B adapters are included in this candidate because they were
previously implemented locally and not yet committed.

## Workflow and limits

[Workflow](../.github/workflows/composerlint-linux-feasibility.yml): one owner-only
`workflow_dispatch` job on `ubuntu-24.04` x64, with `contents: read`, 45-minute
job timeout and one active concurrency group. Only run number 1 / attempt 1 can
allocate the job, and its event must still identify a public repository. Built-in reruns and repeat dispatches refuse; each of at most two
diagnostic runs requires a separately approved ordinal/condition change and manual
start. There is no automatic push/PR/schedule trigger.

The only action is official `actions/checkout` **v7.0.1**, commit
`3d3c42e5aac5ba805825da76410c181273ba90b1`. Its official tag reference, action
runtime/inputs and changelog were reviewed; credentials are not persisted. No
secrets, signing key, write token, cross-run cache or artifact-upload action is used.

Repository visibility and Actions settings were checked read-only before
implementation: public repository, Actions enabled, read-only default workflow
permissions, default branch `main`. No repository/account setting was changed.
Standard public-repository runners are free under GitHub's published policy;
job logs and summaries do not consume artifact storage. Reports therefore use the
run summary/log only. No paid runner, storage upload or budget increase is needed
or authorized. See [GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
and [runner limits](https://docs.github.com/en/actions/reference/limits).

## Exact reviewed artifacts

Full URLs, filenames, sizes and independent maintainer evidence are in
[the pin ledger](../src/composerlint/linux-pins.json). Python/uv hashes were matched
to GitHub release metadata and Astral's checksum/download metadata. Official
Verovio/source and all eight build-wheel hashes were checked against PyPI release
metadata; build hashes are already admitted by the unchanged build requirements.
No external software archive was downloaded or installed on the Mac.

| Artifact | SHA-256 |
| --- | --- |
| CPython 3.12.13 / build 20260807 | `506191be3ee7bd190a8834dcdc1b3bc70aab50608deccc711935aa007239cabd` |
| uv 0.12.3 | `600cf9a742aca00d292673b16b5acffaa7b8c269a364ad0c2e79498dcb1fe101` |
| Official Verovio 6.3.0 Linux ABI3 wheel | `0896a708a5b130cc5bab3a168d70435ad8b3e54692088e7e5e281fef11c0b05d` |
| Verovio 6.3.0 source | `33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c` |
| Existing tie patch | `10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60` |
| build 1.6.1 | `ecd351a4be9d35a9eaaba244a7687143c9c7d4aea6ac964e7e7ddab20cbcf4e7` |
| cmake 4.4.4 | `f773a0544c66370408f451acd487c2c643b0dfebb48c5d64f3638bc1bb820238` |
| ninja 1.13.2 | `65a24341b5ac09fcadcc37082660be40a94174e51a937fabf6e2cae26225fa2c` |
| packaging 26.3 | `d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c` |
| pathspec 1.1.1 | `a00ce642f577bf7f473932318056212bc4f8bfdf53128c78bbd5af0b9b20b189` |
| pyproject-hooks 1.3.3 | `5fc53fdac9f7bd63fbcdc868fb5f90b4784d78a53a3d3388cd738b807441a20b` |
| scikit-build-core 1.1.1 | `6f6d6cf600e2e9639fd258ecd11cfc19bd189354b968916a519c6793e7e26237` |
| swig 4.5.1 | `e0cdc78a984dac35aa78091234e5d62909741ec63df6d03b0764075248cb7845` |

The existing `uv.lock`, renderer source/patch, Mac viewer requirements and build-tool
requirements stay byte-identical. Linux has a separate hash-pinned official-wheel
requirement. Compiler policy is the runner-installed `/usr/bin/gcc-13` and
`/usr/bin/g++-13`, both reporting **13.3.0**, with two build workers. Drift, missing
prerequisites, non-Ubuntu 24.04/x86-64 or a non-ext4 setup filesystem refuses before
bootstrap acquisition. No apt upgrade or replacement toolchain is installed.

## Expected run

The stdlib [driver](../tools/ci_linux.py) validates the event/commit/pins and hosted
compiler, verifies and privately extracts exact Python/uv archives, then drives the
existing JSON-lines v1 installation protocol with matching installation consent.
The shared Python backend owns locked dependency setup, official renderer install,
exact patch application, isolated patched compilation, manifests and activation.
Build tools come from reviewed wheels with no index fallback. Archive bytes are
hashed before execution; extracted links are checked by Python's data filter.

Required gates include all **10 renderer controls with zero skips**, source mapping,
real fresh stdio MCP **0.2.0 / 18 tools** from an unrelated directory, a bounded
verified-edit/export-state-machine test set, private filesystem/collision checks,
process cancellation/reuse/pipe bounds and text fallback. Only the existing opt-in
actual MuseScore demonstration may skip. Native approval stays unavailable on Linux:
production export refuses; approval mocks exist only in tests. No CI flag grants
human approval.

The summary records dispatched commit, actual OS/image/compiler/filesystem,
installed versions, exact acquisition identities, native wheel and manifest hashes,
JUnit results and limitations. On failure it retains a failed status and bounded,
JSON-escaped diagnostics. No installation tree, binary/font, score workspace,
private configuration or approval token is uploaded.

## Local evidence and remaining gates

Local host: **macOS 27.2 ARM64 / CPython 3.12.13**. Focused regression:
**199 passed, 1 skipped in 39.07 seconds**, warnings as errors. This includes
Mac installer/protocol/ZIP, renderer, import/export and process checks; the skip is
the existing optional actual MuseScore demonstration. After bounded failure-log
reporting and the public-repository guard were added, the 18 CI-driver tests passed again (overlapping coverage,
not additional native-platform evidence). Logs: `/private/tmp/composerlint-linux-pilot/`.
A disposable wheel built with installed tools contains the shared adapter and both
Linux data inputs; it was not installed. 240 protected baseline files, including
local release ZIPs, Mac frontend/bootstrap/launcher, dependencies, patch, Skills,
fixtures and AGENTS.md, retain their hashes. No musical algorithm or candidate
verification rule was changed. New platform support is confined to the reviewed
Ubuntu POSIX capabilities; Windows and other targets remain closed.
CI policy tests require the full Git checkout; their driver/workflow inputs remain
outside the Mac source-bundle allowlist. The pilot rejects required-control skips,
so missing CI inputs cannot be counted as a native Linux pass.

Native Linux compilation, installed dependency compatibility, 10 Linux renderer
controls and Linux process/filesystem results remain **pending the separately
approved Actions run**. A real Ubuntu desktop volunteer must later verify native
approval, genuine human export, actual AI-client Skill/browser live selection and
GUI/package behavior. GitHub CI cannot close those gates. No Linux full-support
claim is made.

The commit must be separately authorized for GitHub delivery before it can be tested
there; the manual workflow must be present on the default branch. Neither a local
commit nor a future push starts this manual-only workflow. The pilot run itself
still requires Kenneth's separate approval. The public Mac installer/release is
unchanged. No VM or emulator was installed on the Mac.
