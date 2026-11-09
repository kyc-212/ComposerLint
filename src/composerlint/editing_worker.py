"""Bounded isolated edit/reanalysis/render worker; only owned snapshot paths."""
from pathlib import Path
import json
import os
import hashlib
import sys
import time


def main():
    # Load the same stdlib-only adapter without importing application dependencies.
    import runpy
    support=runpy.run_path(str(Path(__file__).parent / 'platform_support.py'))
    support['watch_parent'](group=True)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from composerlint import service
    from composerlint.review import digest,canonical
    from composerlint.viewer_source import ViewerFailure,source_index,aligned_range
    from composerlint.viewer import verify_pages,records
    from composerlint.renderers.verovio import render
    from composerlint.editing_source import safe_xml,resolve,transpose
    from composerlint.editing_verify import verify,verify_mei
    from composerlint.errors import ComposerLintError
    request=json.load(sys.stdin);started=time.monotonic()
    try:
        path=Path(request['original']);data=path.read_bytes();safe_xml(data)
        before=service.analyze_score(path,request['options'])
        if digest(before.model_dump(mode='json'))!=request['expected_snapshot']:raise ViewerFailure('stale_snapshot','Source/options no longer match the requested base snapshot.')
        if request['action'] in ('prepare','export'):
            operation=request.get('operation',{'kind':'octave_transpose','direction':request.get('direction')})
            exact=operation['kind']=='exact_pitch_batch'
            expressive=operation['kind']=='expressive_edit_batch'
            creation=expressive and operation.get('policy')=='v02_m6_bounded_note_creation_v1'
            if creation:
                from composerlint.editing_creation import resolve as resolve_creation
            target=(resolve_creation if creation else resolve)(data,before,request['selection'],request['target'])
            rhythmic=expressive and operation.get('policy')=='v02_m6_rhythmic_edit_v1'
            if expressive:
                from composerlint.editing_expression import direction,transform
                from composerlint.report_reuse import code_identity
                brief=request['brief'];chosen=direction(brief,operation['direction_id'])
                if (digest(brief)!=operation['brief_sha256'] or brief['source_sha256']!=before.score.source_sha256
                        or brief['base_snapshot']!=request['expected_snapshot']
                        or brief['options_sha256']!=digest(before.options.model_dump(mode='json'))
                        or brief['selection']!=request['selection'] or brief['code_identity']!=code_identity()
                        or any(target[k]!=brief['target'][k] for k in ('part_id','xml_part_id','part_ordinal','voices','start_measure_index','end_measure_index','lane_mode'))):
                    raise ViewerFailure('stale_brief','Actual Original, selection, target, options or implementation differs from the bound brief.')
                if rhythmic:
                    from composerlint.editing_rhythm import transform as rhythm_transform
                    transform=lambda data,before,target,operations: rhythm_transform(data,before,request['selection'],request['target'],operations)
                if creation:
                    from composerlint.editing_creation import transform as creation_transform
                    transform=lambda data,before,target,operations: creation_transform(data,before,request['selection'],request['target'],operations)
                if (brief['draft'].get('creation_policy') or brief['draft'].get('rhythm_policy') or 'v02_m6_expressive_edit_v1')!=operation['policy']:
                    raise ViewerFailure('stale_brief','Operation policy differs from the bound brief.')
                candidate=(Path(request['candidate']).read_bytes() if request['action']=='export' else transform(data,before,target,chosen['operations']))
            elif exact:
                from composerlint.editing_pitch import transform
                candidate,destinations=transform(data,target,operation)
            else:candidate=transpose(data,target,operation['direction'])
            cp=Path(request['candidate'])
            if request['action']!='export':
                with os.fdopen(os.open(cp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f:f.write(candidate)
                cp.chmod(0o600)
            after=service.analyze_score(cp,request['options'])
            if expressive:
                from composerlint.editing_expression_verify import verify,musical_comparison
                if rhythmic:
                    from composerlint.editing_rhythm_verify import verify
                if creation:
                    from composerlint.editing_creation_verify import verify
                verification=verify(data,candidate,before,after,request['selection'],request['target'],chosen['operations'],brief)
                verification['musical_comparison']=musical_comparison(path,cp,request['options'],brief)
                if (rhythmic or creation) and verification['musical_comparison']['status']!='available':
                    raise ViewerFailure('incomplete_rhythm_evidence','Complete before/after context is required for rhythm candidates.')
                changed={r['source_note_ordinal'] for r in verification['changes'] if 'source_note_ordinal' in r}
                target['notes']=[r for r in target['notes'] if r['ordinal'] in changed]
                target['pitched_segment_count']=len(target['notes'])
                target['logical_event_count']=len({r['event_id'] for r in target['notes']})
                verification['brief_binding']={'brief_sha256':operation['brief_sha256'],'direction_id':operation['direction_id'],
                    'source_sha256':brief['source_sha256'],'composer_request':brief['draft']['composer_request'],
                    'direction':chosen,'policy':operation['policy']}
            elif exact:
                from composerlint.editing_verify import verify_exact
                verification=verify_exact(data,candidate,before,after,request['selection'],request['target'],operation)
                target['notes']=[r for r in target['notes'] if r['ordinal'] in destinations]
                target['pitched_segment_count']=len(target['notes'])
                target['logical_event_count']=len({r['event_id'] for r in target['notes']})
            else:verification=verify(data,candidate,before,after,target,operation['direction'])
            report,source=after,candidate
        else:report,source=before,data;target=None;verification=None
        full=report.model_dump(mode='json');root,measures,notes=source_index(source,full['score'])
        if len(measures)>2000 or len({(n['part_id'],n['staff']) for n in notes.values()})>32:raise ViewerFailure('edit_limit','Whole-score preview exceeds 2,000 summed measures or 32 staves.')
        parts=[p.id for p in report.score.parts];end=max(m['ordinal'] for m in measures if m['part_id']==parts[0])
        xml,selected,required=aligned_range(root,measures,notes,parts,parts[0],1,end,bounded=False)
        engine_seconds=time.monotonic()-started;render_start=time.monotonic();rendered=render(xml,width=1800)
        pages=verify_pages(rendered,selected,required);mei=verify_mei(source,rendered['mei'],required,report.score)
        if verification and expressive and not (rhythmic or creation):
            from composerlint.editing_expression_verify import verify_notation
            mei.update(verify_notation(source,report.score,rendered['mei'],rendered['pages'],required,
                [{**op,'_part_id':target['part_id']} for op in chosen['operations']]))
            if any(op['family']!='pitch_register' for op in chosen['operations']):
                # Original marking correspondence is needed for a genuine
                # insert/replace/remove comparison, as well as Proposed's receipt.
                oldroot,oldmeasures,oldnotes=source_index(data,before.score.model_dump(mode='json'))
                oldxml,oldselected,oldrequired=aligned_range(oldroot,oldmeasures,oldnotes,parts,parts[0],1,end,bounded=False)
                oldrender=render(oldxml,width=1800);verify_pages(oldrender,oldselected,oldrequired)
                original_ops=[]
                for op in chosen['operations']:
                    checked={**op,'_part_id':target['part_id']}
                    if op['family']=='articulation':
                        prior=op['symbol'] if op['action']=='remove' else op.get('replaces')
                        checked.update(action='set' if prior else 'remove',symbol=prior or op['symbol'])
                    original_ops.append(checked)
                mei['original_markings']=verify_notation(data,before.score,oldrender['mei'],oldrender['pages'],oldrequired,original_ops)
        if verification and (rhythmic or creation):
            from composerlint.editing_rhythm_verify import verify_notation
            mei.update(verify_notation(source,report.score,rendered['mei'],rendered['pages'],required,verification))
        scope=dict(source_sha256=full['score']['source_sha256'],snapshot_id=digest(full),options_sha256=digest(full['options']),reference_part_id=parts[0],part_ids=parts,start_measure_index=1,end_measure_index=end)
        _,findings=records(full,selected,required)
        manifest=dict(title=root.findtext('work/work-title') or root.findtext('movement-title') or 'Sheet music',viewer_schema_version=1,scope=scope,mode='whole_score',selection=None,view_key=digest(scope),measures=[{k:v for k,v in m.items() if k not in ('note_ids','renderer_id')} for m in selected],pages=pages,page_count=len(pages),groups=pages[0]['groups'],findings=findings,renderer_version=rendered['version'],notices=['Complete literal source notation · written pitch.','Original file unchanged. Temporary preview only; no apply or export.'])
        if verification:
            verification['gates']['notation_correspondence']=True;verification['notation']=mei
            changed={r['ordinal'] for r in target['notes']}
            manifest['changed_note_ids']=[k for k,v in required.items() if v['part_id']==target['part_id'] and v['source_note_ordinal'] in changed]
            manifest['affected_measure_ids']=sorted({v['measure_id'] for k,v in required.items() if k in manifest['changed_note_ids']})
            if rhythmic or creation:
                proposal_ordinals={i for c in verification['changes'] for i in c['proposed_ordinals']}
                _,_,original_notes=source_index(data,before.score.model_dump(mode='json'))
                original_ordinals={c['source_note_ordinal'] for c in verification['changes']}
                manifest['original_changed_note_ids']=[k for k,v in original_notes.items() if v['part_id']==target['part_id'] and v['source_note_ordinal'] in original_ordinals]
                target['rhythm_node_count']=len(proposal_ordinals)
                manifest['changed_note_ids']=[k for k,v in required.items() if v['part_id']==target['part_id'] and v['source_note_ordinal'] in proposal_ordinals]
                manifest['affected_measure_ids']=sorted({v['measure_id'] for k,v in required.items() if v['part_id']==target['part_id'] and v['measure_index'] in verification['affected_measures']})
                verification['gates']['complete_musical_evidence']=True
            if expressive and not (rhythmic or creation):
                marks=[];affected=set(manifest['affected_measure_ids'])
                for change in verification['changes']:
                    if 'kind' not in change:continue
                    affected.update(m['id'] for m in selected if m['part_id']==target['part_id'] and m['ordinal']==change['measure_index'])
                    for row in mei['correspondence']:
                        if change['kind']=='articulation' and row['kind']=='articulation' and row['source_note_ordinal']==change['source_note_ordinal']:
                            marks.extend(row['rendered_ids'])
                        elif change['kind'].startswith('dynamic') and row['kind']=='dynamic' and row['measure_index']==change['measure_index'] and row['absolute_offset']==change['absolute_offset']:
                            marks.append(row['rendered_id'])
                manifest['changed_marking_ids']=sorted(set(marks));manifest['affected_measure_ids']=sorted(affected)
        if verification:
            verification['render_receipt']={'candidate_sha256':scope['source_sha256'],'manifest_sha256':digest(manifest),'svg_sha256':[hashlib.sha256(s.encode()).hexdigest() for s in rendered['pages']],'renderer_version':rendered['version'],'options_sha256':scope['options_sha256']}
        output={'ok':True,'result':{'manifest':manifest,'pages':rendered['pages'],'target':target,'verification':verification,'choices':[{'part_id':p.id,'xml_part_id':p.source_id,'label':p.source_label,'lanes':[{'voice_id':v.id,'staff':v.source_staff,'source_voice':v.source_voice,'identity':v.identity} for v in p.voices]} for p in before.score.parts],'timing':{'engine_seconds':engine_seconds,'render_seconds':time.monotonic()-render_start,'worker_seconds':time.monotonic()-started},'notation':mei,'render_context':[xml,selected,required]}}
    except (ViewerFailure,ComposerLintError) as exc:output={'ok':False,'error':{'code':exc.code,'reason':exc.reason}}
    sys.stdout.write(canonical(output))

if __name__=='__main__':main()
