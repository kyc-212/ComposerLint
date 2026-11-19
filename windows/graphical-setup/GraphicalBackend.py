"""Windows GUI transport for the standalone setup backend."""
import contextlib
from dataclasses import asdict
from importlib import import_module
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
import json
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
MAX_MESSAGE = 128 * 1024
MUSESCORE_ASSET = dict(
    filename='MuseScore-Studio-4.7.5.260831071-x86_64.msi',
    url='https://github.com/musescore/MuseScore/releases/download/v4.7.5/'
        'MuseScore-Studio-4.7.5.260831071-x86_64.msi',
    sha256='07b0f3292612e2df4c7e0632deb790add51b218f0ee33f7fecaa6111dd279ec6',
    size=128745472,
)


class SetupCancelled(Exception):
    pass


class Wizard:
    def __init__(self, incoming, outgoing):
        self.incoming = incoming
        self.outgoing = outgoing

    def send(self, kind, **fields):
        message = json.dumps(dict(protocol=1, type=kind, **fields), ensure_ascii=True)
        if len(message) > MAX_MESSAGE:
            raise ValueError('Installer event exceeded its size limit.')
        print(message, file=self.outgoing, flush=True)

    def read(self):
        line = self.incoming.readline(MAX_MESSAGE + 1)
        if not line:
            return {}
        if len(line) > MAX_MESSAGE or not line.endswith('\n'):
            raise ValueError('Invalid installer input.')
        result = json.loads(line)
        if not isinstance(result, dict):
            raise ValueError('Expected an installer input object.')
        return result

    def ask(self, kind, *, plan_id=None, **fields):
        identity = plan_id or uuid4().hex
        self.send(kind, plan_id=identity, **fields)
        reply = self.read()
        if reply in ({}, {'action': 'cancel', 'plan_id': identity}):
            return None
        if reply == {'action': 'approve', 'plan_id': identity} and kind in ('plan', 'musescore_plan'):
            return 'approve'
        if kind == 'choice' and reply.keys() == {'action', 'plan_id', 'answer'} and (
            reply['action'] == 'answer' and reply['plan_id'] == identity
            and isinstance(reply['answer'], str) and len(reply['answer']) <= 32
        ):
            return reply['answer']
        raise ValueError('The response did not match the displayed installer request.')

    def log(self, text):
        for line in text.splitlines():
            if line:
                self.send('log', message=line[:4096])
        return len(text)

    write = log

    def flush(self):
        self.outgoing.flush()


def prepare_musescore(wizard, windows, musescore):
    try:
        converter = musescore.find_musescore()
        if converter:
            return f'MuseScore Studio {converter.version} detected for MSCZ import.'
        asset = windows.Asset(**MUSESCORE_ASSET)
        message = (f'Download {asset.filename} ({asset.size:,} bytes) from MuseScore and open its '
                   f'Windows installer? Follow the installer prompts. Windows may ask for permission.\n\n'
                   f'{asset.url}\nSHA-256: {asset.sha256}')
        if wizard.ask('musescore_plan', message=message) != 'approve':
            return 'MuseScore setup skipped. You can install MuseScore Studio 4 later for MSCZ import.'
        wizard.send('progress', stage='musescore', message='Downloading and verifying the MuseScore installer...')
        data = windows.acquire(asset)
        wizard.send('progress', stage='musescore', message='Finish in the MuseScore installer window.')
        code = musescore.launch_installer(asset, data)
        if code == 1602:
            return 'MuseScore installation was cancelled. ComposerLint setup is complete.'
        if code in (1641, 3010):
            return 'Windows Installer requested a restart. Restart Windows before trying MSCZ import.'
        if code != 0:
            return f'MuseScore setup exited with code {code}. ComposerLint setup is complete.'
        converter = musescore.find_musescore()
        if converter:
            return f'MuseScore Studio {converter.version} detected for MSCZ import.'
        return 'MuseScore setup completed. Set COMPOSERLINT_MUSESCORE if ComposerLint cannot find it.'
    except (OSError, RuntimeError, ValueError) as error:
        wizard.send('log', message=f'MuseScore setup: {error}'[:4096])
        return 'MuseScore setup could not finish. ComposerLint setup is complete.'


def setup(wizard, options, windows, native, musescore):
    mode = options['mode']
    if mode not in ('Full', 'Text'):
        raise ValueError('Choose a supported installer mode.')
    cache = Path(options['cache']) if options['cache'] else None
    stock = Path(options['renderer']) if options['renderer'] else None
    bootstrap = Path(options['bootstrap'])
    verified = None
    reviewed_locations = {}

    def local_asset(asset, path):
        data = native.checked_input(path, asset.size, asset.sha256)
        if len(data) != asset.size:
            raise ValueError(f'Local file has the wrong size: {asset.filename}')
        return data

    def asset_location(asset):
        if asset.filename == bootstrap.name:
            return bootstrap
        if cache is not None:
            candidate = cache / asset.filename
            try:
                candidate.lstat()
            except FileNotFoundError:
                pass
            else:
                return candidate
        return None

    def status(stage, message):
        wizard.send('progress', stage=stage, message=message)

    def fetch(asset):
        # Later portable-tool fallback gets its own decision in the native backend.
        location = (reviewed_locations[asset.filename] if asset.filename in reviewed_locations
                    else asset_location(asset))
        if location is not None:
            status('reuse', f'Checking local file {asset.filename}')
            return local_asset(asset, location)
        status('download', f'Downloading {asset.filename}')
        return windows.acquire(asset)

    def review(plan, assets):
        reviewed_locations.update((asset.filename, asset_location(asset)) for asset in assets)
        rows = [dict(asdict(asset), action='Verify local file' if reviewed_locations[asset.filename] else 'Download')
                for asset in assets]
        renderer = plan.native_renderer.origin if plan.native_renderer is not None else None
        return wizard.ask('plan', plan_id=plan.identity, mode=mode, destination=str(plan.destination),
                          assets=rows, renderer=renderer) == 'approve'

    def choose(prompt):
        answer = wizard.ask('choice', message=prompt)
        if answer is None:
            raise SetupCancelled
        return answer

    def complete(runtime, plan):
        nonlocal verified
        renderer = str(runtime.parents[2] / 'renderer.json') if plan.native_renderer is not None else None
        verified = dict(python=str(runtime), arguments=['-I', '-B', '-X', 'utf8', '-m', 'composerlint.server'],
                        renderer=renderer, skills=str(ROOT / 'skills'), mode=mode)

    try:
        windows.install(
            ROOT, status=status, confirm=choose,
            build=lambda plan, stage, report, **kwargs: windows.populate(plan, stage, report, fetch=fetch, **kwargs),
            standalone=mode == 'Full', native_bundle=stock, review=review, complete=complete,
        )
    except SetupCancelled:
        wizard.send('cancelled')
        return
    if verified is None:
        wizard.send('cancelled')
    else:
        verified['musescore_status'] = prepare_musescore(wizard, windows, musescore)
        wizard.send('complete', **verified)


def main():
    wizard = Wizard(sys.stdin, sys.stdout)
    try:
        if sys.platform != 'win32':
            raise ValueError('This setup bridge requires Windows.')
        name = '_composerlint_windows_setup'
        spec = ModuleSpec(name, loader=None, is_package=True)
        spec.submodule_search_locations = [str(ROOT / 'src' / 'composerlint')]
        sys.modules[name] = module_from_spec(spec)
        with contextlib.redirect_stdout(wizard):
            setup(wizard, wizard.read(), import_module(f'{name}.windows_setup'),
                  import_module(f'{name}.windows_renderer_native'), import_module(f'{name}.windows_musescore'))
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        wizard.send('error', message=str(error)[:4096])
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
