"""Exact M1 evidence and a private, request-local music21 global-key adapter."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from fractions import Fraction
import math
from music21 import note, stream
from music21.analysis.discrete import KrumhanslSchmuckler
from composerlint.models import PitchedEvent, rt
from composerlint.analysis_models import (KeyEvidenceStatistics,EventExclusion,ExactInterval,
    KeyAnalysis,KeyCandidate,Confidence,reasons)
from composerlint.engine.source_context import descriptor


@dataclass(frozen=True)
class KeyPolicy:
    """Provisional engineering gates; not musical truths or calibrated confidence."""
    policy_id: str = 'm2_global_key_v1'
    min_eligible_event_count: int = 8
    min_distinct_pitch_class_count: int = 4
    min_eligible_activity_duration: Fraction = Fraction(4,1)
    min_eligible_duration_ratio: Fraction = Fraction(1,2)
    min_best_correlation: Decimal = Decimal('0.55000000')
    min_runner_up_margin: Decimal = Decimal('0.05000000')


POLICY = KeyPolicy()
REPRESENTATIVES = ('C4','C#4','D4','E-4','E4','F4','F#4','G4','A-4','A4','B-4','B4')


def union_intervals(intervals):
    result=[]
    for a,b in sorted(intervals):
        if b<=a: continue
        if result and a<=result[-1][1]: result[-1]=(result[-1][0],max(b,result[-1][1]))
        else: result.append((a,b))
    return result


def evidence_statistics(score):
    h=[Fraction() for _ in range(12)]; eligible=[]; activity=[]; denominator=Fraction(); exclusions=[]
    for p in score.parts:
        voices={v.id:v for v in p.voices}
        for e in p.events:
            if not isinstance(e,PitchedEvent): continue
            denominator+=e.duration.fraction()
            good=[]; bad=[]; region_ids=[]; codes=[]
            if e.inspection_only: codes=['inspection_only']
            else:
                for r in voices[e.voice_id].regions:
                    a=max(e.onset.fraction(),r.start.fraction()); b=min(e.end.fraction(),r.end.fraction())
                    if a>=b: continue
                    if e.sounding_pitch is not None and e.id in r.eligible_event_ids.harmony: good.append((a,b))
                    else:
                        bad.append((a,b)); region_ids.append(r.id)
                        codes += ['not_harmony_eligible',*r.event_reasons.get(e.id,[]),*e.reason_codes]
                        if e.sounding_pitch is None: codes+=['unknown_sounding_pitch']
                # M1 regions cover each event's source window. Unknown slots never fall back to written pitch.
            good=union_intervals(good); bad=union_intervals(bad)
            weight=sum((b-a for a,b in good),Fraction())
            if weight:
                eligible.append(e.id); activity+=good; h[e.sounding_pitch.chromatic_number%12]+=weight
            if codes:
                exclusions.append(EventExclusion(event_id=e.id,ineligible_intervals=[ExactInterval(start=rt(a),end=rt(b)) for a,b in bad],reason_codes=reasons(codes),region_ids=region_ids,source_segment_ids=e.source_segment_ids.copy()))
    weighted=sum(h,Fraction()); total_activity=sum((b-a for a,b in union_intervals(activity)),Fraction())
    return KeyEvidenceStatistics(histogram=[rt(x) for x in h],eligible_event_ids=eligible,eligible_event_count=len(eligible),distinct_pitch_class_count=sum(x>0 for x in h),eligible_activity_duration=rt(total_activity),source_pitched_duration=rt(denominator),eligible_weighted_duration=rt(weighted),eligible_duration_ratio=rt(weighted/denominator) if denominator else None,exclusions=exclusions)


def sufficiency_reasons(stats):
    codes=[]
    if stats.eligible_event_count<POLICY.min_eligible_event_count: codes.append('insufficient_event_count')
    if stats.distinct_pitch_class_count<POLICY.min_distinct_pitch_class_count: codes.append('insufficient_pitch_classes')
    if stats.eligible_activity_duration.fraction()<POLICY.min_eligible_activity_duration: codes.append('insufficient_activity_duration')
    if stats.eligible_duration_ratio is None: codes.append('no_pitched_duration')
    elif stats.eligible_duration_ratio.fraction()<POLICY.min_eligible_duration_ratio: codes.append('insufficient_eligible_ratio')
    return reasons(['insufficient_key_material',*codes]) if codes else []


def coefficient(value):
    if not math.isfinite(value): raise ValueError('nonfinite estimator correlation')
    d=Decimal(str(value)).quantize(Decimal('0.00000001'),rounding=ROUND_HALF_EVEN)
    if not -1<=d<=1: raise ValueError('estimator correlation outside [-1,1]')
    return format(abs(d) if d==0 else d,'.8f')


def estimate_candidates(stats):
    private=stream.Stream()
    for pc,weight in enumerate(stats.histogram):
        if weight.fraction()>0:
            n=note.Note(REPRESENTATIVES[pc]); n.duration.quarterLength=weight.fraction(); private.append(n)
    # No broad exception catch: sufficient-material defects must fail visibly.
    solution=KrumhanslSchmuckler().getSolution(private)
    all_keys=[solution,*solution.alternateInterpretations]
    if len(all_keys)!=24 or {(k.tonic.pitchClass,k.mode) for k in all_keys}!={(pc,mode) for pc in range(12) for mode in ('major','minor')}:
        raise ValueError('estimator must return all 24 major/minor candidates')
    values=[(descriptor(k),k.tonic.pitchClass,coefficient(k.correlationCoefficient)) for k in all_keys]
    values.sort(key=lambda x:(-Decimal(x[2]),x[1],x[0].mode!='major',x[0].tonic))
    return [KeyCandidate(id=f'kc{i:04d}',rank=i,key=k,tonic_pitch_class=pc,correlation=c,method='krumhansl_schmuckler') for i,(k,pc,c) in enumerate(values[:3],1)]


def ranking_reasons(candidates,stats):
    codes=[]
    if len({h.fraction() for h in stats.histogram})==1 and stats.histogram[0].fraction()>0: codes.append('constant_key_distribution')
    if Decimal(candidates[0].correlation)<POLICY.min_best_correlation: codes.append('weak_key_estimate')
    if Decimal(candidates[0].correlation)-Decimal(candidates[1].correlation)<POLICY.min_runner_up_margin: codes.append('ambiguous_key_estimate')
    return reasons(codes)


def metadata_reasons(context):
    obs=context.key_observations
    if not obs: return ['no_key_declarations']
    codes=[r for o in obs for r in o.reason_codes]
    keys={(o.concert_key.tonic,o.concert_key.mode) for o in obs if o.concert_key is not None}
    if len(keys)>1: codes.append('conflicting_key_declarations')
    if not any(o.applicability=='usable' and o.location.effective_source_offset is not None and o.location.effective_source_offset.fraction()==0 for o in obs):
        if any(o.location.effective_source_offset is not None and o.location.effective_source_offset.fraction()>0 for o in obs): codes.append('late_key_declaration')
    return reasons(codes)


def select_key(score,context):
    stats=evidence_statistics(score); meta_codes=metadata_reasons(context)
    obs=context.key_observations; selected=None; method='none'; confidence=None; ids=[]; candidates=[]; state='skipped'; estimator_reason='insufficient_key_material'
    codes=meta_codes.copy()
    if score.options.key_override is not None:
        from composerlint.analysis_models import KeyDescriptor
        override=score.options.key_override
        selected=KeyDescriptor(tonic=override.tonic.replace('-','b'),mode=override.mode); method='user_override'
        codes.append('user_key_override'); estimator_reason='selected_reference_precedence'
    elif obs and not meta_codes and all(o.applicability=='usable' for o in obs):
        selected=obs[0].concert_key.model_copy(deep=True); method='source_metadata'; ids=[o.id for o in obs]
        codes.append('explicit_source_key'); estimator_reason='selected_reference_precedence'
    else:
        insufficient=sufficiency_reasons(stats)
        if insufficient: codes+=insufficient
        else:
            state='executed'; estimator_reason='executed'; candidates=estimate_candidates(stats)
            outcome=ranking_reasons(candidates,stats); codes+=outcome
            if not outcome:
                selected=candidates[0].key.model_copy(deep=True); method='estimated'; codes.append('estimated_global_key')
    if selected:
        reference=method in ('source_metadata','user_override')
        level='high' if reference else ('low' if score.format=='midi' or meta_codes and meta_codes!=['no_key_declarations'] else 'medium')
        confidence=Confidence(level=level,reason_code='selected_reference_not_fit' if reference else ('inferred_midi_spelling' if score.format=='midi' else 'estimated_global_key'),explanation='Reference certainty does not establish score fit.' if reference else 'Qualitative global estimate under provisional gates; not a probability.')
    return KeyAnalysis(status='resolved' if selected else 'unknown',key_reference_id='kref0001' if selected else None,selected_key=selected,selection_method=method,confidence=confidence,reason_codes=reasons(codes),source_context=context.model_copy(deep=True),selected_observation_ids=ids,estimator_state=state,estimator_reason_code=estimator_reason,candidates=candidates,statistics=stats,limitation_ids=[])
