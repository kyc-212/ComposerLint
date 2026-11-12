"""Reviewed input identities for the native Windows renderer candidate."""

import hashlib
from pathlib import Path

BUILD = '6.3.0+composerlint.tie1'
OFFICIAL_WHEEL = 'verovio-6.3.0-cp310-abi3-win_amd64.whl'
OFFICIAL_SHA256 = 'cc8bab65570ccb38d6a440fa13d3e4820df34ee9ca483decdbe8371c4291b1a8'
PATCHED_WHEEL = f'verovio-{BUILD}-cp310-abi3-win_amd64.whl'
SOURCE_ARCHIVE = 'verovio-6.3.0.tar.gz'
SOURCE_SHA256 = '33dc3b6816896a649198acd7cce291f54636fbfffa893dd78d84c3073b40d87c'
PATCH = 'verovio-6.3.0-deferred-tie-stops.patch'
PATCH_SHA256 = '10965e05ca1ffca8e9dd8dbf940a8f9313a95b4b414979afac2ff12b91edcd60'
PREIMAGE = '0def2542a241989f2368fe9b54e67db474242fc0d24b27c48af8dac5ee499ab5'
POSTIMAGE = '4235d47c8cf9939ca09858a0d7d15c56eb1b9debdd8ceb6f067bdce536605ad9'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_bytes(path, expected, limit):
    from .windows_private import read_stable
    data, stamp = read_stable(path, limit=limit)
    if stamp.links != 1 or sha(data) != expected:
        raise ValueError(f'Renderer input does not match its SHA-256 pin: {Path(path).name}')
    return data


def apply_reviewed_patch(source, patch):
    """Apply the single reviewed hunk to the exact upstream file bytes."""
    if sha(source) != PREIMAGE or sha(patch) != PATCH_SHA256:
        raise ValueError('Verovio preimage or reviewed patch differs.')
    lines = patch.splitlines(keepends=True)[3:]
    before = b''.join(line[1:] for line in lines if line.startswith((b' ', b'-')))
    after = b''.join(line[1:] for line in lines if line.startswith((b' ', b'+')))
    if source.count(before) != 1:
        raise ValueError('Reviewed Verovio patch context must match once.')
    result = source.replace(before, after, 1)
    if sha(result) != POSTIMAGE:
        raise ValueError('Verovio patched postimage differs.')
    return result
