"""Temporary workspaces, revision guards, finite workers and owned cleanup."""
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from functools import partial
from pathlib import Path
import atexit
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
from composerlint.platform_support import read_score_bytes
from composerlint.review import canonical,digest
from composerlint.viewer_source import fail,ViewerFailure
from composerlint.editing_models import EditResult
from composerlint.runtime_paths import temporary_parent
from composerlint import platform_support as host


def read_source(path,expected_identity=None):
    p=Path(path).expanduser().absolute()
    from composerlint.score_import import verify_import
    from composerlint.errors import ComposerLintError
    try: verify_import(p)
    except ComposerLintError: fail('stale_source','Imported source changed; import the original again.')
    if p.suffix.lower() not in ('.xml','.musicxml') or p.is_symlink():fail('unsupported_edit','Use a regular uncompressed MusicXML file, not a symlink.')
    try:
        if sys.platform == 'win32':
            from composerlint.windows_private import read_stable
            data, stamp=read_stable(p,limit=10*1024*1024)
            identity=(str(p),stamp.volume,stamp.file_id)
            if expected_identity is not None and identity!=expected_identity:
                fail('stale_source','Source identity changed. Reopen the workspace.')
            return p,data,identity
        with host.open_regular(p) as f:
            first=os.fstat(f.fileno())
            if not stat.S_ISREG(first.st_mode) or first.st_size>10*1024*1024:fail('unsupported_edit','Use a regular MusicXML file of at most 10 MiB.')
            data=f.read(10*1024*1024+1);last=os.fstat(f.fileno())
        identity=(str(p.resolve()),first.st_dev,first.st_ino)
        if (first.st_size,first.st_mtime_ns)!=(last.st_size,last.st_mtime_ns) or len(data)>10*1024*1024 or expected_identity is not None and identity!=expected_identity:fail('stale_source','Source identity changed. Reopen the workspace.')
        return p,data,identity
    except host.UnsupportedPlatformOperation as exc:fail('unsupported_edit',str(exc))
    except (OSError,ValueError):fail('stale_source','Original is unavailable or changed. Reopen the workspace.')


_active_workers=set()
_worker_lock=threading.RLock()

def worker(request,timeout=45,*,cancelled=None):
    if sys.platform == 'win32':
        from composerlint.windows_workers import WorkerCancelled

        command=[sys.executable,'-I','-B',str(Path(__file__).with_name('editing_worker.py'))]
        try:
            result=host.run_windows_json(command,canonical(request),timeout=timeout,cancelled=cancelled)
        except host.WorkerLifecycleUnconfirmed:
            raise
        except WorkerCancelled:
            fail('unsupported_edit','Editing was cancelled during workspace shutdown.')
        except host.UnsupportedPlatformOperation as exc:fail('unsupported_edit',str(exc))
        except host.PipeLimitError as exc:fail('edit_limit',str(exc))
        except subprocess.TimeoutExpired:fail('edit_timeout','Editing exceeded its bounded deadline.')
        return worker_result(result.stdout,result.stderr,result.returncode)
    try:
        p=host.popen_owned([sys.executable,'-I','-B',str(Path(__file__).with_name('editing_worker.py'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    except host.UnsupportedPlatformOperation as exc:fail('unsupported_edit',str(exc))
    with _worker_lock:_active_workers.add(p)
    try:
        stdout,stderr=p.communicate(canonical(request),timeout=timeout)
    except BaseException as exc:
        host.terminate_owned(p)
        p.communicate()
        if isinstance(exc,subprocess.TimeoutExpired):fail('edit_timeout','Editing exceeded its bounded deadline; worker group terminated and reaped.')
        raise
    finally:
        with _worker_lock:_active_workers.discard(p)
    return worker_result(stdout,stderr,p.returncode)


def worker_result(stdout,stderr,returncode):
    if returncode:raise RuntimeError('Editing worker failure: '+stderr[-2500:])
    if len(stdout.encode())>64*1024*1024:fail('edit_limit','Editing response exceeds 64 MiB.')
    payload=json.loads(stdout)
    if not payload['ok']:fail(payload['error']['code'],payload['error']['reason'])
    return payload['result']


class EditingRuntime:
    def __init__(self):
        self.lock=threading.RLock();self.workspaces=OrderedDict();self.root=None
        self.idle_ttl=1800;self.absolute_ttl=7200;self.max_workspaces=4;self.openings=0;self.reserved={};self.max_snapshot_bytes=80*1024*1024
        self.windows_stage=None;self.windows_scope=ExitStack();self.windows_stage_unsafe=False
        self.windows_operations={}
        self.windows_closing=False
        self.windows_shutdown_lock=threading.Lock()

    @contextmanager
    def _edit_operation(self):
        if sys.platform != 'win32':
            yield worker
            return
        cancelled,finished=threading.Event(),threading.Event()
        with self.lock:
            if self.windows_closing or self.windows_stage_unsafe:
                fail('unsupported_edit','Windows workspace is closing or worker cleanup is unconfirmed.')
            self.windows_operations[cancelled]=finished
        try:
            yield partial(worker,cancelled=cancelled)
        finally:
            # Report completion after open/prepare has recorded any unconfirmed
            # termination and finished its workspace state transitions.
            with self.lock:
                self.windows_operations.pop(cancelled)
                finished.set()

    def directory(self):
        if sys.platform == 'win32':
            if self.windows_stage_unsafe or self.windows_closing:
                fail('unsupported_edit','Windows workspace is closing or worker cleanup is unconfirmed.')
            from composerlint.windows_install_storage import private_workspace
            try:
                if self.windows_stage is None:
                    self.windows_stage=self.windows_scope.enter_context(
                        private_workspace(temporary_parent(),'composerlint-m5-'))
                self.windows_stage.validate()
                return self.windows_stage.path
            except (OSError,ValueError) as exc:fail('unsupported_edit',f'Private Windows workspace unavailable: {exc}')
        try:host.require_security('Private editing workspaces')
        except host.UnsupportedPlatformOperation as exc:fail('unsupported_edit',str(exc))
        if self.root is None:
            self.sweep_dead_roots()
            self.root=tempfile.TemporaryDirectory(prefix='composerlint-m5-',dir=temporary_parent())
            Path(self.root.name).chmod(0o700)
            Path(self.root.name,'owner.json').write_text(canonical({'pid':os.getpid(),'uid':os.getuid(),'schema':1}))
        return Path(self.root.name)

    def remove(self,path,*,directory=False):
        if self.windows_stage_unsafe:
            return
        if self.windows_stage is not None:
            self.windows_stage.remove(Path(path).relative_to(self.windows_stage.path).parts,
                                      directory=directory)
        elif directory:
            path.rmdir()
        else:
            path.unlink(missing_ok=True)

    def snapshot(self,data):
        d=self.directory()/secrets.token_hex(16)
        original=d/'original.musicxml'
        if self.windows_stage is not None:
            with self.windows_stage.create((d.name,original.name)) as stream:
                stream.write(data)
        else:
            d.mkdir(mode=0o700)
            with os.fdopen(os.open(original,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as stream:stream.write(data)
            original.chmod(0o600)
        return d,original

    def sweep_dead_roots(self):
        host.require_security('Owned workspace cleanup')
        # Only exact marked dead-process roots with an exhaustive known file
        # vocabulary. Unknown files, symlinks or uncertain ownership are retained.
        import re
        for root in temporary_parent().glob('composerlint-m5-*'):
            try:
                if root.is_symlink() or root.stat().st_uid!=os.getuid() or root.stat().st_mode&0o777!=0o700:continue
                marker=root/'owner.json'
                if marker.is_symlink():continue
                owner=json.loads(marker.read_text())
                if set(owner)!={'pid','uid','schema'} or owner['uid']!=os.getuid() or owner['schema']!=1 or type(owner['pid']) is not int or owner['pid']<=0:continue
                try:os.kill(owner['pid'],0)
                except ProcessLookupError:pass
                else:continue
                files=[marker];directories=[];valid=True
                for child in root.iterdir():
                    if child==marker:continue
                    if child.is_symlink() or not child.is_dir() or not re.fullmatch('[0-9a-f]{32}',child.name):valid=False;break
                    directories.append(child)
                    for f in child.iterdir():
                        if f.is_symlink() or not f.is_file() or f.stat().st_uid!=os.getuid() or not (f.name=='original.musicxml' or re.fullmatch(r'[0-9a-f]{32}\.musicxml',f.name)):valid=False;break
                        files.append(f)
                if valid:
                    for f in files:f.unlink()
                    for d in directories:d.rmdir()
                    root.rmdir()
            except (OSError,ValueError):continue

    def snapshot_budget(self,extra):
        used=sum(p.stat().st_size for p in self.directory().glob('*/*.musicxml') if p.is_file())
        if used+sum(self.reserved.values())+extra>self.max_snapshot_bytes:fail('edit_limit','Owned snapshots would exceed the 80 MiB editing pool.')

    def expire(self,w):
        if w['state']=='expired':return True
        now=time.monotonic()
        if now-w['touched']>=self.idle_ttl or now-w['created']>=self.absolute_ttl:
            w['state']='expired';w['proposal']=None;w['expressive_brief']=None;w['brief_requests']={};w['revision']+=1;self.clear_candidate(w);return True
        return w['state']=='expired'

    def clear_candidate(self,w):
        candidate=w.get('candidate_path')
        if candidate:
            self.remove(Path(candidate));w['candidate_path']=None
        if w.get('proposal_size'):
            w['entry']['size']-=w['proposal_size'];w['proposal_size']=0

    def prune(self):
        if self.windows_stage_unsafe:return
        from composerlint.viewer_runtime import runtime
        for key,w in list(self.workspaces.items()):
            self.expire(w)
            if w['state']=='expired' or w['entry']['instance'] not in runtime.entries:
                if not w['pending']:
                    self.clear_candidate(w)
                    self.remove(w['original'])
                    self.remove(w['directory'],directory=True);del self.workspaces[key]

    def find(self,ident,touch=False):
        if self.windows_stage_unsafe:
            fail('unsupported_edit','Worker cleanup is unconfirmed. Private workspace preserved.')
        w=self.workspaces.get(ident)
        if w is None:fail('workspace_expired','Workspace unavailable; open a new workspace.')
        if self.expire(w):
            if not w['pending']:
                self.remove(w['original']);self.remove(w['directory'],directory=True);self.workspaces.pop(ident,None)
            fail('workspace_expired','Temporary workspace expired; recreate explicitly.')
        from composerlint.viewer_runtime import runtime
        if w['entry']['instance'] not in runtime.entries:fail('workspace_expired','Presentation evicted; reopen the workspace.')
        if touch:w['touched']=time.monotonic()
        return w

    def current(self,w):
        try:
            _,data,_=read_source(w['path'],w['identity'])
            if hashlib.sha256(data).hexdigest()!=w['source_sha256']:raise ViewerFailure('stale_source','Original changed; reopen the workspace.')
        except ViewerFailure:
            w['state']='stale';raise
        if w['state']=='stale':fail('stale_source','Source was changed; reopen the workspace.')

    def check_candidate(self,w):
        p=w['proposal']
        if p is None:return
        try:
            if p['operation']['kind']=='expressive_edit_batch':
                from composerlint.report_reuse import code_identity
                saved=w.get('expressive_brief')
                if (saved is None or saved.brief_sha256!=p['operation']['brief_sha256']
                        or digest(saved.brief.model_dump(mode='json'))!=saved.brief_sha256
                        or p['brief_context_revision']!=w['revision']
                        or saved.brief.code_identity!=code_identity()
                        or digest(p['verification'])!=p['verification_sha256']):raise ValueError()
            actual=read_score_bytes(Path(w['candidate_path']))
            receipt=p['verification']['render_receipt']
            if hashlib.sha256(actual).hexdigest()!=p['candidate_sha256'] or receipt['candidate_sha256']!=p['candidate_sha256'] or digest(p['manifest'])!=receipt['manifest_sha256'] or [hashlib.sha256(s).hexdigest() for s in p['pages']]!=receipt['svg_sha256']:raise ValueError()
        except (OSError,ValueError,TypeError):
            w['state']='failed';w['error']={'code':'stale_proposal','reason':'Owned candidate or notation assets changed; discard and recreate explicitly.'}
            fail('stale_proposal',w['error']['reason'])

    def state(self,w):
        from composerlint.viewer_runtime import runtime
        p=w['proposal']
        return {'workspace_id':w['id'],'workspace_revision':w['revision'],'state':w['state'],'source_sha256':w['source_sha256'],'base_snapshot':w['snapshot'],'options_sha256':w['options_sha256'],'view_instance_id':w['entry']['instance'],'selection':runtime.selection_value(w['entry']),'target_choices':w['choices'],'proposal':({k:v for k,v in p.items() if k not in ('pages','manifest','verification','changes','target')}|{'target':{k:v for k,v in p['target'].items() if k!='notes'}}) if p else None,'last_error':w['error'],'csrf_nonce':w['csrf'],'expires_in_seconds':max(0,int(min(self.idle_ttl-(time.monotonic()-w['touched']),self.absolute_ttl-(time.monotonic()-w['created'])))),'supported_operations':['octave_transpose_up','octave_transpose_down','exact_pitch_batch','expressive_edit_batch'],'export_binding':self.export_binding(w),'export_receipt':w.get('export_receipt'),'notice':'Temporary preview until human approval and verified export. Original unchanged.'}

    def export_binding(self,w):
        if sys.platform == 'win32':return None
        if not w.get('proposal') or w['proposal']['operation']['kind']!='expressive_edit_batch':return None
        from composerlint.editing_export import binding
        return binding(w)

    def result(self,w,data=None):
        kind=w['proposal']['operation']['kind'] if w.get('proposal') else None
        policy={'exact_pitch_batch':'v02_m5_exact_pitch_v1','expressive_edit_batch':'v02_m6_expressive_edit_v1'}.get(kind,'v02_m5_octave_v1')
        if kind=='expressive_edit_batch':policy=w['proposal']['operation']['policy']
        return EditResult(edit_policy_id=policy,workspace_id=w['id'],workspace_revision=w['revision'],state=w['state'],data=data or self.state(w))

    def open(self,args):
        with self._edit_operation() as run_worker:
            return self._open(args,run_worker)

    def _open(self,args,run_worker):
        from composerlint.viewer_runtime import runtime
        started=time.monotonic()
        p,data,identity=read_source(args.path)
        # Preflight before creating snapshots or invoking the protected normalizer.
        from composerlint.editing_source import safe_xml
        safe_xml(data)
        view_instance_id=args.view_instance_id
        if view_instance_id is None:
            with runtime.lock:
                runtime.prune()
                entry=next((e for e in reversed(runtime.entries.values())
                    if e.get('source_path')==str(p.resolve()) and e.get('review_context',{}).get('base_snapshot')==args.expected_snapshot
                    and e['manifest']['mode']=='whole_score'
                    and set(e['manifest']['scope']['part_ids'])=={c['part_id'] for c in e['review_context']['target_choices']}),None)
                if entry is not None:view_instance_id=entry['instance']
        with self.lock:
            if self.windows_closing or self.windows_stage_unsafe:
                fail('unsupported_edit','Windows workspace is closing or worker cleanup is unconfirmed.')
            self.prune()
            for existing in self.workspaces.values():
                if existing['identity']==identity and existing['snapshot']==args.expected_snapshot and existing['options_request']==args.options and (view_instance_id is None or existing['entry']['instance']==view_instance_id):
                    self.current(existing);existing['touched']=time.monotonic();return self.result(existing,{**self.state(existing),'workspace_url':runtime.references(existing['entry'])['viewer_url']})
            if len(self.workspaces)+self.openings>=self.max_workspaces:fail('edit_limit','At most four editing workspaces. Close/expire an existing workspace first.')
            self.snapshot_budget(len(data))
            d,original=self.snapshot(data)
            self.openings+=1
        try:
            result=run_worker({'action':'open','original':str(original),'options':args.options,'expected_snapshot':args.expected_snapshot},max(1,45-(time.monotonic()-started)))
            _,current,_=read_source(p,identity)
            if current!=data:fail('stale_source','Original changed while opening workspace.')
            with self.lock,runtime.lock:
                if self.windows_closing or self.windows_stage_unsafe:
                    fail('unsupported_edit','Windows workspace is closing or worker cleanup is unconfirmed.')
                if view_instance_id:
                    entry=runtime.find(view_instance_id)
                    if entry is None or entry.get('source_path')!=str(p.resolve()) or entry['manifest']['scope']!=result['manifest']['scope'] or entry['manifest']['mode']!='whole_score':fail('stale_snapshot','Existing view does not exactly match complete workspace source/options/layout.')
                else:
                    result['manifest']['view_key']=digest({'scope':result['manifest']['scope'],'mode':'whole_score','adapter':2,'renderer':'verovio-6.3.0','width':1800})
                    entry=runtime.add(result['manifest']['view_key'],result['pages'],result['manifest'],result.get('render_context'))
                now=time.monotonic();ident=secrets.token_hex(16)
                w=dict(id=ident,path=p,identity=identity,source_sha256=hashlib.sha256(data).hexdigest(),snapshot=args.expected_snapshot,options_request=args.options,options_sha256=result['manifest']['scope']['options_sha256'],directory=d,original=original,entry=entry,choices=result['choices'],revision=0,proposal=None,pending=None,state='no_proposal',error=None,created=now,touched=now,csrf=secrets.token_urlsafe(32),requests={},discarded=set(),candidate_path=None,proposal_size=0)
                self.workspaces[ident]=w;entry['workspace_id']=ident
                entry['source_path']=str(p.resolve())
                entry['review_context']={'base_snapshot':w['snapshot'],'source_sha256':w['source_sha256'],
                    'options_sha256':w['options_sha256'],'target_choices':w['choices']}
                return self.result(w,{**self.state(w),'workspace_url':runtime.references(entry)['viewer_url'],'timing':result['timing']})
        except host.WorkerLifecycleUnconfirmed:
            with self.lock:
                self.windows_stage_unsafe=True
            fail('unsupported_edit','Worker cleanup is unconfirmed. Private workspace preserved.')
        except BaseException:
            with self.lock:
                self.remove(original);self.remove(d,directory=True)
            raise
        finally:
            with self.lock:self.openings-=1

    def prepare(self,args):
        with self._edit_operation() as run_worker:
            return self._prepare(args,run_worker)

    def _prepare(self,args,run_worker):
        from composerlint.viewer_runtime import runtime
        started=time.monotonic();fingerprint=digest(args.model_dump(mode='json'))
        with self.lock:
            w=self.find(args.workspace_id);self.current(w)
            if args.client_request_id in w['requests']:
                saved=w['requests'][args.client_request_id]
                if saved['fingerprint']!=fingerprint:fail('revision_conflict','Request ID was already used for different arguments.')
                if w['proposal'] and saved['proposal_id']==w['proposal']['proposal_id']:
                    self.check_candidate(w)
                    return self.result(w,self.proposal_summary(w))
                fail('proposal_expired','Retried proposal was discarded or replaced; use a new explicit request.')
            if len(w['requests'])>=64:fail('edit_limit','Workspace reached 64 requests; reopen explicitly.')
            if w['pending'] or args.expected_workspace_revision!=w['revision']:fail('revision_conflict','Workspace changed or an edit is already pending. Retrieve current context.')
            if (args.expected_source_sha256,args.expected_snapshot,args.expected_options_sha256)!=(w['source_sha256'],w['snapshot'],w['options_sha256']):fail('stale_snapshot','Source/base/options expectations differ from workspace.')
            with runtime.lock:
                entry=w['entry'];selection=args.selection
                if args.live_selection_revision is not None:
                    if args.live_selection_revision!=entry['revision'] or entry['selected'] is None:fail('stale_selection','Retrieve the current nonempty live selection.')
                    selection=entry['selected']
                scope=selection.model_dump(mode='json')
                if any(scope[k]!=entry['manifest']['scope'][k] for k in ('source_sha256','snapshot_id','options_sha256')) or args.target.part_id not in selection.part_ids:fail('stale_selection','Selection binding or requested part differs from the original workspace.')
                if not set(selection.part_ids)<=set(entry['manifest']['scope']['part_ids']) or selection.reference_part_id not in entry['manifest']['scope']['part_ids']:fail('invalid_selection','Canonical selection contains an unknown workspace part.')
                selection_revision=entry['revision']
            brief=None
            if args.operation.kind=='expressive_edit_batch':
                saved=w.get('expressive_brief')
                if saved is None or saved.brief_sha256!=args.operation.brief_sha256:
                    fail('stale_brief','Generation requires the current server-owned M6.2 brief digest.')
                self._current_brief(w,saved,args,selection)
                from composerlint.editing_expression import direction
                brief=saved.brief.model_dump(mode='json')
                direction(brief,args.operation.direction_id)
                expected_policy=brief['draft'].get('creation_policy') or brief['draft'].get('rhythm_policy') or 'v02_m6_expressive_edit_v1'
                if args.operation.policy!=expected_policy:fail('stale_brief','Candidate policy differs from its server-owned brief.')
                if selection.end_measure_index-selection.start_measure_index>3 or args.target.lane_mode=='all':
                    fail('unsupported_edit','Expressive candidates require 1–4 measures and one reliable lane.')
            self.snapshot_budget(w['original'].stat().st_size)
            request_id=secrets.token_hex(16);self.reserved[request_id]=w['original'].stat().st_size;w['pending']=request_id;w['state']='preparing';w['error']=None;w['touched']=time.monotonic()
            candidate=w['directory']/(request_id+'.musicxml')
        try:
            r=run_worker({'action':'prepare','original':str(w['original']),'candidate':str(candidate),'options':w['options_request'],'expected_snapshot':w['snapshot'],'selection':scope,'target':args.target.model_dump(mode='json'),'operation':args.operation.model_dump(mode='json'),**({'brief':brief} if brief is not None else {})},max(1,45-(time.monotonic()-started)))
            with self.lock,runtime.lock:
                if self.windows_closing:
                    fail('unsupported_edit','Windows workspace is closing.')
                self.find(w['id']);self.current(w)
                if w['pending']!=request_id or w['revision']!=args.expected_workspace_revision or entry['revision']!=selection_revision:fail('revision_conflict','Selection/workspace changed during preparation; candidate not published.')
                if brief is not None:
                    self._current_brief(w,w['expressive_brief'],args,selection)
                    if w['expressive_brief'].brief_sha256!=args.operation.brief_sha256:
                        fail('stale_brief','Brief changed during candidate preparation.')
                if hashlib.sha256(read_score_bytes(w['original'])).hexdigest()!=w['source_sha256']:
                    fail('stale_source','Owned Original snapshot changed during preparation.')
                size=sum(len(s.encode()) for s in r['pages'])+len(canonical(r['manifest']).encode())+len(canonical(r['verification']).encode())
                used=sum(e['size'] for e in runtime.entries.values())-w['proposal_size']
                if used+size>runtime.max_bytes:fail('edit_limit','Original and proposed presentation exceed the shared 64 MiB pool.')
                if time.monotonic()-started>=45:fail('edit_timeout','Whole preparation exceeded 45 seconds; candidate not published.')
                verification=r['verification'];vhash=digest(verification);chash=hashlib.sha256(read_score_bytes(candidate)).hexdigest();pid=secrets.token_hex(16)
                if chash!=r['manifest']['scope']['source_sha256'] or chash!=verification['render_receipt']['candidate_sha256']:fail('verification_failed','Actual candidate differs from verified rendering receipt.')
                self.clear_candidate(w);w['candidate_path']=str(candidate);w['proposal_size']=size;entry['size']+=size
                w['proposal']=dict(proposal_id=pid,source_sha256=w['source_sha256'],candidate_sha256=chash,verification_sha256=vhash,verification_status='verified',preview_status='ready',target=r['target'],operation=args.operation.model_dump(mode='json'),manifest=r['manifest'],pages=[s.encode() for s in r['pages']],verification=verification,changes=verification['changes'],timing={**r['timing'],'end_to_end_seconds':time.monotonic()-started},created_at=time.time())
                if brief is not None:w['proposal']['brief_context_revision']=w['revision']+1
                w['requests'][args.client_request_id]={'fingerprint':fingerprint,'proposal_id':pid};w['state']='verified';w['revision']+=1;w['touched']=time.monotonic();w['pending']=None
                return self.result(w,self.proposal_summary(w))
        except BaseException as exc:
            with self.lock:
                if isinstance(exc,host.WorkerLifecycleUnconfirmed):
                    self.windows_stage_unsafe=True
                    exc=ViewerFailure('unsupported_edit','Worker cleanup is unconfirmed. Private workspace preserved.')
                else:
                    self.remove(candidate)
                if w['pending']==request_id:
                    w['pending']=None;w['error']={'code':getattr(exc,'code','internal_error'),'reason':getattr(exc,'reason','Preparation failed; original unchanged.')};w['state']='stale' if getattr(exc,'code',None)=='stale_source' else 'verified' if w['proposal'] else 'unsupported' if isinstance(exc,ViewerFailure) else 'failed'
            raise exc
        finally:
            with self.lock:self.reserved.pop(request_id,None)

    def proposal_summary(self,w):
        p=w['proposal']
        expressive=p['operation']['kind']=='expressive_edit_batch'
        return {k:p[k] for k in ('proposal_id','source_sha256','candidate_sha256','verification_sha256','verification_status','preview_status','operation','timing')}|{'target':{k:v for k,v in p['target'].items() if k!='notes'}}|{'message':('Creative alternative: exact changes and preservation verified within supported checks; expressive success remains interpretation.' if expressive else 'Requested pitch change verified within supported checks; original unchanged. Inspect Original/Proposed.'),'workspace_url':self.url(w),'sections':['overview','changes','verification']+(['musical_comparison'] if expressive else [])}

    def expressive_brief(self, args):
        """One temporary discussion brief; never calls the editing/render worker."""
        from composerlint.expressive_models import ExpressiveEditBrief, BriefResult
        from composerlint.passage_session import worker as passage_worker
        from composerlint.report_reuse import code_identity
        from composerlint.viewer_runtime import runtime
        fingerprint = digest(args.model_dump(mode='json'))
        started = time.monotonic()
        with self.lock, runtime.lock:
            w = self.find(args.workspace_id)
            self.current(w)
            if (args.expected_source_sha256, args.expected_snapshot, args.expected_options_sha256) != (
                    w['source_sha256'], w['snapshot'], w['options_sha256']):
                fail('stale_snapshot', 'Brief source/base/options expectations differ from the workspace.')
            entry = w['entry']
            selection = args.selection
            if args.live_selection_revision is not None:
                if entry['revision'] != args.live_selection_revision or entry['selected'] is None:
                    fail('stale_selection', 'Retrieve the current nonempty live selection before discussing an edit.')
                selection = entry['selected']
            scope = selection.model_dump(mode='json')
            if any(scope[k] != entry['manifest']['scope'][k] for k in ('source_sha256','snapshot_id','options_sha256')):
                fail('stale_selection', 'Brief selection does not match the original workspace.')
            if not set(selection.part_ids) <= set(entry['manifest']['scope']['part_ids']) or args.target.part_id not in selection.part_ids:
                fail('invalid_selection', 'Brief target is not a selected workspace part.')
            if selection.end_measure_index - selection.start_measure_index > 3:
                fail('unsupported_brief_scope', 'M6.2 supports 1–4 contiguous measures; explicitly select a smaller window.')
            saved = w.get('expressive_brief')
            requests = w.setdefault('brief_requests', {})
            retry = requests.get(args.client_request_id) if args.client_request_id else None
            if retry:
                if retry['fingerprint'] != fingerprint:
                    fail('revision_conflict', 'Brief request ID was used with different arguments.')
                if saved is None or saved.brief_sha256 != retry['digest'] or w['revision'] != retry['revision']:
                    fail('stale_brief', 'Retried brief is no longer current. Discuss the current context explicitly.')
                self._current_brief(w, saved, args, selection)
                return saved.model_copy(deep=True)
            if w['pending'] or w['revision'] != args.expected_workspace_revision:
                fail('revision_conflict', 'Workspace changed or an edit is pending. Retrieve current context.')
            if args.action != 'set':
                if saved is None:
                    fail('stale_brief', 'No current expressive brief exists in this workspace.')
                if args.expected_brief_sha256 != saved.brief_sha256:
                    fail('stale_brief', 'Discussing a brief requires its current digest.')
                self._current_brief(w, saved, args, selection)
                if args.action == 'get':
                    return saved.model_copy(update={'workspace_revision':w['revision']},deep=True)
            if args.expected_brief_sha256 != (saved.brief_sha256 if saved else None):
                fail('stale_brief', 'Revising or discussing a brief requires its current digest.')
            if len(requests) >= 64:
                fail('edit_limit', 'Workspace reached 64 brief changes; reopen explicitly.')
            revision, live_revision = w['revision'], entry['revision']
            code = code_identity()
            if args.action == 'set':
                # Copy the resolved live selection into the private call. Preserve its
                # original live binding in the final brief, never treat it as a new selection.
                resolved = args.model_dump(mode='json')
                resolved.update(selection=scope, live_selection_revision=None)
                request = dict(operation='expressive_brief_context', arguments=resolved,
                    path=str(w['path']), options=w['options_request'])
            else:
                brief = saved.brief.model_copy(deep=True)
                if args.action == 'choose':
                    if args.direction_id not in {d.direction_id for d in brief.draft.directions}:
                        fail('invalid_brief', 'Choose one of this brief’s actual musical directions.')
                    brief.discussion_state = 'direction_chosen'
                    brief.chosen_direction_id = args.direction_id
                else:
                    brief.discussion_state = 'rejected'
                    brief.chosen_direction_id = None
        # Musical work stays under the existing isolated passage deadline.
        if args.action == 'set':
            checked = passage_worker.request(request, timeout=max(.001, 25-(time.monotonic()-started)))
        with self.lock, runtime.lock:
            self.find(w['id']); self.current(w)
            if w['pending'] or w['revision'] != revision or entry['revision'] != live_revision or code_identity() != code:
                fail('revision_conflict', 'Selection, workspace or implementation changed; brief was not stored.')
            if args.action == 'set':
                if checked['code_identity'] != code:
                    fail('stale_brief', 'Evidence implementation changed while preparing the brief.')
                brief = ExpressiveEditBrief(brief_schema_version=3 if args.draft.creation_policy else 2 if args.draft.rhythm_policy else 1,
                    policy='v02_m6_note_creation_brief_v1' if args.draft.creation_policy else 'v02_m6_rhythmic_brief_v1' if args.draft.rhythm_policy else 'v02_m6_expressive_brief_v1', workspace_id=w['id'], workspace_revision=revision+1,
                    source_sha256=w['source_sha256'], base_snapshot=w['snapshot'], options_sha256=w['options_sha256'],
                    code_identity=checked['code_identity'], selection=selection, live_selection_revision=args.live_selection_revision,
                    view_instance_id=entry['instance'], target=checked['target'], draft=args.draft,
                    default_preservation=checked['default_preservation'], evidence=checked['evidence'],
                    constraint_baselines=checked['constraint_baselines'], future_verification_required=checked['future_verification_required'])
            else:
                brief.workspace_revision = revision+1
            result = BriefResult(workspace_id=w['id'], workspace_revision=revision+1,
                brief_sha256=digest(brief.model_dump(mode='json')), brief=brief)
            if len(result.canonical_json().encode()) > 32768:
                fail('edit_limit', 'Complete brief exceeds 32 KiB. Use fewer directions, anchors or words; nothing was stored.')
            if time.monotonic()-started >= 25:
                fail('edit_timeout', 'Brief exceeded the existing 25-second passage deadline; nothing was stored.')
            w['expressive_brief'] = result.model_copy(deep=True)
            if w.get('proposal') and w['proposal'].get('operation',{}).get('kind')=='expressive_edit_batch':
                w['proposal']=None;self.clear_candidate(w);w['state']='no_proposal'
            w['revision'] += 1
            w['touched'] = time.monotonic()
            requests[args.client_request_id] = dict(fingerprint=fingerprint, digest=result.brief_sha256, revision=w['revision'])
            return result

    def _current_brief(self, w, saved, args, selection):
        from composerlint.report_reuse import code_identity
        b = saved.brief
        p=w.get('proposal')
        candidate_bound=(p is not None and p.get('operation',{}).get('kind')=='expressive_edit_batch'
            and p['operation']['brief_sha256']==saved.brief_sha256 and p.get('brief_context_revision')==w['revision'])
        if (saved.brief_sha256 != digest(b.model_dump(mode='json')) or not (b.workspace_revision == w['revision'] or candidate_bound)
                or b.code_identity != code_identity() or b.selection != selection
                or b.view_instance_id != w['entry']['instance']
                or b.target['part_id'] != args.target.part_id
                or b.target['lane_mode'] != args.target.lane_mode
                or b.target['voices'][0]['voice_id'] != (args.target.voice_id or b.target['voices'][0]['voice_id'])
                or b.live_selection_revision is not None and b.live_selection_revision != w['entry']['revision']):
            fail('stale_brief', 'Brief source, target, selection, code or workspace revision is no longer current; revise explicitly.')

    def url(self,w):
        from composerlint.viewer_runtime import runtime
        return runtime.references(w['entry'])['viewer_url']

    def get(self,args):
        with self.lock:
            w=self.find(args.workspace_id,True);self.current(w);self.check_candidate(w);p=w['proposal']
            if p is None or p['proposal_id']!=args.proposal_id:fail('proposal_expired','Proposal unavailable, discarded or replaced.')
            if (args.expected_source_sha256,args.expected_candidate_sha256,args.expected_verification_sha256)!=(p['source_sha256'],p['candidate_sha256'],p['verification_sha256']):fail('stale_proposal','Proposal expectation mismatch.')
            rows=[self.proposal_summary(w)] if args.section=='overview' else p['changes'] if args.section=='changes' else [{'gate':k,'passed':v} for k,v in p['verification']['gates'].items()]+[{'validation_level':p['verification']['validation_level'],'notation':p['verification']['notation']},{'analysis_snapshots':{k:p['verification']['analysis'][k] for k in ('original_snapshot','proposal_snapshot','notice')}}]+[{'coverage_change':r} for r in p['verification']['analysis']['coverage']]+[{'new_finding':r} for r in p['verification']['analysis']['new_findings']]+[{'resolved_finding':r} for r in p['verification']['analysis']['resolved_findings']]
            if args.section=='musical_comparison':
                comparison=p['verification'].get('musical_comparison')
                if comparison is None:fail('unsupported_edit','Musical comparison is available only for M6 expressive candidates.')
                rows=[{k:v for k,v in comparison.items() if k!='rows'}]+comparison['rows']
            if args.section=='verification' and p['operation']['kind']=='expressive_edit_batch':
                rows.extend([{'brief_binding':p['verification']['brief_binding']},
                    *[{'hard_constraint':r} for r in p['verification']['constraints']],
                    *[{'rhythm_correspondence':r} for r in p['verification'].get('correspondence',[])]])
            if p['operation']['kind']=='expressive_edit_batch':
                # Complete large evidence/coverage records remain retrievable.
                # Decode all base64 chunks in order, check SHA256, then parse JSON.
                import base64
                bounded=[]
                for i,row in enumerate(rows):
                    raw=canonical(row).encode()
                    if len(raw)<=16000:bounded.append(row);continue
                    chunks=[raw[n:n+10000] for n in range(0,len(raw),10000)]
                    bounded.extend({'encoding':'base64_json_chunks','record_index':i,'record_sha256':hashlib.sha256(raw).hexdigest(),
                        'chunk_index':n,'chunk_count':len(chunks),'encoded_chunk':base64.b64encode(chunk).decode()}
                        for n,chunk in enumerate(chunks))
                rows=bounded
            binding=digest({'proposal_id':p['proposal_id'],'verification':p['verification_sha256'],'section':args.section});offset=0
            if args.cursor:
                try:
                    key,value=args.cursor.split(':');offset=int(value)
                    if key!=binding or str(offset)!=value or not 0<offset<len(rows):raise ValueError()
                except ValueError:fail('invalid_cursor','Cursor does not belong to this proposal section.')
            chosen=[]
            for row in rows[offset:offset+args.page_size]:
                if len(canonical(chosen+[row]).encode())>20000:break
                chosen.append(row)
            if not chosen and offset<len(rows):fail('oversized_record','Record exceeds bounded retrieval; narrower analysis retrieval is required.')
            end=offset+len(chosen)
            return self.result(w,{'proposal_id':p['proposal_id'],'section':args.section,'items':chosen,'total_items':len(rows),'start_index':offset,'next_cursor':None if end==len(rows) else binding+':'+str(end),'section_exhausted':end==len(rows)})

    def discard(self,args):
        with self.lock:
            w=self.find(args.workspace_id,True)
            if args.proposal_id in w['discarded']:return self.result(w,{'discarded_proposal_id':args.proposal_id})
            if args.expected_workspace_revision!=w['revision'] or w['pending']:fail('revision_conflict','Workspace changed or preparation is pending.')
            if w['proposal'] is None or w['proposal']['proposal_id']!=args.proposal_id:fail('proposal_expired','This proposal is no longer current.')
            w['discarded'].add(args.proposal_id);w['proposal']=None;self.clear_candidate(w);w['state']='discarded';w['revision']+=1
            return self.result(w,{'discarded_proposal_id':args.proposal_id})

    def _close_windows(self):
        from composerlint.windows_workers import SetupProcessCleanupUnconfirmed

        with self.windows_shutdown_lock:
            with self.lock:
                self.windows_closing=True
                operations=tuple(self.windows_operations.items())
                for cancelled,_ in operations:cancelled.set()
            deadline=time.monotonic()+7
            for _,finished in operations:
                if not finished.wait(max(0,deadline-time.monotonic())):
                    with self.lock:
                        self.windows_stage_unsafe=True
                    raise OSError('Editing worker cleanup is unconfirmed; private workspace preserved.')
            with self.lock:
                if self.windows_stage_unsafe:
                    error=SetupProcessCleanupUnconfirmed('Private workspace preserved.')
                    self.windows_scope.__exit__(type(error),error,None)
                else:
                    self.windows_scope.close()
                self.windows_stage=None
                self.workspaces.clear()
                self.windows_closing=False

    def close(self):
        if sys.platform == 'win32':
            self._close_windows()
            return
        with _worker_lock:
            for p in list(_active_workers):
                host.terminate_owned(p)
                p.wait(timeout=2)
        with self.lock:
            for w in self.workspaces.values():
                self.clear_candidate(w);self.remove(w['original'])
                for p in w['directory'].iterdir():
                    if p.is_file() and not p.is_symlink():p.unlink()
                self.remove(w['directory'],directory=True)
            self.workspaces.clear()
            if self.root:self.root.cleanup();self.root=None

editing=EditingRuntime()
atexit.register(editing.close)
