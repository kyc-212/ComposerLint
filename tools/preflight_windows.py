#!/usr/bin/env python3
"""Inspect Windows setup compatibility without installing anything."""

import sys

# Also protect source imports when the user omits -B.
sys.dont_write_bytecode = True

import argparse
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    # Load the stdlib-only preflight without importing the application or its
    # third-party dependencies. Use the reviewed source adjacent to this tool.
    name = '_composerlint_preflight'
    spec = ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = [str(Path(__file__).absolute().parents[1] / 'src' / 'composerlint')]
    sys.modules[name] = module_from_spec(spec)
    try:
        preflight = import_module(f'{name}.windows_preflight')
        report = preflight.inspect()
        preflight.print_report(report)
        return 0 if report.compatible else 1
    finally:
        for module in tuple(sys.modules):
            if module == name or module.startswith(f'{name}.'):
                del sys.modules[module]


if __name__ == '__main__':
    raise SystemExit(main())
