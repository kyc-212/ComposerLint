# Linux setup distribution boundaries

ComposerLint's original GTK4 frontend, bootstrap bridge, CSS, SVG artwork and build
tool are supplied under the repository's MIT licence. The frontend source is
included for review/rebuilding. The ZIP must retain the repository LICENSE,
THIRD_PARTY_NOTICES.md, LICENSES tree and renderer patch notices in its Source tree.

The frontend dynamically links system GTK4, GLib/GIO and JSON-GLib. Their library
binaries, fonts and icon themes are **not bundled** by this packager. They remain
separately installed distribution components:

- [GTK4](https://www.gtk.org/): LGPL-2.1-or-later.
- [GLib/GIO](https://docs.gtk.org/glib/): LGPL-2.1-or-later.
- [JSON-GLib](https://gnome.pages.gitlab.gnome.org/json-glib/): LGPL-2.1-or-later.

No Python, uv, official/patched Verovio binary or renderer font is embedded in the
ZIP. The user's approved setup downloads pinned prerequisites and builds the exact
patched renderer locally; the existing source/patch/resource integrity and notice
rules still apply. Its installed dependencies keep their upstream licences.

An exact-candidate distribution audit remains required before publication. This
document is not permission to bundle system runtime libraries or redistribute a
prebuilt renderer. Any later private GTK runtime bundle needs its own exact pins,
source/notice/relinking review and approval.
