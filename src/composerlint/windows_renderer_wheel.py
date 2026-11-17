"""Inspect Windows renderer wheels without installing or loading them."""

import base64
import csv
from email.parser import BytesParser
import hashlib
from io import BytesIO, StringIO
import stat
from zipfile import BadZipFile, ZipFile

from .windows_install_storage import _component

TAG = 'cp310-abi3-win_amd64'
DEV_REQUIRES = {
    'build>=1.2; extra == "dev"',
    'cibuildwheel>=3.4; python_version >= "3.11" and extra == "dev"',
    'twine>=5; extra == "dev"',
}
MAX_FILES = 8192
MAX_EXPANDED = 128 * 1024**2
OWNER_ROOT = 'renderer-artifacts/owner/'
PACKAGE_ROOT = 'renderer/Lib/site-packages/'


def require_installed_wheel(raw, version, files):
    for name, data in read_wheel(raw, version).items():
        if files.get(PACKAGE_ROOT + name) != hashlib.sha256(data).hexdigest():
            raise ValueError(f'Installed replacement differs from its verified wheel: {name}')


def read_wheel(raw, version):
    """Return files only after validating the archive, metadata and full RECORD."""
    try:
        return _read_wheel(raw, version)
    except (BadZipFile, csv.Error, RuntimeError) as error:
        raise ValueError(f'Invalid renderer wheel: {error}') from error


def _read_wheel(raw, version):
    dist = f'verovio-{version}.dist-info'
    files, paths = {}, {}
    with ZipFile(BytesIO(raw)) as wheel:
        members = wheel.infolist()
        if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_EXPANDED:
            raise ValueError('Renderer wheel exceeds the inventory or expanded size limit.')
        for member in members:
            directory = member.is_dir()
            name = member.filename.removesuffix('/')
            parts = tuple(_component(part) for part in name.split('/'))
            mode = stat.S_IFMT(member.external_attr >> 16)
            if (member.orig_filename != member.filename or len(parts) > 64
                    or parts[0] not in {'verovio', 'verovio.libs', dist}
                    or (len(parts) == 1 and not directory)
                    or mode not in (0, stat.S_IFDIR if directory else stat.S_IFREG)
                    or member.flag_bits & 1 or (directory and member.file_size)):
                raise ValueError(f'Unexpected renderer wheel member: {name}')
            for depth in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:depth])
                key = prefix.casefold()
                node = (prefix, directory or depth < len(parts))
                if paths.setdefault(key, node) != node:
                    raise ValueError(f'Ambiguous renderer wheel path: {name}')
            if name in files or (directory and name + '/' in files):
                raise ValueError(f'Duplicate renderer wheel member: {name}')
            files[member.filename] = wheel.read(member)
    files = {name: raw for name, raw in files.items() if not name.endswith('/')}
    record_name = f'{dist}/RECORD'
    required = {record_name, f'{dist}/METADATA', f'{dist}/WHEEL',
                'verovio/__init__.py', 'verovio/_verovio.pyd'}
    if not required <= files.keys():
        raise ValueError('Missing renderer wheel metadata or native module.')

    meta = BytesParser().parsebytes(files[f'{dist}/METADATA'], headersonly=True)
    tags = BytesParser().parsebytes(files[f'{dist}/WHEEL'], headersonly=True)
    if (meta.get_all('Name') != ['verovio'] or meta.get_all('Version') != [version]
            or not set(meta.get_all('Requires-Dist', [])) <= DEV_REQUIRES
            or tags.get_all('Wheel-Version') != ['1.0']
            or tags.get_all('Root-Is-Purelib') != ['false'] or tags.get_all('Tag') != [TAG]):
        raise ValueError('Unexpected renderer package metadata, dependencies or Windows ABI.')
    covered = set()
    for name, digest, size in csv.reader(StringIO(files[record_name].decode('utf-8'), newline=''), strict=True):
        if name in covered or name not in files:
            raise ValueError(f'Renderer RECORD has a duplicate or unknown member: {name}')
        covered.add(name)
        if name == record_name:
            if digest or size:
                raise ValueError('Renderer RECORD cannot supply its own digest or size.')
            continue
        content = files[name]
        expected = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode('ascii')
        if digest != 'sha256=' + expected or size != str(len(content)):
            raise ValueError(f'Renderer RECORD differs from member: {name}')
    if covered != files.keys():
        raise ValueError('Renderer RECORD does not cover every wheel file.')
    return files
