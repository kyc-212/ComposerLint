"""Source-preserving advanced orchestration, finite coverage and exact scope resolution."""
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from pathlib import Path
import hashlib
from composerlint.platform_support import read_score_bytes
from composerlint.models import rt
from composerlint.advanced_models import (AdvancedQuery,AdvancedSnapshot,AdvancedFailure,ResolvedScope,Span,
    SourceSupport,Observation,FeatureCoverage,CoverageUnit,FEATURES,POLICY)
from composerlint.review import digest,canonical
from composerlint.engine.advanced_context import audit_source

LIMITS={'source_bytes':10*1024*1024,'measures':2000,'parts':16,'lanes':32,'events':8192,'segments':16384,
    'pattern_windows':32768,'comparisons':50000,'pair_units':50000,'rows':50000,'payload_bytes':32*1024*1024,'deadline_seconds':25}
UNIT_DEFINITIONS={
    'notes':'Primary logical events, including rests and zero-duration inspection events.',
    'sonorities':'Positive-duration target occupancy cells, including silent and unknown cells.',
    'melody':'Adjacent pitched logical-attack opportunities per source lane; blocked transitions are skipped.',
    'patterns':'Contiguous 3–8-attack windows inside eligible runs, signature groups, and explicitly bounded comparison units.',
    'passages':'Explicit rest/ending/source-end boundary cues and one optional two-span comparison.',
    'ensemble':'Lane-pair occupancy cells and consecutive pair-cell transitions; solo pairs are not applicable.'}


def span(a,b):return Span(start=rt(a),end=rt(b))
def overlap(e,a,b):return a<e.end.fraction() and e.onset.fraction()<b if e.duration.fraction()>0 else a<=e.onset.fraction()<b

def merged(spans):
    out=[]
    for a,b in sorted(spans):
        if out and a<=out[-1][1]:out[-1]=(out[-1][0],max(out[-1][1],b))
        else:out.append((a,b))
    return out


def preflight(score):
    values={'parts':len(score.parts),'measures':sum(len(p.measures) for p in score.parts),
        'lanes':sum(len(p.voices) for p in score.parts),'events':sum(len(p.events) for p in score.parts),'segments':sum(len(p.source_segments) for p in score.parts)}
    for key,n in values.items():
        if n>LIMITS[key]:raise AdvancedFailure('analysis_limit_exceeded',f'{key}: {n} exceeds {LIMITS[key]}; use a smaller source. Existing review remains available.')


def resolve(score,query):
    parts={p.id:p for p in score.parts};voices={v.id:v for p in score.parts for v in p.voices}
    events={e.id:e for p in score.parts for e in p.events}
    allp=list(parts);allv=list(voices);contexts=[];ranges=[];comparison=[];target_filters=[];context_filters=[]
    wanted_parts=set(allp);wanted_voices=set(allv);selected=None
    def measure_range(s):
        if s.reference_part_id not in parts:raise AdvancedFailure('invalid_analysis_scope','Unknown reference part ID.')
        p=parts[s.reference_part_id]
        if s.end_measure>len(p.measures):raise AdvancedFailure('invalid_analysis_scope','Traversal measure outside reference part.')
        ps=set(s.part_ids or allp);vs=set(s.voice_ids or [v for v in allv if voices[v].part_id in ps])
        if not ps<=set(parts) or not vs<=set(voices) or any(voices[v].part_id not in ps for v in vs):raise AdvancedFailure('invalid_analysis_scope','Part/voice filters do not resolve together.')
        a,b=p.measures[s.start_measure-1].start.fraction(),p.measures[s.end_measure-1].end.fraction()
        n=query.analysis_options.neighbor_measures
        c,d=p.measures[max(0,s.start_measure-1-n)].start.fraction(),p.measures[min(len(p.measures)-1,s.end_measure-1+n)].end.fraction()
        return (a,b),(c,d),ps,vs
    if query.comparison:
        wanted_parts=set();wanted_voices=set()
        for s in (query.comparison.first,query.comparison.second):
            target,context,ps,vs=measure_range(s);ranges.append(target);contexts.append(context);wanted_parts|=ps;wanted_voices|=vs
            comparison.append([e.id for e in events.values() if e.voice_id in vs and overlap(e,*target)])
            target_filters.append((target,vs));context_filters.append((context,vs))
        if max(ranges[0][0],ranges[1][0])<min(ranges[0][1],ranges[1][1]):raise AdvancedFailure('invalid_analysis_scope','Comparison ranges must not overlap in time.')
    elif query.scope.kind=='whole_score':ranges=[(Fraction(),score.duration.fraction())];contexts=ranges[:]
    elif query.scope.kind=='measure_range':
        target,context,wanted_parts,wanted_voices=measure_range(query.scope);ranges=[target];contexts=[context]
    else:
        selected=set(query.scope.event_ids)
        if not selected<=set(events):raise AdvancedFailure('invalid_analysis_scope','Unknown source event ID.')
        ranges=merged([(events[e].onset.fraction(),events[e].end.fraction()) for e in query.scope.event_ids]);contexts=ranges[:]
    ranges=merged(ranges);contexts=merged(contexts)
    primary=[e.id for e in events.values() if e.voice_id in wanted_voices and (e.id in selected if selected is not None else any(e.voice_id in vs and overlap(e,*r) for r,vs in target_filters) if target_filters else any(overlap(e,a,b) for a,b in ranges))]
    included=[e.id for e in events.values() if e.voice_id in wanted_voices and (any(e.voice_id in vs and overlap(e,*r) for r,vs in context_filters) if context_filters else any(overlap(e,a,b) for a,b in contexts))]
    # Complete neighboring logical attacks are external support, including at exact query edges.
    for vid in wanted_voices:
        ordered=sorted([e for e in events.values() if e.voice_id==vid and e.duration.fraction()>0],key=lambda e:(e.onset.fraction(),e.id))
        for i,e in enumerate(ordered):
            if e.id in primary:
                for k in (i-1,i+1):
                    if 0<=k<len(ordered) and ordered[k].id not in included:included.append(ordered[k].id)
    for role in query.analysis_options.declared_roles:
        if role.part_id not in parts or role.end_measure>len(parts[role.part_id].measures) or role.voice_id is not None and (role.voice_id not in voices or voices[role.voice_id].part_id!=role.part_id):raise AdvancedFailure('invalid_analysis_scope','Declared role has unknown source ownership/range.')
    return ResolvedScope(target_spans=[span(a,b) for a,b in ranges],context_spans=[span(a,b) for a,b in contexts],
        part_ids=[p for p in allp if p in wanted_parts],voice_ids=[v for v in allv if v in wanted_voices],primary_event_ids=primary,
        context_event_ids=[e for e in events if e in included and e not in primary],comparison_event_ids=comparison,selected_subset=selected is not None)


class Context:
    def __init__(self,report,query,audit,resolved,effective):
        self.report,self.score,self.query,self.audit,self.resolved,self.effective=report,report.score,query,audit,resolved,effective
        self.events={e.id:e for p in self.score.parts for e in p.events};self.parts={p.id:p for p in self.score.parts}
        self.voices={v.id:v for p in self.score.parts for v in p.voices};self.segments={s.id:s for p in self.score.parts for s in p.source_segments}
        self.primary=set(resolved.primary_event_ids);self.included_ids=[e for e in self.events if e in self.primary or e in resolved.context_event_ids]
        self.observations=[];self.units=defaultdict(list);self.work=defaultdict(int);self.ids=set();self.runs={};self.windows=[];self.sonorities=[]

    def budget(self,key,n=1):
        self.work[key]+=n
        if self.work[key]>LIMITS[key]:raise AdvancedFailure('analysis_limit_exceeded',f'{key} exceeded {LIMITS[key]}; request fewer features, lanes, or a narrower analytical range.')

    def support(self,eid):
        e=self.events[eid];v=self.voices[e.voice_id];segs=[self.segments[s] for s in e.source_segment_ids]
        return SourceSupport(event_id=eid,part_id=v.part_id,voice_id=v.id,staff=v.source_staff,
            source_segment_ids=e.source_segment_ids,measure_ids=list(dict.fromkeys(s.location.measure_id for s in segs)),role='primary' if eid in self.primary else 'context')

    def emit(self,feature,domain,a,b,ids,fact,premises=(),limitations=()):
        ids=set(ids);support=[self.support(e) for e in self.events if e in ids]
        ob=Observation(id='pending',feature=feature,pitch_domain=domain,span=span(a,b),support=support,premise_ids=list(dict.fromkeys(premises)),limitations=list(limitations),fact=fact)
        ob.id='adv:'+digest({'source':self.score.source_sha256,**ob.model_dump(exclude={'id'},mode='json')})
        if ob.id in self.ids:return next(o for o in self.observations if o.id==ob.id)
        self.budget('rows');self.observations.append(ob);self.ids.add(ob.id)
        return ob

    def unit(self,feature,key,ids=(),eligible=True,checked=None,matched=False,unknown=False,reasons=()):
        checked=eligible if checked is None else checked
        def conv(x):
            if isinstance(x,Fraction):return rt(x).model_dump()
            if isinstance(x,(list,tuple)):return [conv(y) for y in x]
            return x
        self.budget('rows')
        u=CoverageUnit(id='unit:'+digest([feature,conv(key)]),eligible=eligible,checked=checked,matched=matched,unknown=unknown,reasons=list(reasons),event_ids=list(dict.fromkeys(ids)))
        self.units[feature].append(u)
        return u

    def cells(self,voice_ids=None):
        vids=set(voice_ids or self.resolved.voice_ids)
        source=[self.events[e] for e in self.resolved.primary_event_ids if self.events[e].voice_id in vids and self.events[e].duration.fraction()>0]
        for target in self.resolved.target_spans:
            a,b=target.start.fraction(),target.end.fraction()
            cuts=sorted({a,b}|{max(a,e.onset.fraction()) for e in source if overlap(e,a,b)}|{min(b,e.end.fraction()) for e in source if overlap(e,a,b)})
            for x,y in zip(cuts,cuts[1:]):
                if x<y:yield x,y,[e for e in source if e.onset.fraction()<=x and e.end.fraction()>=y]

    def pitch(self,e,domain=None):
        domain=domain or self.query.analysis_options.melodic_pitch_domain
        if e.kind!='pitched':return None
        if domain=='sounding':return e.sounding_pitch if self.audit[e.id].status=='verified' else None
        return e.written_pitch

    def coverages(self):
        entries=[]
        for f in FEATURES:
            units=self.units[f];checked=sum(u.checked for u in units)
            rs=list(dict.fromkeys(r for u in units for r in u.reasons))
            status='not_requested' if f not in self.effective else 'not_applicable' if not units else 'complete' if checked==len(units) and not any(u.unknown for u in units) else 'partial'
            entries.append(FeatureCoverage(feature=f,unit_definition=UNIT_DEFINITIONS[f],status=status,candidate=len(units),eligible=sum(u.eligible for u in units),checked=checked,skipped=len(units)-checked,
                matched=sum(u.matched for u in units),unknown=sum(u.unknown for u in units),reasons=rs or (['solo_no_lane_pairs'] if f=='ensemble' and len(self.resolved.voice_ids)<2 and status=='not_applicable' else []),units=units))
        return entries


@dataclass(frozen=True)
class AdvancedAnalysis:
    report: object
    snapshot: AdvancedSnapshot
    def canonical_json(self):return canonical(self.snapshot.model_dump(mode='json'))


def execute_features(ctx):
    """The same descriptive predicates serve legacy and source-bound contexts."""
    from composerlint.engine.sonorities import execute_notes,execute_sonorities
    from composerlint.engine.melody import execute_melody,execute_patterns,execute_passages
    from composerlint.engine.ensemble import execute_ensemble
    execute_notes(ctx)
    if 'sonorities' in ctx.effective:ctx.sonorities=execute_sonorities(ctx)
    if 'melody' in ctx.effective:
        execute_melody(ctx)
        if 'patterns' in ctx.effective:execute_patterns(ctx)
        if 'passages' in ctx.effective:execute_passages(ctx)
    if 'ensemble' in ctx.effective:execute_ensemble(ctx)


def compute(path,options,query,*,_base_report=None):
    from composerlint.engine.parser import load_score,_options
    from composerlint.engine.source_context import read_source_context
    from composerlint.service import analyze_normalized
    opts=_options(options);query=AdvancedQuery.model_validate(query.model_dump(mode='json') if isinstance(query,AdvancedQuery) else query or {})
    local=Path(path).expanduser()
    if local.is_file() and local.stat().st_size>LIMITS['source_bytes']:raise AdvancedFailure('analysis_limit_exceeded','Input exceeds 10 MiB advanced limit.')
    try:
        if local.is_file() and local.suffix.lower() in ('.xml','.musicxml') and b'<!ENTITY' in read_score_bytes(local).upper():raise AdvancedFailure('unsupported_analysis_case','XML entity declarations are not supported.')
    except OSError as exc:
        raise AdvancedFailure('parse_failed','Source is unreadable or unsafe.') from exc
    score=load_score(path,opts);preflight(score)
    data=read_score_bytes(local);audit=audit_source(score,data)
    source_context=read_source_context(path,score)
    if _base_report is None:
        report=analyze_normalized(score,source_context)
    else:
        from composerlint.analysis_models import AnalysisReport
        from composerlint.report_reuse import code_identity
        try:
            report=AnalysisReport.model_validate(_base_report['report'])
            valid=(_base_report['code']==code_identity() and digest(report.model_dump(mode='json'))==_base_report['snapshot']
                   and report.score==score and report.key_analysis.source_context==source_context)
        except (ValueError,KeyError,TypeError):valid=False
        if not valid:raise AdvancedFailure('unavailable_evidence','Reused base report failed full validation or fresh source/options/code ownership.')
    base=digest(report.model_dump(mode='json'))
    if query.expected_base_snapshot is not None and query.expected_base_snapshot!=base:raise AdvancedFailure('stale_snapshot','Base source/options changed; retrieve a fresh overview and resolve source IDs again.')
    query=query.model_copy(update={'expected_base_snapshot':base},deep=True)
    resolved=resolve(score,query)
    effective=set(query.features)
    if query.analysis_options.include_legacy_harmonic_context:effective.add('sonorities')
    if effective:effective.add('notes')
    if effective & {'patterns','passages','ensemble'}:effective.add('melody')
    if 'passages' in effective:effective.add('patterns')
    if 'ensemble' in effective:effective|={'sonorities','patterns'}
    effective=[f for f in FEATURES if f in effective]
    ctx=Context(report,query,audit,resolved,effective)
    execute_features(ctx)
    if query.analysis_options.include_legacy_harmonic_context:
        from composerlint.advanced_models import LegacyContextFact
        for h in report.harmony:
            a,b=h.scope.start,h.scope.end
            if a is None or not any(a.fraction()<s.end.fraction() and s.start.fraction()<b.fraction() for s in resolved.target_spans):continue
            ids=h.scope.references.event_ids
            blocked=any(audit[e].status=='unknown' for e in ids)
            reference_part=score.parts[0] if score.parts else None
            passage_scope=query.comparison is not None or query.scope.kind=='measure_range' and query.scope.end_measure>query.scope.start_measure or query.scope.kind=='whole_score' and reference_part is not None and len(reference_part.measures)>1
            explicit_reference=opts.key_override is not None
            isolated_without_reference=not passage_scope and not explicit_reference
            ctx.emit('sonorities','sounding',a.fraction(),b.fraction(),ids,LegacyContextFact(record_ref='harmony:'+h.id,usable_as_advanced_interpretation=not blocked and not isolated_without_reference,limitations=['inherited_legacy_global_reference_not_local_key']+(['audit_blocked_pitch'] if blocked else [])+(['isolated_chord_has_no_established_key_function'] if isolated_without_reference else [])))
    component_versions={f:'1' for f in FEATURES}
    snapshot=AdvancedSnapshot(source_sha256=score.source_sha256,base_snapshot_id=base,options_sha256=digest(opts.model_dump(mode='json')),query_sha256=digest(query.model_dump(mode='json')),
        advanced_snapshot_id='0'*64,component_versions=component_versions,requested_query=query,effective_features=effective,resolved_scope=resolved,
        observations=ctx.observations,coverage=ctx.coverages(),transposition_audit=list(audit.values()),
        assumptions=['literal_source_order_no_repeat_expansion','concert_default_without_transpose_no_instrument_name_guess','deterministic_patterns_not_artistic_intent','boundary_candidates_not_formal_segmentation']+(['midi_spelling_lanes_and_quantization_inferred'] if score.format=='midi' else []))
    snapshot.advanced_snapshot_id=digest(snapshot.model_dump(mode='json',exclude={'advanced_snapshot_id'}))
    if len(canonical(snapshot.model_dump(mode='json')).encode())>LIMITS['payload_bytes']:raise AdvancedFailure('analysis_limit_exceeded','Advanced payload exceeds 32 MiB; narrow the query.')
    if hashlib.sha256(read_score_bytes(local)).hexdigest()!=score.source_sha256:raise AdvancedFailure('stale_snapshot','Source changed during analysis; no result returned.')
    bundle=AdvancedAnalysis(report,snapshot)
    from composerlint.advanced_review import validate_analysis
    validate_analysis(bundle)
    return bundle
