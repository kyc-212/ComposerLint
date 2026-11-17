"""Pure focused projections of owned completed reports; no musical execution."""
from types import SimpleNamespace
from typing import Literal
import re
from pydantic import Field, StrictStr, model_validator
from composerlint.analysis_models import (
    SnapshotRoot, InspectionPart, ReferenceSet, AnalysisMetadata, Assumption,
    Limitation, Diagnostic, Coverage, KeyAnalysis, HarmonySpan, CadenceCandidate,
    StyleSourceReferences, score_indexes, validate_references, RULE_IDS,
)
from composerlint.models import AnalysisOptions, NormalizationMetadata, RationalTime

HARMONY_RULES = ('harmony_classification', 'harmonic_function', 'unresolved_seventh', 'cadence_detection')
VOICE_RULES = ('parallel_fifths', 'parallel_octaves', 'voice_crossing', 'range_issue')


def evidence_of(record):
    yield from getattr(record, 'evidence', [])
    for exclusion in getattr(record, 'exclusions', []):
        yield from exclusion.evidence
    for stage in getattr(record, 'stages', []):
        for exclusion in stage.exclusions:
            yield from exclusion.evidence


class FocusedView(SnapshotRoot):
    source_sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    format: Literal['musicxml', 'midi']
    title: StrictStr | None
    duration: RationalTime
    options: AnalysisOptions
    normalization_metadata: NormalizationMetadata
    source_assumptions: list[StrictStr]
    source_limitations: list[StrictStr]
    analysis_metadata: AnalysisMetadata
    context: Literal['global'] = 'global'
    parts: list[InspectionPart]
    assumptions: list[Assumption]
    limitations: list[Limitation]
    diagnostics: list[Diagnostic]
    coverage: list[Coverage]
    context_reference_ids: ReferenceSet

    @model_validator(mode='after')
    def closure(self):
        rules = HARMONY_RULES if isinstance(self, CheckHarmonyView) else VOICE_RULES
        if tuple(c.rule_id for c in self.coverage) != rules:
            raise ValueError('focused coverage order/group mismatch')
        if any(d.rule_id not in rules for d in self.diagnostics):
            raise ValueError('diagnostic outside focused group')
        ix = score_indexes(self)
        combined = {}
        for name in ReferenceSet.model_fields:
            external = getattr(self.context_reference_ids, name)
            if set(external) & ix[name].keys():
                raise ValueError('parent context overlaps local source')
            patterns = {'part_ids':r'p\d{4,}', 'voice_ids':r'p\d{4,}:v\d{4,}',
                        'measure_ids':r'p\d{4,}:m\d{4,}', 'event_ids':r'p\d{4,}:v\d{4,}:e\d{6,}',
                        'source_segment_ids':r'p\d{4,}:v\d{4,}:s\d{6,}',
                        'chord_group_ids':r'p\d{4,}:v\d{4,}:c\d{6,}', 'region_ids':r'p\d{4,}:v\d{4,}:r\d{6,}'}
            if external != sorted(external) or any(re.fullmatch(patterns[name], i) is None for i in external):
                raise ValueError('invalid parent source identity')
            # Located source records retain encounter order across measures and
            # voices. Sorting IDs groups lanes and rejects valid interleaving.
            # External context is disjoint and must not reorder local evidence.
            combined[name] = {i: None for i in [*ix[name], *external]}
        for p in self.parts:
            events = {e.id:e for e in p.events}
            segments = {s.id:s for s in p.source_segments}
            if len(events)!=len(p.events) or len(segments)!=len(p.source_segments):
                raise ValueError('duplicate source identity')
            if p.primary_event_ids != list(events) or p.support_event_ids or p.included_source_segment_ids != list(segments) or p.window_intersections:
                raise ValueError('focused source membership mismatch')
            for e in p.events:
                ss = [segments[sid] for sid in e.source_segment_ids]
                if any(s.event_id!=e.id for s in ss) or e.onset!=ss[0].normalized_onset or e.duration.fraction()!=sum(s.normalized_duration.fraction() for s in ss):
                    raise ValueError('full event/source closure inconsistent')
                if any(a.normalized_onset.fraction()+a.normalized_duration.fraction()!=b.normalized_onset.fraction() for a,b in zip(ss,ss[1:])):
                    raise ValueError('tie source closure discontinuous')
            for s in p.source_segments:
                if s.event_id not in events or s.location.measure_id not in ix['measure_ids'] or s.location.voice_id not in ix['voice_ids']:
                    raise ValueError('source segment owner missing')
            for g in p.chord_groups:
                if not set(g.member_event_ids)<=events.keys():raise ValueError('chord closure incomplete')
            for v in p.voices:
                for region in v.regions:
                    if not set(region.source_occupancy_event_ids)<=events.keys():raise ValueError('region closure incomplete')
        records = [*self.assumptions,*self.limitations,*self.diagnostics,*self.coverage]
        if isinstance(self, CheckHarmonyView):
            if tuple(c.rule_id for c in self.context_coverage)!=RULE_IDS[:3]:raise ValueError('key context coverage mismatch')
            if self.key_analysis.source_context.source_sha256!=self.source_sha256:raise ValueError('key source mismatch')
            if not set(self.key_analysis.limitation_ids)<={l.id for l in [*self.limitations,*self.context_limitations]}:raise ValueError('key limitation missing')
            records += [*self.harmony,*self.cadences,*self.context_coverage,*self.context_diagnostics,*self.context_limitations]
        ass = {a.id for a in self.assumptions}
        coverage = {c.id for c in [*self.coverage,*getattr(self,'context_coverage',[])]}
        harmony = {h.id:h for h in getattr(self,'harmony',[])}
        from composerlint.musical_validation import validate_evidence
        for record in records:
            validate_references(record.scope.references, combined)
            if isinstance(record, Diagnostic) and not set(record.assumption_ids)<=ass:raise ValueError('missing diagnostic assumption')
            for ev in evidence_of(record):
                if hasattr(ev,'references'):
                    # Located evidence must actually be present, rather than merely labeled parent context.
                    validate_references(ev.references, ix, located=True)
                    validate_evidence(ev, ix, self.options, getattr(self,'key_analysis',None), harmony, True)
                if ev.kind=='coverage' and not set(ev.coverage_ids)<=coverage:raise ValueError('missing focused coverage')
                if ev.kind in ('key_reference','source_key','estimation') and not isinstance(self,CheckHarmonyView):
                    raise ValueError('voice evidence cannot require absent key context')
                if isinstance(self,CheckHarmonyView):
                    if ev.kind=='key_reference' and ev.key_reference_id!=self.key_analysis.key_reference_id:raise ValueError('unresolved focused key reference')
                    if ev.kind=='source_key' and not set(ev.observation_ids)<={o.id for o in self.key_analysis.source_context.key_observations}:raise ValueError('unresolved focused key observation')
                    if ev.kind=='estimation' and not set(ev.candidate_ids)<={c.id for c in self.key_analysis.candidates}:raise ValueError('unresolved focused key candidate')
        return self


class CheckHarmonyView(FocusedView):
    key_analysis: KeyAnalysis
    harmony: list[HarmonySpan]
    cadences: list[CadenceCandidate]
    context_coverage: list[Coverage]
    context_diagnostics: list[Diagnostic]
    context_limitations: list[Limitation]


class CheckVoiceLeadingView(FocusedView):
    pass


def project(report, harmony=False):
    """Reuse the established M4 read-only source closure without changing its semantics."""
    from composerlint.service import _style_closure
    group = HARMONY_RULES if harmony else VOICE_RULES
    context_rules = RULE_IDS[:3] if harmony else ()
    chosen = (*group,*context_rules)
    diagnostics = [d for d in report.diagnostics if d.rule_id in chosen]
    limitations = [l for l in report.limitations if any(r in chosen for r in l.related_rule_ids)]
    hs = report.harmony if harmony else []
    cs = report.cadences if harmony else []
    coverage = [next(c for c in report.coverage if c.rule_id==r) for r in group]
    context_coverage = [c for c in report.coverage if c.rule_id in context_rules]
    # Seed only real parent records and their original typed evidence, including stage exclusions.
    seeds = [SimpleNamespace(scope=r.scope,evidence=list(evidence_of(r)),source_references=StyleSourceReferences()) for r in [*coverage,*context_coverage]]
    for r,domain in [(d,'diagnostic_ids') for d in diagnostics]+[(l,'limitation_ids') for l in limitations]+[(h,'harmony_ids') for h in hs]+[(c,'cadence_ids') for c in cs]:
        seeds.append(SimpleNamespace(scope=r.scope,evidence=r.evidence,source_references=StyleSourceReferences(**{domain:[r.id]})))
    closure = _style_closure(report, seeds)
    score = report.score
    data = dict(source_sha256=score.source_sha256,format=score.format,title=score.title,duration=score.duration,
                options=report.options,normalization_metadata=score.normalization_metadata,
                source_assumptions=score.assumptions,source_limitations=score.limitations,
                analysis_metadata=report.analysis_metadata,parts=closure['parts'],context_reference_ids=closure['context_reference_ids'],
                assumptions=[a for a in report.assumptions if (harmony and a.policy_id in ('m2_global_key_v1','m3_deterministic_music_v1')) or a.id in {x.id for x in closure['assumptions']} or a.reason_code=='m3_observed_pitch_relation'],
                diagnostics=[d for d in closure['diagnostics'] if d.rule_id in group],
                limitations=[l for l in closure['limitations'] if not harmony or not any(r in context_rules for r in l.related_rule_ids)],coverage=coverage)
    validate_references(data['context_reference_ids'],score_indexes(score))
    if harmony:
        data.update(key_analysis=report.key_analysis,harmony=closure['harmony'],cadences=closure['cadences'],context_coverage=context_coverage,
                    context_diagnostics=[d for d in closure['diagnostics'] if d.rule_id in context_rules],
                    context_limitations=[l for l in closure['limitations'] if any(r in context_rules for r in l.related_rule_ids)])
    cls = CheckHarmonyView if harmony else CheckVoiceLeadingView
    return cls.model_validate(data).model_copy(deep=True)
