"""One shared service for narrow MCP operations and browser discard."""
from composerlint.editing_runtime import editing

def run(name,args):
    return {'get_edit_workspace':editing.open,'prepare_score_edit':editing.prepare,'get_edit_proposal':editing.get,'discard_edit_proposal':editing.discard,
        'prepare_expressive_edit_brief':editing.expressive_brief}[name](args)


def browser_route(token,resource,post,body):
    """Capability-local routes; no input path, XML, generic invocation or export."""
    import json
    import secrets
    from composerlint.viewer_runtime import runtime, current_source
    from composerlint.editing_models import DiscardArguments
    from composerlint.viewer_source import ViewerFailure
    from composerlint.review import canonical
    from pydantic import ValidationError
    with runtime.lock:
        runtime.prune()
        entry=next((e for e in runtime.entries.values() if secrets.compare_digest(e['token'],token)),None)
    if entry is None:return 410,b'Workspace expired.','text/plain'
    try:
        if resource=='approve-export':
            if not post:return 405,b'POST required.','text/plain'
            from composerlint.editing_export import approve_export
            with editing.lock:
                w=editing.find(entry.get('workspace_id'))
                if (not isinstance(body,dict) or set(body)!={'nonce','binding'}
                        or not secrets.compare_digest(str(body['nonce']),w['csrf'])):
                    return 403,b'Invalid workspace nonce.','text/plain'
                ident=w['id']
            result=approve_export(editing,ident,body['binding'])
            return 200,canonical(result).encode(),'application/json'
        if resource=='analysis.json' and not post:
            return 410,canonical({'error':{'code':'presentation_retired','reason':'Analysis publication retired. Use existing analysis tools and Codex conversation.'}}).encode(),'application/json'
        with editing.lock:
            w=None
            if entry.get('workspace_id'):
                try:w=editing.find(entry['workspace_id'])
                except ViewerFailure as exc:
                    if exc.code!='workspace_expired' or not entry.get('review_context'):raise
                    entry.pop('workspace_id',None)
            if resource=='workspace.json' and not post and w is None and entry.get('review_context'):
                stale=False
                try:current_source(entry)
                except ViewerFailure:stale=True
                context={**entry['review_context'], 'workspace_id':None, 'workspace_revision':0,
                    'state':'stale' if stale else 'no_proposal', 'view_instance_id':entry['instance'],
                    'selection':runtime.selection_value(entry), 'proposal':None, 'last_error':None,
                    'analysis':None,
                    'expires_in_seconds':None, 'supported_operations':['octave_transpose_up','octave_transpose_down','exact_pitch_batch'],
                    'notice':'Viewing and analysis are ready. Ask Codex for an exact edit to prepare a temporary proposal.'}
                return 200,canonical(context).encode(),'application/json'
            if w is None:w=editing.find(entry.get('workspace_id'))
            if resource=='workspace.json' and not post:
                try:editing.current(w);editing.check_candidate(w)
                except ViewerFailure:pass
                try:current_source(entry)
                except ViewerFailure:pass
                return 200,canonical({**editing.state(w),'analysis':None}).encode(),'application/json'
            if resource=='discard' and post:
                if not isinstance(body,dict) or set(body)!={'proposal_id','expected_workspace_revision','nonce'} or not secrets.compare_digest(str(body['nonce']),w['csrf']):return 403,b'Invalid workspace nonce.','text/plain'
                args=DiscardArguments(workspace_id=w['id'],proposal_id=body['proposal_id'],expected_workspace_revision=body['expected_workspace_revision'])
                result=editing.discard(args)
                return 200,result.canonical_json().encode(),'application/json'
            if post:return 405,b'Unsupported workspace mutation.','text/plain'
            editing.current(w);editing.check_candidate(w);p=w['proposal']
            if p is not None and p['preview_status']=='ready' and w['state'] in ('verified','preparing'):
                prefix='p-'+p['proposal_id']+'-'
                if resource.startswith(prefix+'changes-') and resource.endswith('.json'):
                    try:
                        offset=int(resource[len(prefix+'changes-'):-5])
                        if resource!=prefix+f'changes-{offset}.json' or not 0<=offset<len(p['changes']):raise ValueError()
                    except ValueError:return 400,b'Invalid change page.','text/plain'
                    end=min(offset+16,len(p['changes']))
                    return 200,canonical({'items':p['changes'][offset:end],'start_index':offset,'total_items':len(p['changes']),'next_offset':end if end<len(p['changes']) else None}).encode(),'application/json'
                if resource==prefix+'manifest.json':return 200,canonical(p['manifest']).encode(),'application/json'
                if resource==prefix+'verification.json':
                    return 200,canonical({'proposal_id':p['proposal_id'],'candidate_sha256':p['candidate_sha256'],
                        'verification_sha256':p['verification_sha256'],'gates':p['verification']['gates'],
                        'validation_level':p['verification']['validation_level'],'notation':p['verification']['notation'],
                        'analysis':p['verification']['analysis']}).encode(),'application/json'
                for i,svg in enumerate(p['pages'],1):
                    if resource==prefix+f'page-{i}.svg':return 200,svg,'image/svg+xml'
            return 410,b'Proposal unavailable or replaced.','text/plain'
    except (ViewerFailure,ValidationError,OSError) as exc:
        return 409,canonical({'error':{'code':getattr(exc,'code','invalid_edit'),'reason':str(exc)}}).encode(),'application/json'
