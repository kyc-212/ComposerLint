"""Focused CI inventory controls. Use Win32 directory-entry metadata fixtures."""

from pathlib import Path, PureWindowsPath
from runpy import run_path
import stat
import unittest
from unittest.mock import Mock


snapshot_entry = run_path(str(
    Path(__file__).resolve().parents[1] / 'tools' / 'ci_windows_host_files.py'
))['snapshot_entry']
ALIAS_ROOT = PureWindowsPath(r'C:\Users\runneradmin\AppData\Local\Microsoft\WindowsApps')
ALIAS = ALIAS_ROOT / 'python.exe'
ALIAS_METADATA = {
    'attributes': stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_ARCHIVE,
    'reparse_tag': 0x8000001B, 'size': 0, 'created_filetime': 10, 'written_filetime': 20,
}


class WindowsHostSnapshotTests(unittest.TestCase):
    def test_windowsapps_execution_alias_is_recorded_without_opening_it(self):
        read = Mock(return_value=ALIAS_METADATA)
        digest = Mock(side_effect=OSError('The file cannot be accessed by the system'))
        self.assertEqual(snapshot_entry(ALIAS, ALIAS_ROOT, executable=True, metadata=read, digest=digest),
                         {'path': str(ALIAS), 'type': 'app_execution_alias', **ALIAS_METADATA})
        read.assert_called_once_with(ALIAS)
        digest.assert_not_called()
        read.return_value = {**ALIAS_METADATA, 'written_filetime': 21}
        self.assertNotEqual(
            snapshot_entry(ALIAS, ALIAS_ROOT, executable=True, metadata=read, digest=digest),
            {'path': str(ALIAS), 'type': 'app_execution_alias', **ALIAS_METADATA},
        )

    def test_other_reparse_entries_are_errors(self):
        digest = Mock()
        for path, tag, executable in (
            (ALIAS, 0xA000000C, True),
            (PureWindowsPath(r'C:\python.exe'), 0x8000001B, True),
            (ALIAS, 0x8000001B, False),
        ):
            with self.subTest(path=path, tag=tag, executable=executable), self.assertRaisesRegex(
                OSError, 'Unexpected reparse point'
            ):
                snapshot_entry(path, ALIAS_ROOT, executable=executable, digest=digest,
                               metadata=Mock(return_value={**ALIAS_METADATA, 'reparse_tag': tag}))
        digest.assert_not_called()

    def test_regular_executables_are_hashed_and_read_errors_fail(self):
        path = PureWindowsPath(r'C:\hostedtoolcache\Python\python.exe')
        read = Mock(return_value={'attributes': stat.FILE_ATTRIBUTE_ARCHIVE})
        digest = Mock(return_value='a' * 64)
        self.assertEqual(snapshot_entry(path, ALIAS_ROOT, executable=True, metadata=read, digest=digest),
                         {'path': str(path), 'type': 'regular_file', 'sha256': 'a' * 64})
        digest.assert_called_once_with(path)
        digest.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            snapshot_entry(path, ALIAS_ROOT, executable=True, metadata=read, digest=digest)

    def test_missing_config_and_missing_executable_are_distinct(self):
        path = PureWindowsPath(r'C:\config.toml')
        read = Mock(return_value=None)
        self.assertEqual(snapshot_entry(path, ALIAS_ROOT, executable=False, metadata=read),
                         {'path': str(path), 'type': 'missing'})
        with self.assertRaisesRegex(FileNotFoundError, 'Discovered executable disappeared'):
            snapshot_entry(ALIAS, ALIAS_ROOT, executable=True, metadata=read)
