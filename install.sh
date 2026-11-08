#!/bin/sh
# No downloads or system changes in this launcher.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_command=${COMPOSERLINT_SETUP_PYTHON:-python3.12}
if ! command -v "$python_command" >/dev/null 2>&1; then
    echo "ComposerLint needs an existing Python 3.12. See docs/INSTALL.md (prerequisites)." >&2
    echo "Select one with COMPOSERLINT_SETUP_PYTHON=/absolute/path/to/python3.12 ./install.sh" >&2
    exit 1
fi
exec "$python_command" -I -B "$project_dir/tools/setup_composerlint.py" "$@"
