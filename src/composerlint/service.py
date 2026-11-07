"""Shared M2/M3 library orchestration with owned deterministic snapshots."""
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from pydantic import TypeAdapter, ValidationError
from composerlint.engine.parser import load_score
from composerlint.engine.source_context import read_source_context
from composerlint.engine.key_analysis import select_key
from composerlint.errors import ComposerLintError
from composerlint.analysis_models import (AnalysisRequest,AnalysisReport,AnalysisMetadata,Versions,
    RULE_IDS,UNITS,ComponentState,Coverage,CoverageExclusion,AnalysisSummary,Assumption,Limitation,
    Diagnostic,Confidence,SourceKeyEvidence,CoverageEvidence,EstimationEvidence,KeyReferenceEvidence,
    global_scope,record_sort,reasons)


@dataclass(frozen=True)
class RuleSpec:
    id: str
    component: str
    milestone: str
    implementation_state: str
    requires_key: bool
    requires_order: bool
    requires_range: bool
    unit: str
    description: str


CATALOG=tuple(RuleSpec(r,r,'M2' if i<3 else ('M4' if i==11 else 'M3'),
    'implemented',r in ('harmonic_function','unresolved_seventh','cadence_detection'),
    r=='voice_crossing',r=='range_issue',UNITS[i],r.replace('_',' ')) for i,r in enumerate(RULE_IDS))


def _coverage(score,key):
    entries=[]
    for i,spec in enumerate(CATALOG,1):
        codes=[]; exclusions=[]
        if spec.implementation_state=='not_implemented':
            c=e=s=None; k=0
            codes=['not_implemented']
            if spec.requires_key and key.selected_key is None: codes.append('missing_key')
            if spec.requires_order and score.options.voice_order is None: codes.append('missing_voice_order')
            if spec.requires_range and not score.options.ranges: codes.append('missing_range')
            exclusions=[CoverageExclusion(unit_ids=None,count=None,reason_code='not_implemented',evidence=[])]
        else:
            c=1; e=k=1; s=0; codes=['executed']
            if spec.id=='global_key_estimation' and key.estimator_state=='skipped':
                e=k=0; s=1; codes=[key.estimator_reason_code]
                exclusions=[CoverageExclusion(unit_ids=['score'],count=1,reason_code=key.estimator_reason_code,evidence=[])]
        entries.append(Coverage(id=f'cv{i:04d}',rule_id=spec.id,implementation_state=spec.implementation_state,
            status='complete' if k else 'skipped',unit=spec.unit,scope=global_scope(),candidate_count=c,eligible_count=e,checked_count=k,skipped_count=s,flagged_count=0,reason_codes=reasons(codes),exclusions=exclusions))
    return entries


def _estimation_evidence(key):
    return EstimationEvidence(kind='estimation',candidate_ids=[c.id for c in key.candidates],statistics_reference='key_analysis.statistics',histogram_reference='key_analysis.statistics.histogram',reason_codes=key.reason_codes)


def _build_analysis_fields(score,source_context):
    """Build private report fields; callers must fully validate before use."""
    if source_context.source_sha256!=score.source_sha256 or source_context.format!=score.format:
        raise ComposerLintError('parse_failed','source_context_mismatch')
    # Revalidation also prevents callers using model_construct or unvalidated list mutation.
    from composerlint.models import NormalizedScore
    from composerlint.analysis_models import SourceContext
    owned=NormalizedScore.model_validate(score.model_dump(mode='json'))
    context=SourceContext.model_validate(source_context.model_dump(mode='json'))
    key=select_key(owned,context); coverage=_coverage(owned,key)
    from composerlint.engine.voice_leading import execute
    musical=execute(owned)
    from composerlint.engine.harmony import execute as execute_harmony
    from composerlint.engine.harmonic_checks import execute_sevenths,execute_cadences
    harmonic,slices,runs,stages=execute_harmony(owned,key)
    for result in (harmonic,execute_sevenths(owned,key,runs),execute_cadences(owned,key,runs)):
        musical.ledgers.update(result.ledgers);musical.diagnostics.extend(result.diagnostics)
        musical.limitations.extend(result.limitations);musical.harmony.extend(result.harmony);musical.cadences.extend(result.cadences)
    for rule,ledger in musical.ledgers.items():coverage[RULE_IDS.index(rule)]=ledger.coverage()
    coverage[3]=coverage[3].model_copy(update={'stages':stages})
    assumptions=[]
    for reason in ('global_key_only','duration_weighted_logical_events','literal_source_key_scope',
                   'classical_target_requested' if owned.options.target_style else 'classical_target_provisional'):
        assumptions.append(Assumption(id='pending',policy_id='m2_global_key_v1',reason_code=reason,scope=global_scope(),evidence=[],explanation={
            'global_key_only':'One global reference; local modulation and phrases are not inferred.',
            'duration_weighted_logical_events':'Exact eligible logical-event duration; independent unison voices each contribute weight.',
            'literal_source_key_scope':'All source declarations, including late and trailing records, constrain stable metadata selection.',
            'classical_target_requested':'Caller retained the one supported limited Classical profile; no comparison against other styles.',
            'classical_target_provisional':'The one supported Classical profile is a provisional convention; no comparison against other styles.'}[reason]))
    if key.selected_key is not None:
        assumptions.append(Assumption(id='pending',policy_id='m2_global_key_v1',reason_code='selected_reference_not_fit',scope=global_scope(),evidence=[KeyReferenceEvidence(kind='key_reference',key_reference_id='kref0001')],explanation='Selecting a reference is not evidence that the score fits it.'))
    assumptions.append(Assumption(id='pending',policy_id='m3_deterministic_music_v1',reason_code='m3_observed_pitch_relation',scope=global_scope(),evidence=[],explanation='Exact concert observations use source identities and caller configuration; tonal exceptions and compositional intent are not inferred.'))
    for reason in ('m3_surface_harmony','m3_reference_relative_interpretation','m3_conventional_seventh_policy','m3_ending_candidate_policy'):
        assumptions.append(Assumption(id='pending',policy_id='m3_deterministic_music_v1',reason_code=reason,scope=global_scope(),evidence=[],explanation='Bounded surface analysis preserves unknown cases; no local key, phrase structure, hidden tones, style score or ornament intent is inferred.'))
    assumptions.sort(key=record_sort)
    assumptions=[a.model_copy(update={'id':f'a{i:06d}'}) for i,a in enumerate(assumptions,1)]
    limitations=[]
    def limit(reason,evidence,rule,explanation):
        limitations.append(Limitation(id='pending',reason_code=reason,scope=global_scope(),evidence=evidence,explanation=explanation,related_rule_ids=[rule]))
    limit('global_key_only',[], 'key_selection','Global selection does not establish local keys or modulation.')
    for reason in key.reason_codes:
        if reason in ('user_key_override','explicit_source_key','estimated_global_key'): continue
        observed=[o.id for o in context.key_observations if reason in o.reason_codes]
        if reason in ('conflicting_key_declarations','late_key_declaration'): observed=[o.id for o in context.key_observations]
        ev=[SourceKeyEvidence(kind='source_key',observation_ids=observed)] if observed else [_estimation_evidence(key)]
        rule='source_key_context' if observed else 'key_selection'
        limit(reason,ev,rule,reason.replace('_',' ')+'. This describes analysis availability or evidence, not a musical fault.')
    if owned.format=='midi': limit('inferred_midi_spelling',[], 'key_selection','MIDI concert classes are retained; notation spelling is inferred.')
    for c in coverage:
        if c.implementation_state=='not_implemented': limit('not_implemented',[CoverageEvidence(kind='coverage',coverage_ids=[c.id])],c.rule_id,f'{c.rule_id} awaits '+('M4.' if c.rule_id=='classical_style_evidence' else 'M3.'))
    for c in coverage[3:11]:
        for reason in c.reason_codes:
            if reason not in ('executed',):
                limit(reason,[CoverageEvidence(kind='coverage',coverage_ids=[c.id])],c.rule_id,'This evaluation was withheld for the stated prerequisite or context; it is not a negative musical finding.')
    limitations.extend(musical.limitations)
    limitations.sort(key=record_sort)
    limitations=[l.model_copy(update={'id':f'l{i:06d}'}) for i,l in enumerate(limitations,1)]
    key=key.model_copy(update={'limitation_ids':[l.id for l in limitations if l.reason_code!='not_implemented' and any(r in RULE_IDS[:3] for r in l.related_rule_ids)]})
    diagnostics=list(musical.diagnostics)
    for l in limitations:
        if not l.evidence: continue
        interpreted=any(e.kind=='seventh_resolution' for e in l.evidence)
        level=('low' if owned.format=='midi' else 'medium') if interpreted else 'high'
        diagnostics.append(Diagnostic(id='pending',rule_id=l.related_rule_ids[0],category='analysis_limitation',severity='info',confidence=Confidence(level=level,reason_code=l.reason_code,explanation='Bounded interpretation confidence; no score-quality claim.' if interpreted else 'Certain about the recorded limitation; no score-quality claim.'),scope=l.scope.model_copy(deep=True),evidence=[e.model_copy(deep=True) for e in l.evidence],explanation=l.explanation,assumption_ids=[]))
    music_assumptions=[a.id for a in assumptions if a.policy_id=='m3_deterministic_music_v1']
    diagnostics=[d.model_copy(update={'assumption_ids':music_assumptions}) if d.category!='analysis_limitation' else d for d in diagnostics]
    diagnostics.sort(key=record_sort)
    diagnostics=[d.model_copy(update={'id':f'd{i:06d}'}) for i,d in enumerate(diagnostics,1)]
    metadata=AnalysisMetadata(policy_id='m2_global_key_v1',musical_policy_id='m3_deterministic_music_v1',style_policy_id='m4_classical_compatibility_v1',versions=Versions(**{n:version(n) for n in ('composerlint','music21','pydantic')}),estimator='music21.analysis.discrete.KrumhanslSchmuckler',components=[ComponentState(component_id=s.id,implementation_state=s.implementation_state) for s in CATALOG])
    from types import SimpleNamespace
    from composerlint.styles.classical import extract
    style_analysis,style_evidence=extract(SimpleNamespace(score=owned,key_analysis=key,harmony=musical.harmony,cadences=musical.cadences,diagnostics=diagnostics,limitations=limitations,coverage=coverage))
    coverage[-1]=coverage[-1].model_copy(update={'flagged_count':int(style_analysis.conflicting_observation_count>0)})
    return dict(score=owned,options=owned.options.model_copy(deep=True),analysis_metadata=metadata,key_analysis=key,assumptions=assumptions,limitations=limitations,harmony=musical.harmony,cadences=musical.cadences,style_evidence=style_evidence,style_analysis=style_analysis,diagnostics=diagnostics,coverage=coverage,summary=AnalysisSummary.derive(diagnostics,coverage))


def analyze_normalized(score,source_context):
    """Own an independent snapshot; a matching explicit context is mandatory."""
    return AnalysisReport(**_build_analysis_fields(score,source_context))


def analyze_score(path: str | Path,options=None):
    score=load_score(path,options)
    context=read_source_context(path,score)
    return analyze_normalized(score,context)


def analyze_request(data):
    # Preserve loader domain codes before Pydantic wraps ValueError subclasses.
    if isinstance(data, dict) and 'options' in data:
        from composerlint.engine.parser import _options
        data = {**data, 'options': _options(data['options'])}
    try: request=AnalysisRequest.model_validate(data)
    except ValidationError as exc: raise ComposerLintError('invalid_options',str(exc)) from exc
    return analyze_score(request.path,request.options)


def inspect_measure(report,measure_index,part_id=None):
    """Filter an owned report snapshot; no source read, normalization or re-estimation."""
    if isinstance(measure_index,bool) or not isinstance(measure_index,int) or measure_index<=0:
        raise ComposerLintError('invalid_options','measure_index must be a strict positive traversal integer')
    return _inspect_validated_measure(_owned_report(report),measure_index,part_id)


def _inspect_validated_measure(report,measure_index,part_id=None):
    """Project an internally owned, validated report; never return an unvalidated view."""
    from composerlint.analysis_models import (MeasureInspection,InspectionPart,InspectionWindow,
        WindowIntersection,ReferenceSet,score_indexes,point_in_view)
    from composerlint.models import rt
    if isinstance(measure_index,bool) or not isinstance(measure_index,int) or measure_index<=0:
        raise ComposerLintError('invalid_options','measure_index must be a strict positive traversal integer')
    parts=report.score.parts
    if part_id is not None and (not isinstance(part_id,str) or part_id not in {p.id for p in parts}):
        raise ComposerLintError('unknown_voice','unknown reference part ID')
    if not parts: raise ComposerLintError('measure_not_found','score has no reference part/measure')
    reference=parts[0] if part_id is None else next(p for p in parts if p.id==part_id)
    if measure_index>len(reference.measures): raise ComposerLintError('measure_not_found','reference part has no such traversal measure')
    measure=reference.measures[measure_index-1]
    window=InspectionWindow(start=measure.start.model_copy(deep=True),end=measure.end.model_copy(deep=True))
    a,b=window.start.fraction(),window.end.fraction()
    ix=score_indexes(report.score)
    def in_scope(scope):
        if scope.kind=='global': return True
        x,y=scope.start.fraction(),scope.end.fraction()
        if scope.kind=='span': return a<b and a<y and x<b
        segs=[ix['source_segment_ids'][i] for i in scope.references.source_segment_ids]
        if segs: return any(point_in_view(s,ix['measure_ids'][s.location.measure_id],reference.id,measure,window) for s in segs)
        return a<=x<b if a<b else x==a
    diagnostics=[d for d in report.diagnostics if in_scope(d.scope)]
    harmony=[h for h in report.harmony if in_scope(h.scope)]
    cadences=[c for c in report.cadences if in_scope(c.scope)]
    styles=[s for s in report.style_evidence if in_scope(s.scope)]
    primary_harmony_ids=[h.id for h in harmony]
    def all_evidence(record):
        yield from getattr(record,'evidence',[])
        for ex in getattr(record,'exclusions',[]):yield from ex.evidence
        for stage in getattr(record,'stages',[]):
            for ex in stage.exclusions:yield from ex.evidence
    def hreferences(ev):
        return [i for name in ('harmony_ids','source_harmony_ids','target_harmony_ids','next_harmony_ids') for i in getattr(ev,name,[])]
    needed={h.id for h in harmony}
    records=[*diagnostics,*cadences,*styles,*report.limitations,*report.coverage]
    while True:
        before=len(needed)
        for record in records+[h for h in report.harmony if h.id in needed]:
            needed.update(getattr(record,'harmony_ids',[]))
            for ev in all_evidence(record):needed.update(hreferences(ev))
        if len(needed)==before:break
    harmony=[h for h in report.harmony if h.id in needed]
    # Retain globally required assumptions/limitations/coverage without changing their scope/status.
    primary={p.id:set() for p in parts}; selected=set(); required_measures={measure.id}; required_regions=set(); required_groups=set()
    for p in parts:
        for e in p.events:
            yes=a<b and a<e.end.fraction() and e.onset.fraction()<b if e.participates_in_timeline else any(point_in_view(ix['source_segment_ids'][sid],ix['measure_ids'][ix['source_segment_ids'][sid].location.measure_id],reference.id,measure,window) for sid in e.source_segment_ids)
            if yes: primary[p.id].add(e.id);selected.add(e.id)
        for m in p.measures:
            if a<b and m.start.fraction()<b and m.end.fraction()>a: required_measures.add(m.id)
        for v in p.voices:
            for r in v.regions:
                if a<b and r.start.fraction()<b and r.end.fraction()>a: required_regions.add(r.id)
    for d in [*diagnostics,*harmony,*cadences,*styles,*report.limitations,*report.coverage]:
        selected.update(d.scope.references.event_ids);required_regions.update(d.scope.references.region_ids)
        required_groups.update(d.scope.references.chord_group_ids);required_measures.update(d.scope.references.measure_ids)
        for sid in d.scope.references.source_segment_ids: selected.add(ix['source_segment_ids'][sid].event_id)
        for ev in all_evidence(d):
            if hasattr(ev,'references'):
                selected.update(ev.references.event_ids);required_regions.update(ev.references.region_ids);required_groups.update(ev.references.chord_group_ids);required_measures.update(ev.references.measure_ids)
                for sid in ev.references.source_segment_ids: selected.add(ix['source_segment_ids'][sid].event_id)
    # Fixed-point closure retains complete chord/region references, including out-of-window support.
    while True:
        before=(len(selected),len(required_groups))
        for rid in required_regions: selected.update(ix['region_ids'][rid].source_occupancy_event_ids)
        for eid in list(selected): required_groups.update(ix['event_ids'][eid].chord_group_ids)
        for gid in required_groups: selected.update(ix['chord_group_ids'][gid].member_event_ids)
        if before==(len(selected),len(required_groups)):break
    view_parts=[]
    for p in parts:
        events=[e for e in p.events if e.id in selected];sids={sid for e in events for sid in e.source_segment_ids}
        segments=[s for s in p.source_segments if s.id in sids]
        mids=required_measures|{s.location.measure_id for s in segments}
        included=[]
        for s in segments:
            x=s.normalized_onset.fraction();y=x+s.normalized_duration.fraction()
            if (a<b and a<y and x<b if x<y else point_in_view(s,ix['measure_ids'][s.location.measure_id],reference.id,measure,window)): included.append(s.id)
        voices=[v.model_copy(update={'regions':[r.model_copy(deep=True) for r in v.regions if r.id in required_regions]},deep=True) for v in p.voices]
        view_parts.append(InspectionPart(id=p.id,ordinal=p.ordinal,source_id=p.source_id,source_label=p.source_label,instrument=p.instrument,transposition=p.transposition.model_copy(deep=True),voices=voices,measures=[m.model_copy(deep=True) for m in p.measures if m.id in mids],events=[e.model_copy(deep=True) for e in events],source_segments=[s.model_copy(deep=True) for s in segments],chord_groups=[g.model_copy(deep=True) for g in p.chord_groups if g.id in required_groups],primary_event_ids=[e.id for e in events if e.id in primary[p.id]],support_event_ids=[e.id for e in events if e.id not in primary[p.id]],included_source_segment_ids=included,window_intersections=[WindowIntersection(region_id=r.id,start=rt(max(a,r.start.fraction())),end=rt(min(b,r.end.fraction()))) for v in voices for r in v.regions if max(a,r.start.fraction())<min(b,r.end.fraction())]))
    class Local:
        def __init__(self,parts):self.parts=parts
    local=score_indexes(Local(view_parts))
    context=ReferenceSet(**{name:[i for i in ix[name] if i not in local[name]] for name in ReferenceSet.model_fields})
    # Validate every external ID against the parent snapshot before constructing standalone labels.
    from composerlint.analysis_models import validate_references
    validate_references(context,ix)
    from composerlint.analysis_models import StyleSourceReferences
    style_context=StyleSourceReferences(harmony_ids=[h.id for h in report.harmony if h not in harmony],cadence_ids=[c.id for c in report.cadences if c not in cadences],diagnostic_ids=[d.id for d in report.diagnostics if d not in diagnostics]) if report.style_analysis else StyleSourceReferences()
    return MeasureInspection(source_sha256=report.score.source_sha256,reference_part_id=reference.id,reference_measure=measure.model_copy(deep=True),window=window,parts=view_parts,key_analysis=report.key_analysis.model_copy(deep=True),analysis_metadata=report.analysis_metadata.model_copy(deep=True),assumptions=[a.model_copy(deep=True) for a in report.assumptions],limitations=[l.model_copy(deep=True) for l in report.limitations],coverage=[c.model_copy(deep=True) for c in report.coverage],diagnostics=[d.model_copy(deep=True) for d in diagnostics],harmony=[h.model_copy(deep=True) for h in harmony],primary_harmony_ids=primary_harmony_ids,support_harmony_ids=[h.id for h in harmony if h.id not in primary_harmony_ids],cadences=[c.model_copy(deep=True) for c in cadences],style_evidence=[s.model_copy(deep=True) for s in styles],style_analysis=report.style_analysis.model_copy(deep=True) if report.style_analysis else None,style_context_source_references=style_context,context_reference_ids=context)



def _owned_report(source):
    if not isinstance(source,AnalysisReport):
        raise ComposerLintError('invalid_options','source must be a path or completed AnalysisReport')
    return AnalysisReport.model_validate(source.model_dump(mode='json'))


def _style_report(source,options):
    if isinstance(source,AnalysisReport):
        if options is not None:raise ComposerLintError('invalid_options','options must be absent for a completed report')
        report=_owned_report(source)
    elif isinstance(source,(str,Path)):report=analyze_score(source,options)
    else:raise ComposerLintError('invalid_options','source must be a path or completed AnalysisReport')
    if report.analysis_metadata.style_policy_id is None or report.style_analysis is None:
        raise ComposerLintError('invalid_options','style analysis unavailable in this snapshot')
    return report


def detect_style(source,options=None):
    """Evaluate only the one supported Classical profile; never compare styles.

    A path analyzes once. A completed current report reads its stored, owned global
    result after validation, without source access or musical execution.
    """
    return _style_report(source,options).style_analysis.model_copy(deep=True)


def get_style_evidence(source,options=None,rule_id=None):
    """Return the frozen definitions and source-linked observations with global context."""
    from composerlint.analysis_models import STYLE_RULE_IDS,StyleEvidenceView
    from composerlint.styles.classical import get_profile
    if rule_id is not None and (not isinstance(rule_id,str) or rule_id not in STYLE_RULE_IDS):
        raise ComposerLintError('invalid_options','unknown Classical profile rule ID')
    report=_style_report(source,options);profile=get_profile()
    selected=[rule_id] if rule_id else list(STYLE_RULE_IDS)
    observations=[o.model_copy(deep=True) for o in report.style_evidence if o.rule_id in selected]
    closure=_style_closure(report,observations)
    return StyleEvidenceView(profile=profile,style_analysis=report.style_analysis.model_copy(deep=True),
      definitions=[d.model_copy(deep=True) for d in profile.rules if d.rule_id in selected],
      applicability=[a.model_copy(deep=True) for a in report.style_analysis.applicability if a.rule_id in selected],
      observations=observations,source_sha256=report.score.source_sha256,options=report.options.model_copy(deep=True),
      key_analysis=report.key_analysis.model_copy(deep=True),analysis_metadata=report.analysis_metadata.model_copy(deep=True),
      coverage=[c.model_copy(deep=True) for c in report.coverage],**closure)


def _style_closure(report,observations):
    """Existing source records only, closed over located references; global IDs labeled."""
    from composerlint.analysis_models import ReferenceSet,InspectionPart,score_indexes,StyleSourceReferences
    ix=score_indexes(report.score)
    catalogs={name:getattr(report,field) for name,field in (('harmony_ids','harmony'),('cadence_ids','cadences'),('diagnostic_ids','diagnostics'),('limitation_ids','limitations'))}
    needed={n:{i for o in observations for i in getattr(o.source_references,n)} for n in catalogs}
    selected=set();regions=set();groups=set();measures=set();assumptions=set()
    def refs(r):
        selected.update(r.event_ids);regions.update(r.region_ids);groups.update(r.chord_group_ids);measures.update(r.measure_ids)
        selected.update(ix['source_segment_ids'][i].event_id for i in r.source_segment_ids)
    def evidence(e):
        if hasattr(e,'references'):refs(e.references)
        for n in ('harmony_ids','source_harmony_ids','target_harmony_ids','next_harmony_ids'):
            needed['harmony_ids'].update(getattr(e,n,[]))
    for o in observations:
        refs(o.scope.references)
        for e in o.evidence:evidence(e)
    while True:
        before=(sum(map(len,needed.values())),len(selected),len(regions),len(groups),len(assumptions))
        for n,records in catalogs.items():
            for r in records:
                if r.id not in needed[n]:continue
                refs(r.scope.references)
                needed['harmony_ids'].update(getattr(r,'harmony_ids',[]))
                assumptions.update(getattr(r,'assumption_ids',[]))
                for e in r.evidence:evidence(e)
        for a in report.assumptions:
            if a.id in assumptions:
                refs(a.scope.references)
                for e in a.evidence:evidence(e)
        for rid in regions:selected.update(ix['region_ids'][rid].source_occupancy_event_ids)
        for eid in list(selected):groups.update(ix['event_ids'][eid].chord_group_ids)
        for gid in groups:selected.update(ix['chord_group_ids'][gid].member_event_ids)
        if before==(sum(map(len,needed.values())),len(selected),len(regions),len(groups),len(assumptions)):break
    parts=[]
    for p in report.score.parts:
        events=[e.model_copy(deep=True) for e in p.events if e.id in selected]
        sids={s for e in events for s in e.source_segment_ids};segments=[s.model_copy(deep=True) for s in p.source_segments if s.id in sids]
        mids=measures|{s.location.measure_id for s in segments}
        parts.append(InspectionPart(id=p.id,ordinal=p.ordinal,source_id=p.source_id,source_label=p.source_label,
          instrument=p.instrument,transposition=p.transposition.model_copy(deep=True),
          voices=[v.model_copy(update={'regions':[r.model_copy(deep=True) for r in v.regions if r.id in regions]},deep=True) for v in p.voices],
          measures=[m.model_copy(deep=True) for m in p.measures if m.id in mids],events=events,source_segments=segments,
          chord_groups=[g.model_copy(deep=True) for g in p.chord_groups if g.id in groups],primary_event_ids=[e.id for e in events],support_event_ids=[],included_source_segment_ids=[s.id for s in segments],window_intersections=[]))
    from types import SimpleNamespace
    local=score_indexes(SimpleNamespace(parts=parts))
    closure={field:[r.model_copy(deep=True) for r in records if r.id in needed[n]] for n,records in catalogs.items() for field in [{'harmony_ids':'harmony','cadence_ids':'cadences','diagnostic_ids':'diagnostics','limitation_ids':'limitations'}[n]]}
    closure.update(style_context_source_references=StyleSourceReferences(**{n:[r.id for r in records if r.id not in needed[n]] for n,records in catalogs.items()}),parts=parts,assumptions=[a.model_copy(deep=True) for a in report.assumptions if a.id in assumptions],context_reference_ids=ReferenceSet(**{n:[i for i in ix[n] if i not in local[n]] for n in ReferenceSet.model_fields}))
    return closure


def _focused_report(source, options):
    if isinstance(source, AnalysisReport):
        if options is not None:
            raise ComposerLintError('invalid_options', 'options must be absent for a completed report')
        return _owned_report(source)
    if isinstance(source, (str, Path)):
        return analyze_score(source, options)
    raise ComposerLintError('invalid_options', 'source must be a path or completed AnalysisReport')


def check_harmony(source, options=None):
    """Copy completed harmony/seventh/cadence evidence and its key context."""
    from composerlint.focused import project
    return project(_focused_report(source, options), harmony=True)


def check_voice_leading(source, options=None):
    """Copy completed parallel/crossing/configured-range findings and availability."""
    from composerlint.focused import project
    return project(_focused_report(source, options))


def get_review_page(source, options=None, **selection):
    """Whole-score analysis followed by a bounded, read-only presentation projection."""
    from composerlint.review_models import ReviewArguments
    from composerlint.review import project_review, _project_validated_review
    args = ReviewArguments(path='<completed-report>' if isinstance(source, AnalysisReport) else str(source), **selection)
    if isinstance(source, AnalysisReport):
        if options is not None:
            raise ComposerLintError('invalid_options', 'options must be absent for a completed report')
        # project_review makes and fully validates its own copy. Do not validate
        # the same caller-owned report a second time before that boundary.
        report = source
        page = project_review(report, args)
    elif isinstance(source,(str,Path)) and args.section=='overview':
        score=load_score(source,options)
        context=read_source_context(source,score)
        fields=_build_analysis_fields(score,context)
        # Detach every nested model/list before the one complete validation.
        # No completed report escapes this synchronous build/project boundary.
        data=TypeAdapter(dict).dump_python(fields,mode='json')
        report=AnalysisReport.model_validate(data)
        del fields,data,score,context
        page=_project_validated_review(report,args)
    else:
        report = _focused_report(source, options)
        page = project_review(report, args)
    if not isinstance(source, AnalysisReport) and args.section=='overview':
        from composerlint.report_reuse import publish
        publish(source,report)
    return page


def analyze_advanced(path, options=None, query=None):
    """Independent advanced capability; no renderer, edits or legacy report changes."""
    from composerlint.advanced_runtime import analyze
    return analyze(path, options, query)


def evaluate_music(path, options=None, query=None):
    """Owned read-only V0.2-M4 evaluation; legacy and M3 services retain their meanings."""
    from composerlint.evaluation_runtime import analyze
    return analyze(path, options, query)
