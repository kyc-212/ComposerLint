"""Human-only native confirmation and separate, exclusive M6 export.

No MCP/WebMCP approval API. The native dialog must be operated by the composer;
agents must never click it. This protects application routes, not privileged host
software capable of controlling the desktop or modifying this process.
"""
from pathlib import Path
import hashlib
import json
import os
import secrets
import time
from composerlint.review import canonical, digest
from composerlint.viewer_source import fail
from composerlint.runtime_paths import export_dir, export_creatable_dirs
from composerlint import platform_support as host

EXPORT_ROOT = export_dir()


def binding(w):
    p = w['proposal']
    return dict(workspace_id=w['id'], workspace_revision=w['revision'],
        view_instance_id=w['entry']['instance'], selection_revision=w['entry']['revision'],
        source_sha256=w['source_sha256'], options_sha256=w['options_sha256'],
        base_snapshot=w['snapshot'], proposal_id=p['proposal_id'],
        candidate_sha256=p['candidate_sha256'], verification_sha256=p['verification_sha256'],
        brief_sha256=p['operation']['brief_sha256'], policy=p['operation']['policy'])


def confirm(message):
    return host.confirm_export(message)


def export_directory():
    """Walk from / using held directory descriptors; reject every symlink."""
    try:
        return host.export_directory(EXPORT_ROOT, export_creatable_dirs())
    except host.UnsupportedPlatformOperation as exc:
        fail('unsafe_export_path', str(exc))
    except OSError:
        fail('unsafe_export_path', 'Export directory must have regular, non-symlink parents.')


def read_at(fd, name):
    try:
        return host.read_at(fd, name)
    except OSError as exc:
        fail('unsafe_export_path', str(exc))


def write_at(fd, name, raw):
    host.write_at(fd, name, raw)


def eligible(engine, ident, expected):
    w = engine.find(ident)
    engine.current(w); engine.check_candidate(w)
    p = w['proposal']
    if (w['state'] != 'verified' or w['pending'] or not p
            or p['operation']['kind'] != 'expressive_edit_batch'
            or p['preview_status'] != 'ready' or p['verification_status'] != 'verified'
            or not p['verification']['gates'] or not all(v is True for v in p['verification']['gates'].values())):
        fail('export_unavailable', 'A current verified M6 creative candidate and complete notation are required.')
    if binding(w) != expected: fail('stale_approval','Candidate, selection or workspace changed; inspect again.')
    return w


def approve_export(engine, ident, expected):
    from composerlint.editing_runtime import worker
    try: host.require_security('Safe MusicXML export')
    except host.UnsupportedPlatformOperation as exc: fail('export_unavailable', str(exc))
    if host.approval_method() is None:
        fail('export_unavailable', 'Native human approval is unavailable on this platform; nothing exported.')
    with engine.lock:
        w = eligible(engine, ident, expected)
        old = w.get('export_receipt')
        if old and old['binding'] == expected:
            fd = export_directory()
            try:
                if (hashlib.sha256(read_at(fd, Path(old['output_path']).name)).hexdigest() != expected['candidate_sha256']
                        or json.loads(read_at(fd, Path(old['receipt_path']).name)) != old):
                    fail('export_modified','Previously exported file or receipt changed; no retry write.')
                return old
            finally: os.close(fd)
        if w.get('export_pending'): fail('revision_conflict','Confirmation/export is already pending.')
        ticket = secrets.token_hex(16); w['export_pending'] = ticket
        p = w['proposal']
        name = f"{w['path'].stem}-M6-{p['proposal_id']}.musicxml"
        message = (f"Save a new MusicXML copy to:\n{EXPORT_ROOT / name}\n\n"
            f"Candidate: {p['candidate_sha256']}\n"
            f"Target: {canonical(p['target'] | {'notes': []})}\n\n"
            f"Exact changes: {canonical(p['changes'])}\n\n"
            f"Composer intention: {p['verification']['brief_binding']['composer_request']}\n"
            "Only the listed edits and preservation checks are verified. A requested mode, mood or improvement is not guaranteed. "
            + p['verification']['notation']['limitation'] + '\nOriginal stays unchanged. Reject cancels export.')
    fd = None; stage = None
    try:
        if not confirm(message):
            fail('approval_rejected','Human confirmation rejected, timed out or unavailable; nothing exported.')
        with engine.lock:
            w = eligible(engine, ident, expected)
            if w.get('export_pending') != ticket: fail('stale_approval','Confirmation no longer current.')
            approval = dict(binding=expected, method=host.approval_method(), approved_at=time.time(), one_use_id=ticket)
            fd = export_directory()
            stage = '.' + ticket + '.pending.musicxml'
            raw = Path(w['candidate_path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected['candidate_sha256']: fail('stale_proposal','Candidate changed.')
            write_at(fd, stage, raw)
            request = dict(action='export', original=str(w['original']), candidate=str(EXPORT_ROOT / stage),
                options=w['options_request'], expected_snapshot=w['snapshot'], selection=w['expressive_brief'].brief.selection.model_dump(mode='json'),
                target=dict(part_id=p['target']['part_id'],lane_mode=p['target']['lane_mode'],voice_id=p['target']['voices'][0]['voice_id']), operation=p['operation'], brief=w['expressive_brief'].brief.model_dump(mode='json'))
        checked = worker(request)
        with engine.lock:
            w = eligible(engine, ident, expected)
            v = checked['verification']
            if (not all(x is True for x in v['gates'].values()) or v['changes'] != p['changes']
                    or v['constraints'] != p['verification']['constraints']
                    or v.get('correspondence') != p['verification'].get('correspondence')
                    or checked['manifest']['scope']['source_sha256'] != expected['candidate_sha256']
                    or read_at(fd, stage) != raw):
                fail('export_verification_failed','Staged bytes failed independent preservation/reopen checks.')
            # Ensure the pathname still names our held destination before publishing.
            checkfd = export_directory()
            try:
                if os.fstat(checkfd)[:2] != os.fstat(fd)[:2]: fail('unsafe_export_path','Export directory changed.')
            finally: os.close(checkfd)
            receipt = dict(binding=expected, human_approval=approval, output_path=str(EXPORT_ROOT/name),
                receipt_path=str(EXPORT_ROOT/(name+'.receipt.json')), exported_sha256=hashlib.sha256(raw).hexdigest(),
                exact_changes=p['changes'], correspondence=v.get('correspondence',[]), checks=v['gates'], constraints=v['constraints'],
                analysis=v['analysis'], limitation=v['notation']['limitation'],
                musical_intent=p['verification']['brief_binding'],
                notice='Exact edits and preservation verified. Expressive success and requested mode remain interpretation; MuseScore playback not tested.')
            write_at(fd, '.'+ticket+'.receipt.pending', canonical(receipt).encode())
            # link is atomic and refuses any existing name, including symlinks.
            host.publish_exclusive(fd, stage, name)
            host.publish_exclusive(fd, '.'+ticket+'.receipt.pending', name+'.receipt.json')
            if read_at(fd,name) != raw: fail('export_verification_failed','Published bytes differ.')
            engine.current(w)
            os.fsync(fd)
            os.unlink(stage, dir_fd=fd); os.unlink('.'+ticket+'.receipt.pending', dir_fd=fd)
            w['export_receipt'] = receipt
            return receipt
    except BaseException as exc:
        if fd is not None and stage is not None:
            try: write_at(fd, '.'+ticket+'.failed.json', canonical(dict(status='failed',stage=stage, reason=str(exc), binding=expected)).encode())
            except OSError: pass
        raise
    finally:
        if fd is not None: os.close(fd)
        with engine.lock:
            if w.get('export_pending') == ticket: w.pop('export_pending',None)
