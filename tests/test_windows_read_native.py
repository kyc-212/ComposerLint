"""Native pilot controls. Run with isolated CPython and the standard library."""

import ctypes
from ctypes import wintypes as win
import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows')
class WindowsReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x0800)
        kernel.GetCurrentProcess.restype = win.HANDLE
        kernel.IsWow64Process2.argtypes = (win.HANDLE, ctypes.POINTER(win.USHORT),
                                          ctypes.POINTER(win.USHORT))
        kernel.IsWow64Process2.restype = win.BOOL
        process, machine = win.USHORT(), win.USHORT()
        if not kernel.IsWow64Process2(kernel.GetCurrentProcess(), ctypes.byref(process),
                                     ctypes.byref(machine)):
            raise ctypes.WinError()
        if process.value or machine.value != 0x8664:
            raise RuntimeError('The pilot requires native Windows x64 Python.')
        print(f'Windows version {sys.getwindowsversion()}; '
              f'{platform.python_implementation()} {platform.python_version()}; '
              f'runner image {os.environ.get("ImageVersion", "local")}', flush=True)

        # Exercise both source modules without importing the app or its dependencies.
        name = '_composerlint_windows_pilot'
        spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        spec.submodule_search_locations = [str(Path(__file__).resolve().parents[1] /
                                               'src' / 'composerlint')]
        sys.modules[name] = importlib.util.module_from_spec(spec)
        cls.addClassCleanup(lambda: sys.modules.pop(name, None))
        cls.reader = importlib.import_module(name + '.windows_read')
        cls.host = importlib.import_module(name + '.platform_support')
        for module in (cls.reader, cls.host):
            cls.addClassCleanup(sys.modules.pop, module.__name__, None)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='composerlint Windows pilot ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / '音楽 🎵'
        self.folder.mkdir()
        self.score = self.folder / 'Étude.musicxml'
        self.data = b'<score-partwise/>\r\n\x1a'
        self.score.write_bytes(self.data)

    def test_reads_binary_bytes_from_unicode_and_mixed_separator_paths(self):
        for path in (self.score, str(self.score).replace('\\', '/')):
            with self.subTest(path=path), self.reader.open_regular(path) as stream:
                self.assertFalse(os.get_inheritable(stream.fileno()))
                self.assertEqual(stream.read(), self.data)
        self.assertEqual(self.score.read_bytes(), self.data)

    def test_long_path_uses_relative_opens(self):
        extended_root = Path('\\\\?\\' + str(self.root))
        start = extended_root / ('x' * 120)
        start.mkdir()
        self.addCleanup(shutil.rmtree, start)
        leaf = start / ('y' * 120) / 'long.musicxml'
        leaf.parent.mkdir(parents=True)
        leaf.write_bytes(self.data)
        raw = str(leaf)[4:]
        self.assertGreater(len(raw), 260)
        with self.reader.open_regular(raw) as stream:
            self.assertEqual(stream.read(), self.data)

    def test_rejects_junction_in_path(self):
        from _winapi import CreateJunction

        junction = self.root / 'junction'
        CreateJunction(str(self.folder), str(junction))
        self.addCleanup(junction.rmdir)
        redirected = junction / self.score.name
        self.assertEqual(redirected.read_bytes(), self.data)
        with self.assertRaisesRegex(OSError, 'reparse points'):
            with self.reader.open_regular(redirected):
                self.fail('Junction target was opened')

    def test_refuses_unsafe_syntax_missing_files_and_directories(self):
        for path in (r'C:relative.xml', r'\\server\share\score.xml', r'\\?\C:\a.xml',
                     str(self.score) + ':stream', str(self.score) + '.', self.folder / 'NUL.xml'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                with self.reader.open_regular(path):
                    self.fail('Ambiguous path was opened')
        with self.assertRaises(FileNotFoundError):
            with self.reader.open_regular(self.folder / 'missing.xml'):
                self.fail('Missing file was opened')
        with self.assertRaises(OSError):
            with self.reader.open_regular(self.folder):
                self.fail('Directory was opened as a file')

    def test_sharing_guards_release_after_exception(self):
        moved_file = self.score.with_name('renamed.musicxml')
        moved_folder = self.folder.with_name('renamed')
        with self.assertRaisesRegex(RuntimeError, 'end the read'):
            with self.reader.open_regular(self.score) as stream:
                for source, destination in ((self.score, moved_file), (self.folder, moved_folder)):
                    with self.subTest(source=source), self.assertRaises(PermissionError):
                        source.rename(destination)
                with self.assertRaises(PermissionError):
                    self.score.open('r+b').close()
                self.assertEqual(stream.read(), self.data)
                raise RuntimeError('end the read')
        self.score.rename(moved_file)
        self.folder.rename(moved_folder)
        self.assertEqual((moved_folder / moved_file.name).read_bytes(), self.data)

    def test_existing_application_gates_remain_closed(self):
        recipe = self.host.setup_recipe()
        self.assertEqual(recipe.target, 'windows-x64')
        self.assertEqual(recipe.python_relative, 'Scripts/python.exe')
        self.assertFalse(recipe.installable)
        for operation in ('Private filesystem access', 'Protected score-file reading',
                          'Exclusive setup locking', 'Owned process-tree lifecycle',
                          'Worker pipe transport', 'Safe MusicXML export'):
            with self.subTest(operation=operation):
                with self.assertRaises(self.host.UnsupportedPlatformOperation):
                    self.host.require_security(operation)
        self.assertIsNone(self.host.approval_method())


if __name__ == '__main__':
    unittest.main()
