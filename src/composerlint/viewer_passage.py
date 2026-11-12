"""Source-only viewer integration; the existing passage engine owns musical work."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from composerlint.platform_support import read_score_bytes
from composerlint import passage
from composerlint.review import canonical,digest
from composerlint.viewer_models import SourceSelection,SourceViewerPage,ViewerPassagePage
from composerlint.advanced_models import PassageArguments,PassageQuery,PASSAGE_POLICY,AdvancedFailure
from composerlint.viewer_source import fail,source_index


def encode_selection(selection,entry):
    data=selection.model_dump(mode='json')
    signature=hmac.new(entry['token'].encode(),canonical(data).encode(),hashlib.sha256).hexdigest()
    return 'CLV2.'+base64.urlsafe_b64encode(canonical({'selection':data,'signature':signature}).encode()).decode().rstrip('=')


def checked_selection(token,path,expected_source,options=None,*,instance=None,revision=None,query=None):
    """Verify server-authenticated current state, not a caller's checksum."""
    from composerlint.viewer_runtime import runtime,current_source
    from composerlint.engine.parser import _options
    try:
        if not token.startswith('CLV2.') or len(token)>4096:raise ValueError()
        value=json.loads(base64.b64decode(token[5:]+'='*(-len(token[5:])%4),altchars=b'-_',validate=True))
        if set(value)!={'selection','signature'}:raise ValueError()
        selection=SourceSelection.model_validate(value['selection'])
    except (ValueError,TypeError,KeyError):fail('invalid_selection','Malformed source-bound viewer reference.')
    with runtime.lock:
        entry=runtime.find(selection.view_instance_id)
        if entry is None:fail('view_expired','Source-bound selection requires its original live view.')
        expected=hmac.new(entry['token'].encode(),canonical(selection.model_dump(mode='json')).encode(),hashlib.sha256).hexdigest()
        if not isinstance(value['signature'],str) or not hmac.compare_digest(expected,value['signature']):
            fail('invalid_selection','Source-bound viewer signature does not match.')
        runtime.validate_live(selection.view_instance_id,selection.revision,selection)
        if instance is not None and instance!=selection.view_instance_id or revision is not None and revision!=selection.revision:
            fail('stale_selection','Wrong viewer instance or selection revision.')
        if (entry.get('source_path')!=str(Path(path).expanduser().resolve()) or
            expected_source!=selection.source_snapshot_id or digest(_options(options).model_dump(mode='json'))!=selection.options_sha256):
            fail('stale_selection','Selection source, path or effective options differ.')
        current_source(entry)
        if query is not None:
            scope=query.scope
            if scope is None or (scope.reference_part_id,scope.start_measure,scope.end_measure)!=(selection.reference_part_id,selection.start_measure_index,selection.end_measure_index):
                fail('stale_selection','Passage range differs from the current live selection.')
            if scope.part_ids is not None and not set(scope.part_ids)<=set(selection.part_ids):
                fail('invalid_selection','Passage focus is outside the displayed instruments.')
        return selection,entry


def bound_passage(args):
    """Keep live checks and the existing isolated computation inside 25 seconds."""
    from composerlint.advanced_runtime import request_worker,LIMITS
    started=time.monotonic()
    from composerlint.viewer_source import ViewerFailure
    def checked():
        try:return checked_selection(args.viewer_selection_ref,args.path,args.expected_source_snapshot,args.options,query=args.query())
        except ViewerFailure as exc:raise AdvancedFailure('unavailable_evidence',exc.code+': '+exc.reason) from exc
    selection,_=checked()
    viewer_fields={'viewer_schema_version':2,'viewer_selection':selection.model_dump(mode='json'),
                   'viewer_selection_ref':args.viewer_selection_ref}
    # Exact additional canonical bytes when these fields join the result object.
    overhead=len(canonical(viewer_fields).encode())-1
    result=request_worker({'operation':'passage','arguments':args.model_dump(mode='json'),'response_overhead':overhead},
        timeout=max(0,LIMITS['deadline_seconds']-(time.monotonic()-started)))
    checked()
    if time.monotonic()-started>=LIMITS['deadline_seconds']:
        raise AdvancedFailure('analysis_timeout','Live passage request exceeded its existing deadline.')
    page=ViewerPassagePage(**result,**viewer_fields)
    if len(canonical(page.model_dump(mode='json')).encode())>args.max_bytes:
        raise AdvancedFailure('oversized_record','Complete viewer-bound passage envelope exceeds the unchanged byte budget.')
    return page


def rows(measures,notes):
    mids={m['id'] for m in measures}
    result=[{'record_ref':'measure:'+m['id'],'data':{k:v for k,v in m.items() if k not in ('note_ids','renderer_id')}} for m in measures]
    result.extend({'record_ref':'segment:'+n['source_segment_id'],'data':n} for n in notes.values() if n['measure_id'] in mids)
    return result


def project(args,base,items):
    binding=digest({'source':base['source_snapshot_id'],'key':base['view_key'],'kind':base['kind'],
        'view':base['view_instance_id'],'selection':base['selection'].model_dump(mode='json') if base.get('selection') else None,
        'passage':base['passage_request'].query().model_dump(mode='json') if base.get('passage_request') else None,
        'notation_page':base.get('notation_page'),'page_size':args.page_size,'max_bytes':args.max_bytes})
    offset=0
    if args.cursor:
        try:
            key,raw=args.cursor.split(':');offset=int(raw)
            if key!=binding or str(offset)!=raw or not 0<offset<len(items):raise ValueError()
        except ValueError:fail('invalid_cursor','Continuation belongs to a different source/view/selection/query.')
    def make(end):
        return SourceViewerPage(**base,total_items=len(items),start_index=offset,returned_count=end-offset,items=items[offset:end],
            next_cursor=None if end==len(items) else binding+':'+str(end),section_exhausted=end==len(items))
    end=offset
    for n in range(offset+1,min(offset+args.page_size,len(items))+1):
        if len(make(n).canonical_json().encode())>args.max_bytes:break
        end=n
    result=make(end)
    if end==offset and offset<len(items) or len(result.canonical_json().encode())>args.max_bytes:
        fail('oversized_record','Complete source viewer record exceeds the unchanged response budget.')
    return result


def run(args,resolving=False):
    from composerlint.viewer_runtime import runtime
    from composerlint.viewer import verify_pages,check_source
    from composerlint.viewer_source import ViewerFailure
    timings={};started=time.monotonic()
    # Retain viewer XML safety checks before the normalizer sees the source.
    local=Path(args.path).expanduser()
    try:data=read_score_bytes(local) if local.stat().st_size<=10*1024*1024 else None
    except OSError:fail('file_not_found','Source is not readable.')
    if data is None:fail('asset_too_large','Source exceeds the existing 10 MiB viewer limit.')
    if local.suffix.lower() not in ('.musicxml','.xml'):fail('unsupported_notation','Viewer supports uncompressed MusicXML.')
    import re
    if b'<!ENTITY' in data.upper() or re.search(br'<!DOCTYPE[^>]*\[',data,re.I):fail('unsupported_notation','XML entity declarations are unsupported.')
    selection=entry=None
    if resolving:
        selection,entry=checked_selection(args.selection_ref,args.path,args.expected_source_snapshot,args.options,instance=args.view_instance_id,revision=args.revision)
    source=passage.prepare(local,args.options,timings)
    if args.expected_source_snapshot is not None and args.expected_source_snapshot!=source.source_snapshot_id:
        fail('stale_snapshot','Source/options/policy changed; reopen the source-bound score.')
    score=source.score.model_dump(mode='json');root,measures,notes=source_index(data,score)
    if len(measures)>2000 or len({(n['part_id'],n['staff']) for n in notes.values()})>32:
        fail('asset_too_large','Viewer limit: 2,000 source measures and 32 staves.')
    parts=[p['id'] for p in score['parts']]
    reference=selection.reference_part_id if resolving else args.reference_part_id or parts[0]
    ref=[m for m in measures if m['part_id']==reference]
    if not ref:fail('invalid_selection','Reference part has no source measures.')
    identity={'source_sha256':score['source_sha256'],'source_snapshot_id':source.source_snapshot_id,'options_sha256':digest(score['options'])}
    scope={**identity,'execution_policy':PASSAGE_POLICY,'reference_part_id':reference,'part_ids':parts,
           'start_measure_index':1,'end_measure_index':len(ref)}
    key=entry['key'] if resolving else digest({'source':identity,'reference':reference,'adapter':'source_bound_viewer_v3_pages','width':args.width})
    notices=['Complete original written notation; musical evidence uses the source-bound passage route.',
             'All instruments and sounding occupants remain evidence; selection edges do not establish phrases or cadences.',
             'Source preparation repeats per request. No whole-score M1 report is constructed.']
    base=dict(kind='selection' if resolving else 'view',**identity,view_instance_id=None,view_key=key,
              presentation='text',status='resolved' if resolving else 'text',notices=notices,timings=timings)
    selected=measures;request=None
    if resolving:
        selected_query=PassageQuery(execution_policy=PASSAGE_POLICY,expected_source_snapshot=source.source_snapshot_id,
            scope={'reference_part_id':reference,'start_measure':selection.start_measure_index,'end_measure':selection.end_measure_index,
                   'part_ids':args.focus_part_ids,'voice_ids':args.focus_voice_ids})
        resolved=passage.resolve_source(source.score,selected_query)
        if args.focus_part_ids and len(set(args.focus_part_ids))!=len(args.focus_part_ids) or args.focus_voice_ids and len(set(args.focus_voice_ids))!=len(args.focus_voice_ids):
            fail('invalid_selection','Focus IDs must be distinct.')
        target=resolved.target_spans[0];a,b=target.start.fraction(),target.end.fraction()
        from composerlint.models import RationalTime
        selected=[m for m in measures if RationalTime.model_validate(m['start']).fraction()<b and RationalTime.model_validate(m['end']).fraction()>a]
        request=PassageArguments(path=args.path,options=args.options,**selected_query.model_dump(mode='json'),section='overview',viewer_selection_ref=args.selection_ref)
        base.update(view_instance_id=entry['instance'],selection=selection,selection_ref=args.selection_ref,passage_request=request)
        output=rows(selected,notes)
        output.append({'record_ref':'source:passage_scope','data':resolved.model_dump(mode='json')})
        checked_selection(args.selection_ref,args.path,args.expected_source_snapshot,args.options,instance=args.view_instance_id,revision=args.revision)
    else:
        output=rows(selected,notes)
        if args.presentation!='text':
            if args.cursor or args.view_instance_id:
                entry=runtime.find(args.view_instance_id)
                if entry is None or entry['key']!=key:fail('view_expired','Continuation requires the matching source-bound live view.')
            else:
                with runtime.lock:
                    runtime.prune()
                    entry=next((e for e in reversed(runtime.entries.values()) if e['key']==key and e.get('source_path')==str(local.resolve())),None)
            if entry is None:
                from composerlint.renderers.verovio import render
                t=time.monotonic()
                try:
                    from composerlint.viewer_pages import layout_pages,verify_page
                    xml=ET.tostring(root,encoding='unicode');rendered=render(xml,width=args.width,notation_page=1)
                    pages=layout_pages(rendered,measures,notes)
                    verify_page(rendered['svg'],pages[0],measures,notes)
                    timings.update(rendered.get('timings',{}))
                    manifest={'title':root.findtext('work/work-title') or root.findtext('movement-title') or 'Sheet music',
                        'viewer_schema_version':2,'scope':scope,'mode':'whole_score','view_key':key,'selection':None,
                        'measures':[{k:v for k,v in m.items() if k not in ('note_ids','renderer_id')} for m in measures],
                        'note_measure_ids':{k:v['measure_id'] for k,v in notes.items()},'pages':pages,'page_count':len(pages),
                        'groups':pages[0]['groups'],'findings':[],'notices':notices,'renderer_version':rendered['version'],
                        'renderer_build':rendered['package_version'],'svg_delivery':'on_demand_verified_before_delivery'}
                    check_source(local,data);entry=runtime.add(key,[rendered['svg']]+[None]*(len(pages)-1),manifest,(xml,measures,notes),
                        lazy_renderer={'width':args.width,'layout_sha256':digest(pages),'build':rendered['package_version']})
                    entry['source_path']=str(local.resolve())
                except ViewerFailure as exc:
                    if args.presentation!='auto':raise
                    base['status']=exc.code;base['notices'].append(exc.reason+' Source-bound text evidence remains available.')
                timings['render_mapping_seconds']=time.monotonic()-t
            else:timings['render_mapping_seconds']=0.0
            if entry is not None:
                if args.notation_page>entry['manifest']['page_count']:fail('invalid_page','Notation page is outside this score.')
                pending_page=entry['pages'][args.notation_page-1] is None
                page_started=time.monotonic()
                base.update(runtime.references(entry,args.notation_page),page_count=entry['manifest']['page_count'],notation_page=args.notation_page,
                            presentation='svg' if args.presentation=='svg' else 'interactive',status='mapped')
                if pending_page:timings['render_mapping_seconds']=time.monotonic()-page_started
                ids=set(entry['manifest']['pages'][args.notation_page-1]['measure_ids'])
                output=rows([m for m in measures if m['id'] in ids],notes)
    check_source(local,data);timings['viewer_preprojection_seconds']=time.monotonic()-started
    return project(args,base,output)
