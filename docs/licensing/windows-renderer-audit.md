# Windows x64 renderer distribution audit

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
