"""Seal native Windows renderer evidence for maintainer-only inspection."""

import argparse
import base64
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_windows_renderer import private, recipe
from package_windows_full import checked_zip, read_local_file

ARTIFACT = 'windows-renderer-candidate.enc'
ARCHIVE_LIMIT = 32 * 1024**2
MAGIC = b'ComposerLint Windows renderer retention v1\x00'
KEY_BYTES = 384  # RSA-3072
NONCE_BYTES = 12


def recipient_der(encoded, expected):
    if not re.fullmatch(r'[A-Za-z0-9+/]{1,600}={0,2}', encoded):
        raise ValueError('Supply a base64 DER public key for encrypted CI retention.')
    der = base64.b64decode(encoded, validate=True)
    if recipe.sha(der) != expected:
        raise ValueError('Retention recipient public key fingerprint mismatch.')
    return der


def oaep():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=MAGIC)


def seal(data, der):
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    recipient = load_der_public_key(der)
    if (not isinstance(recipient, RSAPublicKey) or recipient.key_size != KEY_BYTES * 8
            or recipient.public_numbers().e != 65537):
        raise ValueError('Encrypted retention requires an RSA-3072 public key with exponent 65537.')
    if not 0 < len(data) <= ARCHIVE_LIMIT:
        raise ValueError('Unexpected renderer retention archive size.')
    key = AESGCM.generate_key(bit_length=256)
    nonce = secrets.token_bytes(NONCE_BYTES)
    header = MAGIC + recipient.encrypt(key, oaep()) + nonce
    return header + AESGCM(key).encrypt(nonce, data, header)


def unseal(data, secret):
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    header_size = len(MAGIC) + KEY_BYTES + NONCE_BYTES
    if (not isinstance(secret, RSAPrivateKey) or secret.key_size != KEY_BYTES * 8
            or not data.startswith(MAGIC) or not header_size + 16 < len(data) <= ARCHIVE_LIMIT + header_size + 16):
        raise ValueError('Invalid Windows renderer retention envelope.')
    header = data[:header_size]
    key = secret.decrypt(header[len(MAGIC):len(MAGIC) + KEY_BYTES], oaep())
    return AESGCM(key).decrypt(header[-NONCE_BYTES:], data[header_size:], header)


def retained_entries(root, manifest_bytes, read):
    manifest = recipe.full_manifest(manifest_bytes)
    if {entry.name for entry in root.iterdir()} != recipe.BUNDLE_LIMITS.keys():
        raise ValueError('Unexpected retainer input inventory.')
    verified = recipe.verify_full_artifacts(manifest, lambda name, limit: read(root / name, limit))
    return {'windows-full-artifacts.json': manifest_bytes,
            **{'renderer/' + name: data for name, data in verified.items()}}


def audit_archive(data, expected_manifest):
    with ZipFile(BytesIO(data)) as archive:
        expected = {'windows-full-artifacts.json': recipe.MANIFEST_LIMIT,
                    **{'renderer/' + name: limit for name, limit in recipe.BUNDLE_LIMITS.items()}}
        infos = archive.infolist()
        if (len(infos) != len(expected) or set(archive.namelist()) != expected.keys()
                or any(not 0 < info.file_size <= expected[info.filename] for info in infos)):
            raise ValueError('Invalid renderer retention ZIP inventory.')
        raw = archive.read('windows-full-artifacts.json')
        if recipe.sha(raw) != expected_manifest:
            raise ValueError('Retained manifest differs from the digest recorded by CI.')
        manifest = recipe.full_manifest(raw)
        verified = recipe.verify_full_artifacts(manifest, lambda name, _: archive.read('renderer/' + name))
    return raw, verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='mode', required=True)
    encrypt = subparsers.add_parser('seal')
    encrypt.add_argument('bundle', type=Path)
    encrypt.add_argument('manifest', type=Path)
    encrypt.add_argument('output_directory', type=Path)
    encrypt.add_argument('manifest_sha256')
    decrypt = subparsers.add_parser('inspect')
    decrypt.add_argument('encrypted', type=Path)
    decrypt.add_argument('encrypted_sha256')
    decrypt.add_argument('manifest_sha256')
    decrypt.add_argument('private_key', type=Path)
    decrypt.add_argument('output_directory', type=Path, help='New reviewer-only directory')
    args = parser.parse_args()
    if args.mode == 'seal':
        from build_windows_renderer import setup

        if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_OS') != 'Windows'
                or os.environ.get('RUNNER_ARCH') != 'X64'):
            raise ValueError('Only the guarded native Windows build may seal renderer artifacts.')
        der = recipient_der(os.environ['RETENTION_PUBLIC_KEY'], os.environ['RETENTION_KEY_SHA256'])
        manifest_bytes = setup._read_source(args.manifest, recipe.MANIFEST_LIMIT)
        if recipe.sha(manifest_bytes) != args.manifest_sha256:
            raise ValueError('Retention manifest changed after native verification.')
        payload = checked_zip(retained_entries(args.bundle, manifest_bytes, setup._read_source))
        with private.private_directory(args.output_directory, tree_root=True) as output:
            output.write_new(ARTIFACT, seal(payload, der))
    else:
        import getpass
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        envelope = read_local_file(args.encrypted, ARCHIVE_LIMIT + 1024)
        if recipe.sha(envelope) != args.encrypted_sha256:
            raise ValueError('Ciphertext differs from the digest recorded by CI.')
        password = getpass.getpass('Private key passphrase (Enter for an unencrypted key): ')
        secret = load_pem_private_key(read_local_file(args.private_key, 16 * 1024),
                                      password.encode() if password else None)
        raw, files = audit_archive(unseal(envelope, secret), args.manifest_sha256)
        args.output_directory.mkdir(mode=0o700)
        (args.output_directory / 'renderer').mkdir(mode=0o700)
        for name, data in files.items():
            with (args.output_directory / 'renderer' / name).open('xb') as stream:
                stream.write(data)
        with (args.output_directory / 'windows-full-artifacts.json').open('xb') as stream:
            stream.write(raw)


if __name__ == '__main__':
    main()
