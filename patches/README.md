# Verovio deferred-tie patch

`verovio-6.3.0-deferred-tie-stops.patch` changes only
`MusicXmlInput::CloseTie()` in official Verovio 6.3.0. It retains a stopping note
once when another voice serializes it before its start. Existing matching and
warning refusal remain active. Official 6.3.0 failed the authored one/four-measure
deferred-tie controls; the patched build returned exact expected endpoints.

The patch is **LGPL-3.0-only**, independently of ComposerLint's MIT license.
Preserve the [modification/author notice](../LICENSES/third-party/verovio/LOCAL-TIE-FIX-NOTICE.txt)
and full [LGPLv3](../LICENSES/third-party/verovio/COPYING.LESSER) and
[GPLv3](../LICENSES/third-party/verovio/COPYING) texts. No renderer binary or font
software is supplied, and no upstream endorsement is implied.

Follow [source build and manifest instructions](../docs/INSTALL.md). Obtain the
[official 6.3.0 source](https://pypi.org/project/verovio/6.3.0/#files) separately and
verify archive SHA-256:
`33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c`.
Verify patch SHA-256:
`10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60`.

Apply with `patch --fuzz=0 -p1`. Use a separately provisioned build environment,
`VEROVIO_BUILD_VERSION=6.3.0+composerlint.tie1`, the native C++20 toolchain and Mac
deployment target 13 for the demonstrated route. Keep upstream source/resources
and notices intact. Instructions do not authorize automatic downloads/installations
or upstream brew/choco hooks. Intel/Windows/Linux builds are not verified.

After separate renderer installation and explicit server-owner manifest creation,
run the authored controls from the ComposerLint source directory:

```sh
COMPOSERLINT_RENDERER_EQUIVALENCE=1 .venv/bin/python -B -m pytest -q \
  tests/test_renderer_packaging_equivalence.py
```

These compare the official 6.3.0 base environment with the configured patched child,
cover forward/open/deferred ties, invalid notation and complete authored multipage
mapping. Hashes alone are insufficient. Preserve source/candidate/approval/export
checks; never suppress warnings or simplify a score to obtain a pass.

[Source versus binary requirements](../docs/licensing/renderer-redistribution.md)
remain separate. The source candidate contains this patch, attribution and build
instructions. Future binary/font redistribution requires complete artifact-specific
license/source review and separate publication approval.
