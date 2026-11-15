"""Offline package, manifest, and restricted retention controls for Windows Full setup."""

from importlib.util import find_spec
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import package_windows_full as package
import windows_renderer_retention as retention
from build_windows_renderer import recipe, setup


def manifest_bytes():
    ci = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
          'run_id': 987, 'run_number': 27, 'attempt': 1, 'image': 'win25', 'image_version': '20261115.1'}
    files = {name: {'size': 1, 'sha256': recipe.FIXED_HASHES.get(name, 'a' * 64)}
             for name in recipe.BUNDLE_LIMITS}
    return json.dumps({'schema': 1, 'ci': ci, 'files': files}).encode('ascii')


class FullDistributionTests(unittest.TestCase):
    def test_exact_inventory_fixed_pins_and_duplicate_json_keys(self):
        raw = manifest_bytes()
        recipe.full_manifest(raw)
        for update in (
                lambda obj: obj['files'].pop(recipe.PROVENANCE),
                lambda obj: obj['files'].update(extra={'size': 1, 'sha256': 'a' * 64}),
                lambda obj: obj['files'][recipe.OFFICIAL_WHEEL].update(sha256='0' * 64),
                lambda obj: obj['files'][recipe.PATCHED_WHEEL].update(size=True),
                lambda obj: obj['files'][recipe.PROVENANCE].update(size=recipe.BUNDLE_LIMITS[recipe.PROVENANCE] + 1),
                lambda obj: obj['ci'].update(attempt=2),
        ):
            with self.subTest(update=update):
                obj = json.loads(raw)
                update(obj)
                with self.assertRaises(ValueError):
                    recipe.full_manifest(json.dumps(obj).encode('ascii'))
        with self.assertRaisesRegex(ValueError, 'Duplicate keys'):
            recipe.full_manifest(raw.replace(b'"schema": 1', b'"schema": 1, "schema": 1'))

    def test_full_backend_refuses_replaced_manifest_before_touching_bundle(self):
        with patch.object(setup, '_read_source', return_value=manifest_bytes()) as read:
            with self.assertRaisesRegex(setup.SetupError, 'differs from the verified setup package'):
                setup.with_renderer(None, ROOT, r'C:\private\renderer', '0' * 64)
        read.assert_called_once_with(ROOT / recipe.FULL_MANIFEST, recipe.MANIFEST_LIMIT)

    def test_full_backend_holds_the_directory_before_reading_its_inventory(self):
        raw = manifest_bytes()
        with (patch.object(setup, '_read_source', return_value=raw) as read,
              patch.object(setup, 'stable_directory', side_effect=PermissionError('Unsafe NTFS chain')) as hold,
              patch.object(Path, 'iterdir', side_effect=AssertionError('Listed without a held directory')),
              self.assertRaisesRegex(PermissionError, 'Unsafe NTFS chain')):
            setup.with_renderer(None, ROOT, r'C:\private\renderer', recipe.sha(raw))
        hold.assert_called_once()
        read.assert_called_once_with(ROOT / recipe.FULL_MANIFEST, recipe.MANIFEST_LIMIT)

    def test_artifact_and_provenance_bytes_are_independently_verified(self):
        payloads = {name: name.encode() for name in recipe.BUNDLE_LIMITS if name != recipe.PROVENANCE}
        obj = json.loads(manifest_bytes())
        expected = {name: recipe.sha(data) for name, data in payloads.items()}
        provenance = dict(official_sha256=expected[recipe.OFFICIAL_WHEEL],
                          source_sha256=expected[recipe.SOURCE_ARCHIVE],
                          patch_sha256=expected[recipe.PATCH], preimage_sha256=recipe.PREIMAGE,
                          postimage_sha256=recipe.POSTIMAGE, wheel_sha256=expected[recipe.PATCHED_WHEEL])
        payloads[recipe.PROVENANCE] = json.dumps(provenance).encode()
        obj['files'] = {name: {'size': len(data), 'sha256': recipe.sha(data)}
                        for name, data in payloads.items()}
        with patch.multiple(recipe, FIXED_HASHES={name: expected[name] for name in recipe.FIXED_HASHES},
                            OFFICIAL_SHA256=expected[recipe.OFFICIAL_WHEEL],
                            SOURCE_SHA256=expected[recipe.SOURCE_ARCHIVE], PATCH_SHA256=expected[recipe.PATCH]):
            manifest = recipe.full_manifest(json.dumps(obj).encode())
            read = lambda name, _: payloads[name]
            self.assertEqual(recipe.verify_full_artifacts(manifest, read), payloads)
            raw = json.dumps(obj).encode()
            archive = package.checked_zip({'windows-full-artifacts.json': raw,
                                           **{'renderer/' + k: v for k, v in payloads.items()}})
            self.assertEqual(retention.audit_archive(archive, recipe.sha(raw)), (raw, payloads))
            with self.assertRaisesRegex(ValueError, 'manifest differs'):
                retention.audit_archive(archive, '0' * 64)
            provenance['postimage_sha256'] = 'f' * 64
            payloads[recipe.PROVENANCE] = json.dumps(provenance).encode()
            with self.assertRaisesRegex(ValueError, 'artifact differs.*build-provenance.json'):
                recipe.verify_full_artifacts(manifest, read)
            manifest['files'][recipe.PROVENANCE]['sha256'] = recipe.sha(payloads[recipe.PROVENANCE])
            with self.assertRaisesRegex(ValueError, 'disagrees.*postimage_sha256'):
                recipe.verify_full_artifacts(manifest, read)

    def test_reviewed_setup_zip_carries_install_guide_and_the_same_manifest(self):
        raw = manifest_bytes()
        entries = package.setup_entries(raw)
        payload = package.checked_zip(entries)
        with ZipFile(BytesIO(payload)) as archive:
            self.assertEqual(archive.read(recipe.FULL_MANIFEST), raw)
            self.assertIn(b'### Windows x64 Full setup', archive.read('docs/INSTALL.md'))
            self.assertIn(b'docs/INSTALL.md#windows-x64-full-setup', archive.read('START-HERE.md'))
            self.assertNotIn(recipe.PATCHED_WHEEL, archive.namelist())
            expected = {name: archive.read(name) for name in archive.namelist() if name != 'FILES.sha256'}
            self.assertEqual(archive.read('FILES.sha256'), package.inventory(expected))
        self.assertEqual(package.checked_zip(entries), payload)

    def test_zip_rejects_windows_path_aliases_and_no_retention_zip_can_omit_provenance(self):
        for name in ('../x', '/x', 'file:stream', 'dir/NUL.txt', 'foo.'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package.checked_zip({name: b'fixture'})
        with self.assertRaisesRegex(ValueError, 'Case-colliding'):
            package.checked_zip({'Name.txt': b'a', 'name.txt': b'b'})
        incomplete = package.checked_zip({'windows-full-artifacts.json': manifest_bytes()})
        with self.assertRaisesRegex(ValueError, 'inventory'):
            retention.audit_archive(incomplete, 'a' * 64)

    @unittest.skipUnless(find_spec('cryptography'), 'Requires existing cryptography environment.')
    def test_envelope_creates_a_new_session_key_and_refuses_wrong_key_or_tampering(self):
        from base64 import b64encode
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        secret = generate_private_key(public_exponent=65537, key_size=3072)
        der = secret.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        encoded = b64encode(der).decode('ascii')
        self.assertEqual(retention.recipient_der(encoded, recipe.sha(der)), der)
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            retention.recipient_der(encoded, '0' * 64)
        payload = b'known test bytes' * 100
        envelope = retention.seal(payload, der)
        self.assertNotIn(payload, envelope)
        self.assertNotEqual(envelope, retention.seal(payload, der))
        self.assertEqual(retention.unseal(envelope, secret), payload)
        with self.assertRaises(InvalidTag):
            retention.unseal(envelope[:-1] + bytes([envelope[-1] ^ 1]), secret)
        other = generate_private_key(public_exponent=65537, key_size=3072)
        with self.assertRaises(ValueError):
            retention.unseal(envelope, other)
