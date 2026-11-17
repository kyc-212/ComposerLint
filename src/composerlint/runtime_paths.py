"""Server-owner paths. No checkout discovery, writes, or request-controlled paths."""
import os
from pathlib import Path
import sys
import tempfile


def absolute(value, label):
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(f'{label} must be an absolute path without parent traversal.')
    return path


def _override(name):
    if name in os.environ:
        return absolute(os.environ[name], name)
    return None


def _base(kind):
    home = Path.home()
    if sys.platform == 'darwin':
        return home / 'Library' / 'Application Support'
    if sys.platform == 'win32':
        return absolute(os.environ.get('LOCALAPPDATA', str(home / 'AppData' / 'Local')), 'LOCALAPPDATA')
    key = 'XDG_CONFIG_HOME' if kind == 'config' else 'XDG_DATA_HOME'
    return absolute(os.environ.get(key, str(home / ('.config' if kind == 'config' else '.local/share'))), key)


def config_dir():
    return _override('COMPOSERLINT_CONFIG_DIR') or _base('config') / 'ComposerLint'


def data_dir():
    return _override('COMPOSERLINT_DATA_DIR') or _base('data') / 'ComposerLint'


def renderer_config():
    explicit = _override('COMPOSERLINT_RENDERER_CONFIG')
    path = explicit if explicit is not None else config_dir() / 'renderer.json'
    # An explicit missing file is an error, never permission to fall back.
    if explicit is not None or path.exists() or path.is_symlink():
        return path
    return None


def export_dir():
    return _override('COMPOSERLINT_EXPORT_DIR') or data_dir() / 'exports'


def export_creatable_dirs():
    """Only known application directories; override parents must already exist."""
    destination = export_dir()
    if 'COMPOSERLINT_EXPORT_DIR' in os.environ:
        return {destination}
    return {data_dir(), destination}


def temporary_parent():
    path = _override('COMPOSERLINT_TEMP_DIR')
    if path is not None:
        if path.is_symlink() or not path.is_dir():
            raise ValueError('COMPOSERLINT_TEMP_DIR must be an existing non-symlink directory.')
        return path
    return Path(tempfile.gettempdir()).resolve()
