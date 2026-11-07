"""One isolated deadline for M3+M4+validation+projection; no renderer or persistent cache."""
import json,subprocess,sys,time
from pathlib import Path
from composerlint.review import canonical
from composerlint.evaluation_models import EvaluationQuery,EvaluationSnapshot,EvaluationPage,EvaluationFailure
from composerlint.evaluation_policy import LIMITS


def request_worker(request,*,timeout=None):
    deadline=LIMITS['deadline_seconds'] if timeout is None else timeout;started=time.monotonic()
    if request['operation']=='page' and 'base_report' not in request:
        from composerlint.report_reuse import take
        args=request['arguments'];base=take(args['path'],args.get('options'))
        if base is not None:request={**request,'base_report':base}
    payload=canonical(request);remaining=deadline-(time.monotonic()-started)
    if remaining<=0:raise EvaluationFailure('analysis_timeout','Whole evaluation deadline exceeded before worker launch.')
    try:cp=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).with_name('evaluation_worker.py'))],input=payload,capture_output=True,text=True,timeout=remaining)
    except subprocess.TimeoutExpired as exc:raise EvaluationFailure('analysis_timeout',f'Evaluation exceeded {deadline} seconds; isolated worker terminated and reaped. Narrow the scope.') from exc
    if cp.returncode:raise RuntimeError('Evaluation worker implementation failure: '+cp.stderr[-4000:])
    if len(cp.stdout.encode())>64*1024*1024:raise EvaluationFailure('analysis_limit_exceeded','Worker exceeded 64 MiB response limit.')
    try:payload=json.loads(cp.stdout)
    except ValueError as exc:raise RuntimeError('Evaluation worker returned invalid JSON: '+cp.stderr[-2000:]) from exc
    if time.monotonic()-started>=deadline:raise EvaluationFailure('analysis_timeout','Whole evaluation deadline exceeded; no successful prefix returned.')
    if not payload['ok']:
        e=payload['error']
        if e.get('legacy'):
            from composerlint.errors import ComposerLintError
            raise ComposerLintError(e['code'],e['reason'])
        raise EvaluationFailure(e['code'],e['reason'],e.get('record_ref'))
    return payload['result']


def analyze(path,options=None,query=None):
    from composerlint.engine.parser import _options
    from composerlint.engine.evaluation import MusicalEvaluation
    from composerlint.engine.advanced import AdvancedAnalysis
    from composerlint.advanced_models import AdvancedSnapshot
    from composerlint.analysis_models import AnalysisReport
    if not isinstance(path,(str,Path)):
        from composerlint.errors import ComposerLintError
        raise ComposerLintError('invalid_options','path must be a local string or Path')
    opts=_options(options);q=EvaluationQuery.model_validate(query.model_dump(mode='json') if isinstance(query,EvaluationQuery) else query or {})
    r=request_worker({'operation':'analysis','path':str(path),'options':opts.model_dump(mode='json'),'query':q.model_dump(mode='json')})
    return MusicalEvaluation(AdvancedAnalysis(AnalysisReport.model_validate(r['report']),AdvancedSnapshot.model_validate(r['advanced_snapshot'])),EvaluationSnapshot.model_validate(r['snapshot']))


def page(args):
    return EvaluationPage.model_validate(request_worker({'operation':'page','arguments':args.model_dump(mode='json')}))
