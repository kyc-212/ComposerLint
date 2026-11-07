"""Source-bound descriptive passages using the existing M3 predicates, without M1.

No preparation cache: every worker request loads the complete source once.
Musical construction and independent reconstruction stay within admitted support.
"""
from dataclasses import dataclass
from pathlib import Path
import base64
import hashlib
import json
import time

from composerlint.models import NormalizedScore
from composerlint.advanced_models import (PASSAGE_POLICY, FEATURES, AdvancedFailure,
    PassageArguments, PassageQuery, PassageScope, PassageSnapshot, PassagePage, AdvancedItem)
from composerlint.engine.advanced import Context, LIMITS, preflight, span, overlap, execute_features
from composerlint.engine.advanced_context import audit_source, chromatic, diatonic
from composerlint.advanced_review import AdvancedGraph, validate_observations
from composerlint.review import canonical, digest, ReviewFailure

COMPONENT_VERSIONS = {f:'1' for f in FEATURES}
ASSUMPTIONS = ['whole_source_preparation_not_global_musical_analysis','literal_source_order_no_repeat_expansion',
    'complete_sounding_occupancy_despite_instrumental_focus','context_is_not_selected_material',
    'no_key_function_cadence_expression_or_form_inference']


@dataclass(frozen=True)
class PreparedSource:
    path: Path
    raw: bytes
    score: NormalizedScore
    source_context: object
    audit: dict
    sealed_score: bytes
    code_id: str
    source_snapshot_id: str


@dataclass(frozen=True)
class PassageBundle:
    source: PreparedSource
    snapshot: PassageSnapshot


def source_identity(payload,context,audit,code):
    return digest({'kind':PASSAGE_POLICY,'normalized_source':json.loads(payload),
        'source_declarations':context.model_dump(mode='json'),
        'transpose_audit':[a.model_dump(mode='json') for a in audit.values()],
        'code_identity':code})


def prepare(path,options,timings):
    from composerlint.engine.parser import load_score
    from composerlint.engine.source_context import read_source_context
    from composerlint.report_reuse import code_identity
    started=time.monotonic();local=Path(path).expanduser()
    if local.suffix.lower() not in ('.xml','.musicxml'):
        raise AdvancedFailure('unsupported_format','Source-bound Stage 1 supports uncompressed MusicXML; legacy MIDI review is unchanged.')
    if local.is_file() and local.stat().st_size>LIMITS['source_bytes']:
        raise AdvancedFailure('analysis_limit_exceeded','Source exceeds the existing 10 MiB limit.')
    if local.is_file() and b'<!ENTITY' in local.read_bytes().upper():
        raise AdvancedFailure('unsupported_analysis_case','XML entity declarations are not supported.')
    t=time.monotonic();score=load_score(local,options);preflight(score)
    timings['loading_normalization_seconds']=time.monotonic()-t
    raw=local.read_bytes();t=time.monotonic();audit=audit_source(score,raw)
    context=read_source_context(local,score)
    timings['metadata_audit_seconds']=time.monotonic()-t
    payload=canonical(score.model_dump(mode='json')).encode();code=code_identity()
    identity=source_identity(payload,context,audit,code)
    timings.update(preparation_seconds=time.monotonic()-started,source_loads=1.0,
        preparation_runs=1.0,preparation_reused_between_requests=0.0,
        normalized_source_revalidations=0.0,transpose_audit_passes=1.0)
    return PreparedSource(local,raw,score,context,audit,payload,code,identity)


def fresh(source):
    from composerlint.score_import import verify_import
    verify_import(source.path)
    if hashlib.sha256(source.path.read_bytes()).hexdigest()!=source.score.source_sha256:
        raise AdvancedFailure('stale_snapshot','Source changed during passage preparation/computation.')


def resolve_source(score,query):
    """Select focus separately from complete, time-aligned source occupancy."""
    scope=query.scope;parts={p.id:p for p in score.parts}
    voices={v.id:v for p in score.parts for v in p.voices}
    if scope is None or scope.reference_part_id not in parts:
        raise AdvancedFailure('invalid_analysis_scope','An existing reference part and passage are required.')
    reference=parts[scope.reference_part_id]
    if scope.end_measure>len(reference.measures):raise AdvancedFailure('invalid_analysis_scope','Passage is outside the reference part.')
    ps=set(scope.part_ids or parts);vs=set(scope.voice_ids or [v for v in voices if voices[v].part_id in ps])
    if not ps<=parts.keys() or not vs<=voices.keys() or any(voices[v].part_id not in ps for v in vs):
        raise AdvancedFailure('invalid_analysis_scope','Focus parts/lanes do not resolve together.')
    a,b=reference.measures[scope.start_measure-1].start.fraction(),reference.measures[scope.end_measure-1].end.fraction()
    n=query.analysis_options.neighbor_measures
    c=reference.measures[max(0,scope.start_measure-1-n)].start.fraction()
    d=reference.measures[min(len(reference.measures)-1,scope.end_measure+n-1)].end.fraction()
    events=[e for p in score.parts for e in p.events]
    occupancy=[e.id for e in events if overlap(e,a,b)]
    occupied=set(occupancy)
    primary=[e.id for e in events if e.id in occupied and e.voice_id in vs]
    admitted={e.id for e in events if overlap(e,c,d)}
    initial=set(admitted)
    # Existing M3 continuity uses adjacent complete logical events. Preserve
    # those endpoints even with a zero-neighbor allowance or a long held tie.
    for vid in {e.voice_id for e in events if e.id in occupied}:
        ordered=sorted((e for e in events if e.voice_id==vid and e.duration.fraction()>0),key=lambda e:(e.onset.fraction(),e.id))
        for i,event in enumerate(ordered):
            if event.id in occupied:
                for k in (i-1,i+1):
                    if 0<=k<len(ordered):admitted.add(ordered[k].id)
        pitched=[e for e in ordered if e.kind=='pitched']
        for i,event in enumerate(pitched):
            if event.id not in occupied:continue
            for k in (i-1,i+1):
                if not 0<=k<len(pitched):continue
                other=pitched[k];admitted.add(other.id)
                left,right=(other,event) if k<i else (event,other)
                # Retain every rest/unknown/overlap between adjacent attacks;
                # otherwise a long gap could become fictitious continuity.
                admitted.update(e.id for e in ordered if e.onset.fraction()<right.onset.fraction() and e.end.fraction()>left.end.fraction())
    # A complete logical endpoint can overlap blockers outside the neighbor
    # window. Keep them as source support; never hide chordal/overlap ambiguity.
    endpoints=[e for e in events if e.id in admitted and e.duration.fraction()>0]
    by_voice={v:[e for e in events if e.voice_id==v] for v in {e.voice_id for e in endpoints}}
    for endpoint in endpoints:
        for e in by_voice[endpoint.voice_id]:
            if overlap(e,endpoint.onset.fraction(),endpoint.end.fraction()):admitted.add(e.id)
    local_voices={e.voice_id for e in events if e.id in initial}
    limitations=['bounded_context_does_not_establish_key_phrase_cadence_or_form']
    if scope.start_measure-1<n:limitations.append('preceding_neighbor_context_reaches_source_start')
    if scope.end_measure+n>len(reference.measures):limitations.append('following_neighbor_context_reaches_source_end')
    if not n:limitations.append('neighbor_measures_disabled_adjacent_logical_support_only')
    primary_set=set(primary)
    return PassageScope(target_spans=[span(a,b)],context_spans=[span(c,d)],
        part_ids=list(parts),voice_ids=[v for v in voices if v in local_voices],
        primary_event_ids=primary,context_event_ids=[e.id for e in events if e.id in admitted and e.id not in primary_set],
        occupancy_event_ids=occupancy,boundary_support_event_ids=[e.id for e in events if e.id in admitted-initial],
        context_limitations=limitations,
        selected_subset=False)


def effective_features(query):
    features=set(query.features);features.add('notes')
    if features & {'patterns','ensemble'}:features.add('melody')
    if 'ensemble' in features:features|={'sonorities','patterns'}
    return [f for f in FEATURES if f in features]


class PassageContext(Context):
    def __init__(self,source,query,resolved):
        super().__init__(source,query,source.audit,resolved,effective_features(query))
        included=set(self.included_ids)
        self.events={eid:e for eid,e in self.events.items() if eid in included}

    def cells(self,voice_ids=None):
        # Harmonic occupancy is complete even when the target focuses one part.
        vids=set(voice_ids or self.resolved.voice_ids)
        source=[e for e in self.events.values() if e.id in set(self.resolved.occupancy_event_ids)
                and e.voice_id in vids and e.duration.fraction()>0]
        for target in self.resolved.target_spans:
            a,b=target.start.fraction(),target.end.fraction()
            cuts=sorted({a,b}|{max(a,e.onset.fraction()) for e in source if overlap(e,a,b)}
                        |{min(b,e.end.fraction()) for e in source if overlap(e,a,b)})
            for x,y in zip(cuts,cuts[1:]):
                if x<y:yield x,y,[e for e in source if e.onset.fraction()<=x and e.end.fraction()>=y]


def compute(source,query,timings):
    started=time.monotonic();resolved=resolve_source(source.score,query)
    timings['scope_seconds']=time.monotonic()-started
    ctx=PassageContext(source,query,resolved);started=time.monotonic();execute_features(ctx)
    timings['scoped_computation_seconds']=time.monotonic()-started
    s=PassageSnapshot(source_sha256=source.score.source_sha256,source_snapshot_id=source.source_snapshot_id,
        options_sha256=digest(source.score.options.model_dump(mode='json')),query_sha256=digest(query.model_dump(mode='json')),
        passage_snapshot_id='0'*64,component_versions=COMPONENT_VERSIONS,requested_query=query,
        effective_features=ctx.effective,resolved_scope=resolved,observations=ctx.observations,coverage=ctx.coverages(),
        transposition_audit=[source.audit[eid] for eid in ctx.included_ids],work_counts=dict(ctx.work),
        assumptions=ASSUMPTIONS)
    if query.expression_policy is not None:
        from composerlint.expression import build
        s.expression_context=build(source,resolved,s.observations)
    s.passage_snapshot_id=digest(s.model_dump(mode='json',exclude={'passage_snapshot_id'}))
    if len(canonical(s.model_dump(mode='json')).encode())>LIMITS['payload_bytes']:
        raise AdvancedFailure('analysis_limit_exceeded','Passage payload exceeds the existing advanced payload limit.')
    return PassageBundle(source,s)


def require(value,reason):
    if not value:raise AdvancedFailure('unavailable_evidence',reason)


def validate_ensemble(events,observation):
    """Independent interval/attack arithmetic, in addition to scoped replay."""
    f=observation.fact
    if f.kind!='ensemble':return
    if f.relation=='motion':
        before=[events[x] for x in f.from_event_ids];after=[events[x] for x in f.to_event_ids]
        require(len(before)==len(after)==2,'Motion needs the two actual source lanes.')
        require([e.voice_id for e in before]==[e.voice_id for e in after]==f.voice_ids,'Motion changed instrumental/source lanes.')
        dirs=[(chromatic(q.sounding_pitch)>chromatic(p.sounding_pitch))-(chromatic(q.sounding_pitch)<chromatic(p.sounding_pitch)) for p,q in zip(before,after)]
        old=(diatonic(before[1].sounding_pitch)-diatonic(before[0].sounding_pitch),chromatic(before[1].sounding_pitch)-chromatic(before[0].sounding_pitch))
        new=(diatonic(after[1].sounding_pitch)-diatonic(after[0].sounding_pitch),chromatic(after[1].sounding_pitch)-chromatic(after[0].sounding_pitch))
        expected='stationary' if dirs==[0,0] else 'oblique' if 0 in dirs else 'contrary' if dirs[0]!=dirs[1] else 'parallel' if old==new and f.synchronous_attacks else 'same_direction'
        require(f.directions==dirs and f.motion==expected and f.interval_semitones==new[1],'Motion arithmetic disagrees with source.')
    elif f.relation=='doubling':
        left,right=[events[x] for x in f.from_event_ids]
        dc=chromatic(right.sounding_pitch)-chromatic(left.sounding_pitch)
        dd=diatonic(right.sounding_pitch)-diatonic(left.sounding_pitch)
        kind='unison' if dc==dd==0 else 'octave' if dc!=0 and dc%12==0 and dd%7==0 else None
        require(kind is not None and f.doubling==kind and f.interval_semitones==dc,'Doubling is not a verified sounding unison/octave.')
    elif f.relation=='coordination':
        a=observation.span.start.fraction();b=observation.span.end.fraction()
        occupied=[e for e in events.values() if e.voice_id in f.voice_ids and e.duration.fraction()>0 and e.onset.fraction()<=a and e.end.fraction()>=b]
        require(set(f.from_event_ids)=={e.id for e in occupied},'Coordination omitted a source occupant.')
        attacks=[any(e.kind=='pitched' and e.voice_id==v and e.onset.fraction()==a for e in occupied) for v in f.voice_ids]
        require(f.attacks==attacks and f.joint_attacks==int(all(attacks)) and f.separate_attacks==int(sum(attacks)==1) and f.denominator_attacks==sum(attacks),'Attack/hold coordination disagrees with source.')


def validate(bundle,timings):
    """Reconstruct source semantics and all scoped records, never an M1 report."""
    started=time.monotonic();source=bundle.source;s=bundle.snapshot
    fresh(source)
    require(canonical(source.score.model_dump(mode='json')).encode()==source.sealed_score,'Prepared source was mutated.')
    # This retains the normalized-source integrity boundary, not musical M1 validation.
    score=NormalizedScore.model_validate_json(source.sealed_score)
    audit=audit_source(score,source.raw)
    require(audit==source.audit,'Prepared transposition audit was altered.')
    require(source_identity(source.sealed_score,source.source_context,audit,source.code_id)==source.source_snapshot_id,'Prepared source identity mismatch.')
    from composerlint.report_reuse import code_identity
    require(source.code_id==code_identity(),'Source policy/code changed.')
    timings.update(normalized_source_revalidations=1.0,transpose_audit_passes=2.0)
    return _validate_owned_passage(bundle,timings,score=score,audit=audit,_started=started)


def _validate_owned_passage(bundle,timings,*,score,audit,_started=None):
    """Identical scoped semantic reconstruction on a privately verified source.

    The MCP store authenticates its source before calling this; no public query
    can supply this object, an audit, or a validation-skip flag. Public validate
    above retains its independent normalized-source reconstruction.
    """
    started=time.monotonic() if _started is None else _started
    source=bundle.source;s=bundle.snapshot
    PassageSnapshot.model_validate(s.model_dump(mode='json'))
    query=PassageQuery.model_validate(s.requested_query.model_dump(mode='json'))
    require(query.expected_source_snapshot==source.source_snapshot_id==s.source_snapshot_id,'Source/passage snapshot mismatch.')
    require(s.source_sha256==score.source_sha256 and s.options_sha256==digest(score.options.model_dump(mode='json')) and s.query_sha256==digest(query.model_dump(mode='json')),'Source/options/query identity mismatch.')
    require(s.passage_snapshot_id==digest(s.model_dump(mode='json',exclude={'passage_snapshot_id'})),'Passage identity mismatch.')
    require(s.component_versions==COMPONENT_VERSIONS and s.assumptions==ASSUMPTIONS,'Passage policy declarations changed.')
    expected_scope=resolve_source(score,query)
    require(s.resolved_scope==expected_scope,'Passage scope/occupancy/context was omitted or substituted.')
    included=set(expected_scope.primary_event_ids+expected_scope.context_event_ids)
    require(s.transposition_audit==[audit[e.id] for p in score.parts for e in p.events if e.id in included],'Incomplete/forged pitch and tie audit.')
    # Arithmetic/ownership validation is shared with M3, independently of producer records.
    validate_observations(score,s,occupancy_event_ids=expected_scope.occupancy_event_ids)
    events={e.id:e for p in score.parts for e in p.events}
    for o in s.observations:validate_ensemble(events,o)
    replay=PassageContext(source,query,expected_scope)
    execute_features(replay)
    require(s.effective_features==replay.effective,'Incorrect effective feature dependencies.')
    require(s.transposition_audit==[audit[eid] for eid in replay.included_ids],'Incomplete/forged pitch and tie audit.')
    require(s.observations==replay.observations,'Missing, extra or altered source-derived passage observations.')
    require(s.coverage==replay.coverages() and s.work_counts==dict(replay.work),'Incomplete or forged passage coverage/work enumeration.')
    if query.expression_policy is not None:
        from composerlint.expression import build
        require(s.expression_context==build(source,expected_scope,replay.observations),
                'Missing, extra or altered source expression notation/chronology.')
    else:
        require(s.expression_context is None,'Expression evidence requires explicit policy opt-in.')
    timings.update(validation_seconds=time.monotonic()-started,scoped_predicate_passes=2.0)
    return True


class PassageGraph(AdvancedGraph):
    def __init__(self,source,snapshot=None):
        self.raw={};self.items={};self.links={};self.parts={};self.measures={}
        score=source.score.model_dump(mode='json');self.data={'score':score}
        self.raw.update({'options:effective':score['options'],
            'metadata:analysis':{'execution_policy':PASSAGE_POLICY,'global_musical_report_constructed':False},
            'normalization:score':score['normalization_metadata'],
            'source:assumptions':{'values':score['assumptions']},'source:limitations':{'values':score['limitations']}})
        self.index_source(score)
        self.raw['source:prepared']={'source_sha256':score['source_sha256'],'source_snapshot_id':source.source_snapshot_id,
            'options_sha256':digest(score['options']),'part_refs':['part:'+p['id'] for p in score['parts']],
            'part_count':len(score['parts']),'lane_count':sum(len(p['voices']) for p in score['parts']),
            'event_count':sum(len(p['events']) for p in score['parts']),
            'measures_by_part':{p['id']:len(p['measures']) for p in score['parts']},
            'whole_source_preparation':True,'global_analysis_performed':False}
        self.snapshot=snapshot;self.extra_edges={};self.index_refs=[]
        if snapshot is not None:
            self.index_advanced(source.score,snapshot,{'source_snapshot_id':snapshot.source_snapshot_id,'passage_snapshot_id':snapshot.passage_snapshot_id})
            self.raw['advanced:overview']['work_counts']=snapshot.work_counts
            # Complete admitted source occupancy is part of every passage proof,
            # including instruments outside an explicit target focus.
            included=snapshot.resolved_scope.primary_event_ids+snapshot.resolved_scope.context_event_ids
            self.extra_edges['advanced:query'].update({'source:prepared',*('event:'+e for e in included),*('transpose:'+e for e in included)})
            if snapshot.expression_context is not None:
                expression=snapshot.expression_context
                marking_refs=[m.id for m in expression.markings]
                anchor_refs=[a.id for a in expression.chronology]
                self.raw['expression:overview']={'policy':expression.policy,
                    'notation_ref':'expression:notation','chronology_ref':'expression:chronology',
                    'coverage_ref':'expression:coverage','limitations':expression.limitations}
                self.raw['expression:notation']={'marking_refs':marking_refs}
                self.raw['expression:chronology']={'anchor_refs':anchor_refs}
                self.raw['expression:coverage']=expression.coverage
                self.extra_edges['expression:overview']={'advanced:query','expression:notation','expression:chronology','expression:coverage'}
                self.extra_edges['expression:notation']=set(marking_refs)|{'advanced:query'}
                self.extra_edges['expression:chronology']=set(anchor_refs)|{'advanced:query'}
                for marking in expression.markings:
                    self.raw[marking.id]=marking.model_dump(mode='json')
                    self.extra_edges[marking.id]={'advanced:query',*marking.pairing_refs,
                        *('transpose:'+eid for eid in marking.event_ids)}
                    for eid in marking.event_ids:
                        self.raw['transpose:'+eid]=source.audit[eid].model_dump(mode='json')
                for anchor in expression.chronology:
                    self.raw[anchor.id]=anchor.model_dump(mode='json')
                    self.extra_edges[anchor.id]={anchor.record_ref,'advanced:query'}
                self.raw['advanced:overview']['expression_ref']='expression:overview'
                self.index_refs.append('expression:overview')
        self.extra_edges['source:prepared']={'part:'+p['id'] for p in score['parts']}
        self.extra_edges['source:prepared'].update('voice:'+v['id'] for p in score['parts'] for v in p['voices'])

    def source_rows(self):
        refs=['source:prepared',*['part:'+k for k in self.parts],
              *[r for r in self.raw if r.startswith('voice:')],*['measure:'+k for k in self.measures]]
        rows=[]
        for ref in refs:
            self.materialize(ref);rows.append(AdvancedItem.model_validate(self.items[ref].model_dump()))
        return rows


def project(source,bundle,args,timings,*,_response_overhead=0,_graph=None,_delivery_binding=None):
    started=time.monotonic();s=bundle.snapshot if bundle else None
    if s and args.expected_passage_snapshot is not None and args.expected_passage_snapshot!=s.passage_snapshot_id:
        raise AdvancedFailure('stale_advanced_snapshot','Passage query/options/source changed.')
    page_query=args.query().model_copy(update={'expected_source_snapshot':source.source_snapshot_id})
    query_sha=digest(page_query.model_dump(mode='json'))
    cursor_binding={'source':source.source_snapshot_id,'passage':s.passage_snapshot_id if s else None,'query':query_sha,
                    'section':args.section,'record_ref':args.record_ref,'page_size':args.page_size,'max_bytes':args.max_bytes}
    if args.viewer_selection_ref is not None:cursor_binding['viewer_selection']=digest(args.viewer_selection_ref)
    if _delivery_binding is not None:cursor_binding['delivery_policy']=_delivery_binding
    binding=digest(cursor_binding)
    start=0
    if args.cursor:
        try:
            token=json.loads(base64.b64decode(args.cursor,altchars=b'-_',validate=True))
            if set(token)!={'binding','offset'} or token['binding']!=binding or type(token['offset']) is not int or token['offset']<=0:raise ValueError()
            start=token['offset']
        except (ValueError,TypeError,UnicodeError):raise AdvancedFailure('invalid_cursor','Cursor is not bound to this source/passage/section.') from None
    graph=_graph if _graph is not None else PassageGraph(source,s)
    try:rows=graph.advanced_rows(args) if s else graph.source_rows()
    except ReviewFailure as exc:raise AdvancedFailure(exc.code,exc.reason,exc.record_ref) from exc
    if start>len(rows) or start==len(rows) and start!=0:raise AdvancedFailure('invalid_cursor','Cursor outside available rows.')
    def page(end):
        cursor=None if end==len(rows) else base64.urlsafe_b64encode(canonical({'binding':binding,'offset':end}).encode()).decode()
        return PassagePage(analysis_scope='selected_passage' if s else 'source_preparation',
            source_sha256=source.score.source_sha256,source_snapshot_id=source.source_snapshot_id,
            options_sha256=digest(source.score.options.model_dump(mode='json')),query_sha256=query_sha,
            passage_snapshot_id=s.passage_snapshot_id if s else None,section=args.section,total_items=len(rows),start_index=start,
            returned_count=end-start,items=rows[start:end],next_cursor=cursor,section_exhausted=end==len(rows),
            evidence_complete_in_this_response=args.section=='evidence' and start==0 and end==len(rows),timings=dict(timings),
            **({'completeness_notice':'Source expression notation and a chronology of existing relationships. Retrieve complete supporting proof; no emotion detection, formal phrase analysis, global key or quality judgment.'} if args.expression_policy is not None else {}))
    end=start
    for n in range(start+1,min(start+args.page_size,len(rows))+1):
        if len(canonical(page(n).model_dump(mode='json')).encode())+_response_overhead>args.max_bytes:break
        end=n
    timings['projection_seconds']=time.monotonic()-started
    result=page(end)
    if end==start and start<len(rows) or len(canonical(result.model_dump(mode='json')).encode())+_response_overhead>args.max_bytes:
        raise AdvancedFailure('oversized_record','Complete indivisible passage record exceeds the unchanged byte budget.')
    return result


def run(args,*,_response_overhead=0):
    # Private transport accounting only; it never changes source or semantic validation.
    if type(_response_overhead) is not int or not 0<=_response_overhead<8192:
        raise ValueError('Invalid private response envelope size')
    timings={};source=prepare(args.path,args.options,timings)
    if args.expected_source_snapshot is not None and args.expected_source_snapshot!=source.source_snapshot_id:
        raise AdvancedFailure('stale_snapshot','Source/options/policy changed; prepare a new source snapshot.')
    bundle=None
    if args.section!='source':
        bundle=compute(source,args.query(),timings)
        validate(bundle,timings)
    result=project(source,bundle,args,timings,_response_overhead=_response_overhead);fresh(source)
    return result
