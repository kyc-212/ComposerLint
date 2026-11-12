"""Whole advanced deadline, isolated worker and optional owned M1 report reuse."""
import json
from pathlib import Path
import subprocess
import sys
import time
from composerlint.advanced_models import AdvancedFailure,AdvancedQuery,AdvancedSnapshot,AdvancedPage
from composerlint.engine.advanced import AdvancedAnalysis,LIMITS
from composerlint.analysis_models import AnalysisReport
from composerlint.review import canonical
from composerlint import platform_support as host


def request_worker(request,*,timeout=None):
    if request['operation']=='passage':
        from composerlint.passage_session import worker
        return worker.request(request,timeout=timeout)
    deadline=LIMITS['deadline_seconds'] if timeout is None else timeout
    started=time.monotonic()
    if request['operation']=='page' and 'base_report' not in request:
        from composerlint.report_reuse import take
        args=request['arguments'];base=take(args['path'],args.get('options'))
        if base is not None:request={**request,'base_report':base}
    payload=canonical(request);remaining=deadline-(time.monotonic()-started)
    if remaining<=0:raise AdvancedFailure('analysis_timeout','Advanced deadline exceeded before worker launch.')
    try:
        cp=host.run_owned([sys.executable,'-I','-B',str(Path(__file__).with_name('advanced_worker.py'))],input=payload,capture_output=True,text=True,timeout=remaining)
    except host.UnsupportedPlatformOperation as exc:
        raise AdvancedFailure('unsupported_analysis_case',str(exc)) from exc
    except host.PipeLimitError as exc:
        raise AdvancedFailure('analysis_limit_exceeded', str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise AdvancedFailure('analysis_timeout',f'Advanced request exceeded {deadline} seconds; isolated worker terminated and reaped. Narrow the query/features.') from exc
    if cp.returncode:
        # Programming failures remain internal errors, with the actual child stderr available.
        raise RuntimeError('Advanced worker implementation failure: '+cp.stderr[-4000:])
    if len(cp.stdout.encode())>64*1024*1024:raise AdvancedFailure('analysis_limit_exceeded','Worker response exceeded 64 MiB transport limit.')
    try:payload=json.loads(cp.stdout)
    except ValueError as exc:raise RuntimeError('Advanced worker returned invalid JSON: '+cp.stderr[-2000:]) from exc
    if time.monotonic()-started>=deadline:raise AdvancedFailure('analysis_timeout','Advanced request exceeded its deadline; no successful snapshot returned.')
    if not payload['ok']:
        error=payload['error']
        if error.get('legacy'):
            from composerlint.errors import ComposerLintError
            raise ComposerLintError(error['code'],error['reason'])
        raise AdvancedFailure(error['code'],error['reason'],error['record_ref'])
    return payload['result']


def analyze(path,options=None,query=None):
    from composerlint.engine.parser import _options
    if not isinstance(path,(str,Path)):
        from composerlint.errors import ComposerLintError
        raise ComposerLintError('invalid_options','path must be a local string or Path')
    opts=_options(options)
    q=AdvancedQuery.model_validate(query.model_dump(mode='json') if isinstance(query,AdvancedQuery) else query or {})
    result=request_worker({'operation':'analysis','path':str(path),'options':opts.model_dump(mode='json'),'query':q.model_dump(mode='json')})
    return AdvancedAnalysis(AnalysisReport.model_validate(result['report']),AdvancedSnapshot.model_validate(result['snapshot']))


def page(args):
    return AdvancedPage.model_validate(request_worker({'operation':'page','arguments':args.model_dump(mode='json')}))
