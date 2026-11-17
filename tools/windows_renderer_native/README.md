# Standalone Windows renderer proof

Build from the prepared Verovio source pair described in
[RENDERER-RELEASE.md](../../windows/RENDERER-RELEASE.md). Use the `tie1`
tree for the patched proof and the comparison tree for the upstream
control. The two trees use the same staged fonts and resources.

Use trusted existing CMake, Ninja and LLVM-MinGW tools. An LLVM-MinGW
UCRT toolchain can build the x64 Windows executable on a Mac or on
Windows. For each tree, point CMake at fresh build output and the exact
tool paths. Select `tie1` for the patched tree or `comparison` for the
upstream control.

The recipe makes a build-local copy of Verovio. Its Windows build uses
MinGW's time functions for logging in place of Verovio's Windows shim.

```text
cmake -S tools/windows_renderer_native -B <build-dir> -G Ninja
  -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_SYSTEM_PROCESSOR=x86_64
  -DCMAKE_C_COMPILER=<absolute-x86_64-w64-mingw32-clang>
  -DCMAKE_CXX_COMPILER=<absolute-x86_64-w64-mingw32-clang++>
  -DCMAKE_MAKE_PROGRAM=<absolute-ninja>
  -DCMAKE_BUILD_TYPE=Release
  -DCOMPOSERLINT_VEROVIO_SOURCE=<prepared-tree>
  -DCOMPOSERLINT_RENDERER_VARIANT=tie1
cmake --build <build-dir> --parallel
```

Combine the indented lines into the first command. The output is
`<build-dir>/bin/composerlint-renderer.exe`. On macOS, use
`-DCOMPOSERLINT_HOST_PROOF=ON` with the existing Apple compiler for a
protocol check. A macOS build provides rendering evidence only.

Inspect each Windows executable on the build host with
`prove.py <executable> <prepared-tree> --pe-only --readobj <llvm-readobj>`.
The check reports SHA-256, machine type, Windows executable protections,
and runtime imports. Keep that output with the build evidence.

On clean, standard-user Windows 11 x64 without Visual Studio or the
Visual C++ Redistributable, run the patched executable against the
prepared resources with the same standard-library Python test driver.
Give it the reviewed executable SHA-256 and the comparison paths.

```text
python -B tools/windows_renderer_native/prove.py <patched-executable> <patched-tree>
  --expected-sha256 <patched-sha256>
  --comparison <comparison-executable> --comparison-resources <comparison-tree>
```

The worker reads a JSON request from stdin and writes the response to
stdout, using the existing ComposerLint rendering contract. The driver
sets the worker's working directory to the prepared tree so it finds
`data/` without consulting a global resource location. On Windows the
driver also records the modules actually loaded after rendering.

### Test a private installation or owner replacement

From a reviewed commit with standalone support, run `python -B
tools/package_windows_full.py --standalone --commit <full-sha> --output
<new-directory>` to produce the source-only PowerShell setup ZIP and its
SHA-256. Follow the [Windows installation guide](../../docs/INSTALL.md#windows-x64-full-setup).

A local successful build leaves `native-renderer.zip` in its completed
installation. An owner can use that ZIP as a template for a private
replacement. It contains `patched/composerlint-renderer.exe`,
`comparison/composerlint-renderer.exe`, shared resources under
`resources/data/`, notices under `LICENSES/`, and `proof.json` at its root.
Keep the native JSON interface, update the notices for the actual inputs
and keep the matching source. Update `proof.json` so its `files` object
maps each payload path to its lowercase SHA-256. The table excludes
`proof.json` itself and the optional `README.txt`. Build the ZIP without
explicit directory entries and use the new ZIP's SHA-256 with
`-OwnerRendererBundle` and `-OwnerRendererSha256`. Keep the prior generation
and verify the new one before selecting its client entry.
