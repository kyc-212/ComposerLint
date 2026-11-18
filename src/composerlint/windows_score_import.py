"""Private NTFS workspaces for Windows compressed score imports."""
import atexit
from contextlib import ExitStack
import threading

from . import score_import as shared
from .runtime_paths import temporary_parent


_imports = ExitStack()
_import_lock = threading.Lock()
atexit.register(_imports.close)


def convert_mscz(original):
    from .windows_install_storage import private_workspace
    from .windows_musescore import convert, find_musescore

    converter = find_musescore()
    if converter is None:
        shared.fail('MuseScore Studio 4 is required for MSCZ import. Install it through Windows setup '
                    'or configure COMPOSERLINT_MUSESCORE with the full path to MuseScore4.exe.')
    # Check the compressed source before exposing it to the external converter.
    shared.archive_entries(original)
    with private_workspace(temporary_parent(), shared.PREFIX) as stage:
        copied = stage.path / 'input.mscz'
        output = stage.path / 'converted.musicxml'
        with stage.create((copied.name,)) as stream:
            stream.write(original)
        try:
            with stage.held_file((copied.name,)):
                result = convert(converter, copied, output, stage)
        except TimeoutError:
            shared.fail('MuseScore conversion timed out.')
        if result.returncode:
            raise shared.MuseScoreFailure(result.returncode, result.stderr + result.stdout)
        data = shared.read_regular(output, shared.MAX_SOURCE)
        stage.audit()
    return data, converter.version


def import_compressed_score(source, original, suffix):
    from .windows_install_storage import private_workspace
    from .windows_workers import SetupProcessCleanupUnconfirmed

    try:
        version = None
        if suffix == '.mscz':
            data, version = convert_mscz(original)
            method = 'musescore_cli_musicxml'
            description = 'MuseScore'
        else:
            data = shared.mxl_document(shared.archive_entries(original))
            method = 'musicxml_container_rootfile'
            description = 'compressed MusicXML'
        shared.validate_musicxml(data)
        with ExitStack() as pending:
            stage = pending.enter_context(private_workspace(temporary_parent(), shared.PREFIX))
            working = stage.path / 'score.musicxml'
            receipt = stage.path / 'import.json'
            result = shared.ImportResult(
                original_path=str(source), source_format=suffix[1:], original_sha256=shared.sha(original),
                working_path=str(working), musicxml_sha256=shared.sha(data), conversion_method=method,
                converter_version=version, receipt_path=str(receipt),
                message=f'Imported your {description} score as MusicXML. The original file remains unchanged.',
            )
            with stage.create((working.name,)) as stream:
                stream.write(data)
            if shared.read_regular(source) != original:
                shared.fail('Original changed during import.')
            with stage.create((receipt.name,)) as stream:
                stream.write(result.model_dump_json(indent=2).encode('utf-8'))
            shared.validate_for_reuse(working, result.musicxml_sha256)
            stage.audit()
            with _import_lock:
                _imports.enter_context(pending.pop_all())
            return result
    except SetupProcessCleanupUnconfirmed:
        shared.fail('MuseScore process cleanup could not be confirmed. No working score was admitted.')
    except shared.ComposerLintError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        shared.fail(f'Windows score import failed. {error}')
