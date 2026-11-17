"""One bounded stdlib computation process. No renderer, listener or persistence."""
from pathlib import Path
import json
import sys


def main():
    # Also works from an extracted wheel without installing it or altering global paths.
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    if sys.argv[1:]==['--passage-session']:
        from composerlint.passage_session import serve
        serve()
        return
    from composerlint.platform_support import read_score_bytes
    from composerlint.engine.advanced import compute
    from composerlint.advanced_models import AdvancedArguments,AdvancedFailure
    from composerlint.errors import ComposerLintError
    from composerlint.review import canonical
    request=json.load(sys.stdin)
    try:
        if request['operation']=='passage':
            from composerlint.passage import run
            from composerlint.advanced_models import PassageArguments
            result=run(PassageArguments.model_validate(request['arguments']),_response_overhead=request.get('response_overhead',0))
            sys.stdout.write(canonical({'ok':True,'result':result.model_dump(mode='json')}))
            return
        if request['operation']=='page':
            from composerlint.advanced_review import project_advanced
            args=AdvancedArguments.model_validate(request['arguments'])
            bundle=compute(args.path,args.options,args.query(),_base_report=request.get('base_report'))
            result=project_advanced(bundle,args).model_dump(mode='json')
        else:
            bundle=compute(request['path'],request['options'],request['query'])
            result={'report':bundle.report.model_dump(mode='json'),'snapshot':bundle.snapshot.model_dump(mode='json')}
        import hashlib
        if hashlib.sha256(read_score_bytes(Path(args.path if request['operation']=='page' else request['path']).expanduser())).hexdigest()!=bundle.snapshot.source_sha256:
            raise AdvancedFailure('stale_snapshot','Source changed during evidence projection; restart with a fresh overview.')
        output={'ok':True,'result':result}
    except (AdvancedFailure,ComposerLintError) as exc:
        output={'ok':False,'error':{'code':exc.code,'reason':exc.reason,'record_ref':getattr(exc,'record_ref',None),'legacy':isinstance(exc,ComposerLintError)}}
    sys.stdout.write(canonical(output))


if __name__=='__main__':main()
