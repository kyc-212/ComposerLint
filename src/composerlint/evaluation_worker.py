"""Finite read-only worker; computes M3 once directly, never nests a worker."""
from pathlib import Path
import json,sys,hashlib


def main():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from composerlint.platform_support import read_score_bytes
    from composerlint.engine.evaluation import compute
    from composerlint.evaluation_models import EvaluationArguments,EvaluationFailure
    from composerlint.advanced_models import AdvancedFailure
    from composerlint.errors import ComposerLintError
    from composerlint.review import canonical
    request=json.load(sys.stdin)
    try:
        if request['operation']=='page':
            from composerlint.engine.evaluation import _build_evaluation
            from composerlint.evaluation_review import project_evaluation
            args=EvaluationArguments.model_validate(request['arguments']);path=args.path
            # This bundle stays worker-owned. Projection performs full validation
            # once, immediately before publication; public compute still validates.
            bundle=_build_evaluation(path,args.options,args.query(),_base_report=request.get('base_report'));result=project_evaluation(bundle,args).model_dump(mode='json')
        else:
            path=request['path'];bundle=compute(path,request['options'],request['query'])
            result={'report':bundle.advanced.report.model_dump(mode='json'),'advanced_snapshot':bundle.advanced.snapshot.model_dump(mode='json'),'snapshot':bundle.snapshot.model_dump(mode='json')}
        if hashlib.sha256(read_score_bytes(Path(path).expanduser())).hexdigest()!=bundle.snapshot.source_sha256:raise EvaluationFailure('stale_snapshot','Source changed during evaluation/projection; no successful result returned.')
        output={'ok':True,'result':result}
    except (EvaluationFailure,AdvancedFailure,ComposerLintError) as exc:
        output={'ok':False,'error':{'code':exc.code,'reason':exc.reason,'record_ref':getattr(exc,'record_ref',None),'legacy':isinstance(exc,ComposerLintError)}}
    sys.stdout.write(canonical(output))


if __name__=='__main__':main()
