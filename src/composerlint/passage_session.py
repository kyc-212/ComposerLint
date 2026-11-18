"""Private, bounded passage worker lifetime and server-owned verified state.

Only the MCP parent owns the pipe. Public requests contain queries, never cache
entries or validation flags. Legacy workers and the pure passage API are separate.
"""
import atexit
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from composerlint.advanced_models import AdvancedFailure, PassageArguments
from composerlint.engine.advanced import LIMITS
from composerlint.review import canonical, digest
from composerlint import platform_support as host

cancel_event = ContextVar('passage_cancel_event', default=None)
PREPARATION_SECONDS = 120
TTL_SECONDS = 300
MAX_CACHE_BYTES = LIMITS['payload_bytes']


def fresh_owned(source):
    """Freshness checks stay bounded even if a cached source grows or vanishes."""
    from composerlint.score_import import verify_import
    verify_import(source.path)
    try:
        if sys.platform=='win32':
            data=host.read_score_bytes(source.path)
        else:
            with source.path.open('rb') as stream:data=stream.read(LIMITS['source_bytes']+1)
    except OSError as exc:raise AdvancedFailure('stale_snapshot','Owned source is no longer readable.') from exc
    if len(data)>LIMITS['source_bytes']:
        raise AdvancedFailure('analysis_limit_exceeded','Owned source exceeds the existing 10 MiB input limit.')
    if hashlib.sha256(data).hexdigest()!=source.score.source_sha256:
        raise AdvancedFailure('stale_snapshot','Original source changed; prepare it again.')


@dataclass
class Owned:
    source: object
    expires: float
    seal: str
    passages: OrderedDict = field(default_factory=OrderedDict)


@dataclass
class Verified:
    bundle: object
    graph: object
    seal: str
    proofs: OrderedDict = field(default_factory=OrderedDict)


class Store:
    def __init__(self, *, clock=time.monotonic, ttl=TTL_SECONDS, max_bytes=MAX_CACHE_BYTES):
        from composerlint.report_reuse import code_identity
        self.clock, self.ttl, self.max_bytes = clock, ttl, max_bytes
        self._boot_code=code_identity()
        self.sources = OrderedDict()
        self.imported = None
        self.expired = OrderedDict()
        self._key = secrets.token_bytes(32)

    def validate_import(self, path, fingerprint):
        from .engine.parser import load_score
        from .report_reuse import code_identity

        local = Path(path).resolve()
        score = load_score(local)
        if score.source_sha256 != fingerprint:
            raise AdvancedFailure('stale_snapshot', 'Score changed during import.')
        key = (str(local), digest(score.options.model_dump(mode='json')), code_identity())
        existing = self.sources.get(key)
        if existing and existing.source.score.source_sha256 != fingerprint:
            del self.sources[key]
        size = len(score.model_dump_json().encode())
        self.imported = None
        if size + self._size() <= self.max_bytes:
            self.imported = (key, self.clock() + self.ttl, size, score)

    def take_import(self, key):
        if self.imported is None:
            return None
        cached_key, expires, _, score = self.imported
        if self.clock() >= expires:
            self.imported = None
            return None
        if cached_key != key:
            return None
        self.imported = None
        from .score_import import read_regular, verify_import
        local = Path(key[0])
        verify_import(local)
        if hashlib.sha256(read_regular(local)).hexdigest() == score.source_sha256:
            return score
        return None

    def sign(self, value):
        return hmac.new(self._key, canonical(value).encode(), hashlib.sha256).hexdigest()

    def source_state(self, source):
        return dict(path=str(source.path.resolve()), raw=hashlib.sha256(source.raw).hexdigest(),
            score=source.score.model_dump(mode='json'), sealed=hashlib.sha256(source.compressed_score).hexdigest(),
            context=source.source_context.model_dump(mode='json'),
            audit={k:v.model_dump(mode='json') for k,v in source.audit.items()},
            code=source.code_id, snapshot=source.source_snapshot_id)

    def passage_state(self, entry):
        return dict(snapshot=entry.bundle.snapshot.model_dump(mode='json'), raw=entry.graph.raw,
            items={k:v.model_dump(mode='json') for k,v in entry.graph.items.items()},
            links=entry.graph.links, indexes=entry.graph.index_refs,
            extra_edges={k:sorted(v) for k,v in entry.graph.extra_edges.items()},
            proofs=entry.proofs)

    def _size(self):
        return (self.imported[2] if self.imported else 0) + sum(len(canonical(self.source_state(e.source)).encode()) + len(e.source.raw) + len(e.source.compressed_score)
            + sum(len(canonical(self.passage_state(p)).encode()) for p in e.passages.values())
            for e in self.sources.values())

    def bound(self, key):
        if self.imported and self._size() > self.max_bytes:
            self.imported = None
        while len(self.sources)>2 or self._size()>self.max_bytes:
            if len(self.sources)==1:
                self.sources.clear()
                raise AdvancedFailure('analysis_limit_exceeded','Verified passage state exceeds the existing 32 MiB budget.')
            self.sources.popitem(last=False)
        if key not in self.sources:
            raise AdvancedFailure('stale_snapshot','Owned source was evicted; prepare the source again.')

    def checked_source(self, args, timings):
        from composerlint.engine.parser import _options
        from composerlint.report_reuse import code_identity
        from composerlint import passage
        now=self.clock()
        for k,e in list(self.sources.items()):
            if now>=e.expires:
                self.expired[k]=e.source.source_snapshot_id
                del self.sources[k]
        while len(self.expired)>16:self.expired.popitem(last=False)
        local=Path(args.path).expanduser().resolve()
        code=code_identity();options=digest(_options(args.options).model_dump(mode='json'))
        if code!=self._boot_code:
            self.sources.clear()
            raise AdvancedFailure('stale_snapshot','Passage worker implementation changed; restart the owned worker.')
        key=(str(local),options,code)
        entry=self.sources.get(key)
        if entry is not None:
            if not hmac.compare_digest(entry.seal,self.sign(self.source_state(entry.source))):
                self.sources.clear()
                raise AdvancedFailure('unavailable_evidence','Owned source integrity check failed.')
            try:fresh_owned(entry.source)
            except Exception:
                del self.sources[key]
                raise
            timings.update(source_reuse_hits=1.0,preparation_reused_between_requests=1.0)
        else:
            if args.expected_source_snapshot is not None and (key in self.expired or any(
                e.source.source_snapshot_id==args.expected_source_snapshot for e in self.sources.values())):
                raise AdvancedFailure('stale_snapshot','No live owned source for this path/options/policy; prepare it again.')
            imported = self.take_import(key)
            source=passage.prepare(local,args.options,timings, validated_score=imported)
            # load_score constructs the fully validated NormalizedScore. Reconcile
            # its detached seal and complete audit before publishing private state.
            passage.require(canonical(source.score.model_dump(mode='json')).encode()==source.sealed_score,
                            'Prepared source seal mismatch.')
            passage.require(passage.source_identity(source.sealed_score,source.source_context,source.audit,source.code_id)==source.source_snapshot_id,
                            'Prepared source ownership mismatch.')
            fresh_owned(source)
            if args.expected_source_snapshot is not None and args.expected_source_snapshot!=source.source_snapshot_id:
                raise AdvancedFailure('stale_snapshot','Fresh source differs from the requested snapshot.')
            timings['source_validation_passes']=1.0
            entry=Owned(source,self.clock()+self.ttl,self.sign(self.source_state(source)))
            self.expired.pop(key,None)
            self.sources[key]=entry;self.bound(key)
        if args.expected_source_snapshot is not None and args.expected_source_snapshot!=entry.source.source_snapshot_id:
            raise AdvancedFailure('stale_snapshot','Source snapshot does not match owned source.')
        self.sources.move_to_end(key)
        return key,entry

    def begin(self, args):
        timings={k:0.0 for k in ('loading_normalization_seconds','metadata_audit_seconds','preparation_seconds',
            'source_loads','preparation_runs','preparation_reused_between_requests','normalized_source_revalidations',
            'transpose_audit_passes','scope_seconds','scoped_computation_seconds','validation_seconds',
            'scoped_predicate_passes','source_validation_passes','source_reuse_hits','passage_reuse_hits','graph_construction_seconds')}
        key, owned = self.checked_source(args, timings)
        return key, owned, timings

    def run(self, args, *, response_overhead=0, prepared=None):
        from composerlint import passage
        from composerlint.passage_delivery import project_shared
        started=time.monotonic()
        key, owned, timings = prepared if prepared is not None else self.begin(args)
        source=owned.source;entry=None
        if args.section!='source':
            query=args.query();qkey=digest(query.model_dump(mode='json'))
            entry=owned.passages.get(qkey)
            if entry is not None:
                if not hmac.compare_digest(entry.seal,self.sign(self.passage_state(entry))):
                    owned.passages.clear()
                    raise AdvancedFailure('unavailable_evidence','Owned passage/evidence integrity check failed.')
                timings['passage_reuse_hits']=1.0
            else:
                if args.expected_passage_snapshot is not None:
                    raise AdvancedFailure('stale_advanced_snapshot','No live verified result for this exact query; request its overview again.')
                bundle=passage.compute(source,query,timings)
                passage._validate_owned_passage(bundle,timings,score=source.score,audit=source.audit)
                t=time.monotonic();graph=passage.PassageGraph(source,bundle.snapshot)
                timings['graph_construction_seconds']=time.monotonic()-t
                entry=Verified(bundle,graph,'')
                entry.seal=self.sign(self.passage_state(entry));owned.passages[qkey]=entry
                while len(owned.passages)>4:owned.passages.popitem(last=False)
            if args.expected_passage_snapshot is not None and args.expected_passage_snapshot!=entry.bundle.snapshot.passage_snapshot_id:
                raise AdvancedFailure('stale_advanced_snapshot','Passage snapshot differs from the verified result.')
            owned.passages.move_to_end(qkey)
        try:
            if args.delivery_policy=='shared_evidence_v2':
                result=project_shared(source,entry,args,timings,self._key,response_overhead=response_overhead)
            else:
                result=passage.project(source,entry.bundle if entry else None,args,timings,
                    _response_overhead=response_overhead+256,_graph=entry.graph if entry else None)
        except AdvancedFailure as exc:
            if entry is not None:
                if exc.code in ('invalid_cursor','unknown_record','oversized_record'):
                    # These refusals may materialize valid server-derived graph
                    # nodes, but never accept caller-supplied records or facts.
                    entry.seal=self.sign(self.passage_state(entry))
                else:owned.passages.pop(qkey,None)
            raise
        except BaseException:
            if entry is not None:owned.passages.pop(qkey,None)
            raise
        if entry is not None:entry.seal=self.sign(self.passage_state(entry))
        fresh_owned(source)
        if self.clock()>=owned.expires:
            self.sources.pop(key,None)
            raise AdvancedFailure('stale_snapshot','Owned source expired during the request.')
        from composerlint.report_reuse import code_identity
        if code_identity()!=source.code_id:
            self.sources.pop(key,None)
            raise AdvancedFailure('stale_snapshot','Implementation policy changed during request.')
        self.bound(key)
        result.timings.update(timings,owned_request_seconds=time.monotonic()-started,
                              owned_state_bytes=float(self._size()))
        # Re-account final timings and all private viewer-envelope bytes.
        if len(canonical(result.model_dump(mode='json')).encode())+response_overhead>args.max_bytes:
            raise AdvancedFailure('oversized_record','Complete passage response exceeds its byte budget.')
        return result


class Worker:
    def __init__(self):
        self.process=None;self.lock=threading.RLock();self.code=None

    def close(self):
        with self.lock:
            p=self.process;self.process=None;self.code=None
            if p is None:return
            if sys.platform=='win32':
                p.close()
                return
            host.terminate_owned(p)
            p.wait(timeout=2)
            for stream in (p.stdin,p.stdout,p.stderr):stream.close()

    def request(self, request, *, timeout=None):
        passage = request['operation'] == 'passage'
        split = passage and request['arguments'].get('section', 'source') != 'source'
        stage = 'Score preparation' if passage or request['operation'] == 'validate_import' else 'Passage analysis'
        limit = PREPARATION_SECONDS if stage == 'Score preparation' else LIMITS['deadline_seconds']
        started = time.monotonic()
        caller_end = started + timeout if timeout is not None else float('inf')
        end = min(started + limit, caller_end)
        cancelled = cancel_event.get()
        while not self.lock.acquire(timeout=min(.1, max(0, end - time.monotonic()))):
            if cancelled is not None and cancelled.is_set():
                raise AdvancedFailure('analysis_timeout', f'{stage} was cancelled.')
            if time.monotonic() >= end:
                raise AdvancedFailure('analysis_timeout', f'{stage} timed out waiting for the worker.')
        try:
            payload=(canonical(request)+'\n').encode()
            if len(payload)>12*1024*1024:
                raise AdvancedFailure('analysis_limit_exceeded','Passage request exceeds existing worker input budget.')
            from composerlint.report_reuse import code_identity
            code=code_identity()
            if self.process is not None and self.code!=code:self.close()
            windows=sys.platform=='win32'
            if self.process is None or (self.process.exited() if windows else self.process.poll() is not None):
                self.close()
                command=[sys.executable,'-I','-B',str(Path(__file__).with_name('advanced_worker.py')),'--passage-session']
                if windows:
                    from composerlint.windows_workers import PassageProcess
                    self.process=PassageProcess(command)
                else:
                    self.process=host.popen_owned(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE,bufsize=0)
                self.code=code
            p=self.process
            def exchange(data, deadline):
                if cancelled is not None and cancelled.is_set():
                    raise InterruptedError('Request cancelled')
                try:
                    if windows:
                        raw = p.exchange(data, deadline, cancelled=cancelled)
                    else:
                        raw = host.exchange_line(p, data, deadline, cancelled=cancelled)
                except host.PipeLimitError as exc:
                    self.close()
                    raise AdvancedFailure('analysis_limit_exceeded', 'Passage response exceeded its transport budget.') from exc
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(stage, limit)
                return json.loads(raw)

            if split:
                response = exchange((canonical({**request, 'operation':'begin_passage'})+'\n').encode(), end)
                if response['ok']:
                    stage = 'Passage analysis'
                    end = min(caller_end, time.monotonic() + LIMITS['deadline_seconds'])
                    response = exchange(b'{"operation":"finish_passage"}\n', end)
            else:
                response = exchange(payload, end)
            if code_identity()!=code:
                self.close()
                raise AdvancedFailure('stale_snapshot','Implementation changed during the owned request.')
            if not response['ok']:
                error=response['error']
                if error.get('legacy'):
                    from composerlint.errors import ComposerLintError
                    raise ComposerLintError(error['code'],error['reason'])
                raise AdvancedFailure(error['code'],error['reason'],error.get('record_ref'))
            return response['result']
        except host.UnsupportedPlatformOperation as exc:
            raise AdvancedFailure('unsupported_analysis_case',str(exc)) from exc
        except (subprocess.TimeoutExpired,TimeoutError) as exc:
            self.close()
            raise AdvancedFailure('analysis_timeout', f'{stage} exceeded its deadline. The worker was stopped.') from exc
        except InterruptedError as exc:
            self.close()
            raise AdvancedFailure('analysis_timeout', f'{stage} was cancelled.') from exc
        except OSError as exc:
            if sys.platform!='win32':
                self.close();raise
            self.close()
            raise AdvancedFailure('unsupported_analysis_case',f'Windows passage worker unavailable: {exc}') from exc
        except (AdvancedFailure,):raise
        except BaseException as exc:
            self.close()
            if cancelled is not None and cancelled.is_set():
                raise AdvancedFailure('analysis_timeout', f'{stage} was cancelled.') from exc
            raise
        finally:self.lock.release()


worker=Worker()
atexit.register(worker.close)


def serve():
    from composerlint.errors import ComposerLintError
    store=Store()
    pending = None
    while True:
        line=sys.stdin.buffer.readline(12*1024*1024+1)
        if not line:return
        try:
            if len(line)>12*1024*1024:raise AdvancedFailure('analysis_limit_exceeded','Passage request exceeds input budget.')
            request=json.loads(line)
            if request == {'operation':'finish_passage'} and pending is not None:
                args, prepared, overhead = pending
                pending = None
                try:
                    page = store.run(args, response_overhead=overhead, prepared=prepared)
                finally:
                    del prepared
                sys.stdout.buffer.write((canonical({'ok': True, 'result': page.model_dump(mode='json')})+'\n').encode())
                sys.stdout.buffer.flush()
                continue
            pending = None
            if request.get('operation') == 'validate_import':
                if set(request) != {'operation', 'path', 'fingerprint'}:
                    raise ValueError('Invalid import request')
                store.validate_import(request['path'], request['fingerprint'])
                sys.stdout.buffer.write(b'{"ok":true,"result":null}\n')
                sys.stdout.buffer.flush()
                continue
            if request.get('operation') == 'expressive_brief_context':
                if set(request) != {'operation','arguments','path','options'}:
                    raise ValueError('Invalid private brief context envelope')
                from composerlint.expressive_brief import context
                result = context(store, request['arguments'], request['path'], request['options'])
                sys.stdout.buffer.write((canonical({'ok':True,'result':result})+'\n').encode());sys.stdout.buffer.flush()
                continue
            if set(request)-{'operation','arguments','response_overhead'} or request.get('operation') not in ('passage','begin_passage'):
                raise ValueError('Invalid private passage-worker operation')
            overhead=request.get('response_overhead',0)
            if type(overhead) is not int or not 0<=overhead<8192:raise ValueError('Invalid private byte accounting')
            args = PassageArguments.model_validate(request['arguments'])
            if request['operation'] == 'begin_passage':
                pending = (args, store.begin(args), overhead)
                sys.stdout.buffer.write(b'{"ok":true,"result":null}\n')
                sys.stdout.buffer.flush()
                continue
            result=store.run(args,response_overhead=overhead)
            response={'ok':True,'result':result.model_dump(mode='json')}
        except (AdvancedFailure,ComposerLintError) as exc:
            response={'ok':False,'error':{'code':exc.code,'reason':exc.reason,'record_ref':getattr(exc,'record_ref',None),'legacy':isinstance(exc,ComposerLintError)}}
        sys.stdout.buffer.write((canonical(response)+'\n').encode());sys.stdout.buffer.flush()
