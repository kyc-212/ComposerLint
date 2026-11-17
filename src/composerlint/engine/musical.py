"""Private exact evaluation ledger and immutable M3 conventions."""
from dataclasses import dataclass, field
from fractions import Fraction
import json
import re
from types import MappingProxyType
from composerlint.models import rt, PitchedEvent
from composerlint.analysis_models import (ReferenceSet, AnalysisScope, Confidence, Diagnostic,
    Coverage, CoverageExclusion, StageCoverage, RULE_IDS, UNITS, reasons, global_scope, score_indexes,
    ExactInterval, EventEvidence, IntervalFact)

POLICY=MappingProxyType({'id':'m3_deterministic_music_v1','minimum_ending_silence':Fraction(1),
    'range_bounds':'inclusive','range_precedence':'voice_then_part','compound_policy':'spelled_perfect_same_direction',
    'surface_step_semitones':(1,2),'seventh_step_semitones':(-1,-2),'seventh_diatonic_delta':-1,
    'chord_templates':MappingProxyType({'major':(0,4,7),'minor':(0,3,7),'diminished':(0,3,6),'augmented':(0,4,8),
        'dominant7':(0,4,7,10),'major7':(0,4,7,11),'minor7':(0,3,7,10),
        'half_diminished7':(0,3,6,10),'diminished7':(0,3,6,9),'minor_major7':(0,3,7,11)})})


def d(p): return 7*p.octave+'CDEFGAB'.index(p.step)
def spelling(p): return p.step+('#'*p.alteration if p.alteration>=0 else 'b'*-p.alteration)
def m21pitch(p):
    from music21.pitch import Pitch
    return Pitch(spelling(p).replace('b','-')+str(p.octave))
def interval_fact(a,b):
    from music21.interval import Interval
    i=Interval(m21pitch(a),m21pitch(b))
    return IntervalFact(diatonic_separation=d(b)-d(a),chromatic_separation=b.chromatic_number-a.chromatic_number,
        generic_size=abs(d(b)-d(a))+1,quality=i.specifier.name,name=i.name)
def bounds(a,b): return ExactInterval(start=rt(a),end=rt(b))
def intersect(a,b,x,y):return max(a,x)<min(b,y)

def union(intervals):
    result=[]
    for a,b in sorted(intervals):
        if a>=b:continue
        if result and a<=result[-1][1]:result[-1]=(result[-1][0],max(b,result[-1][1]))
        else:result.append((a,b))
    return result

def references(score,ids=(),a=None,b=None,regions=(),measures=()):
    ix=score_indexes(score); ids=set(ids)
    segments=[s for s in ix['source_segment_ids'].values() if s.event_id in ids and (a is None or intersect(a,b,s.normalized_onset.fraction(),s.normalized_onset.fraction()+s.normalized_duration.fraction()))]
    mids=set(measures)|{s.location.measure_id for s in segments}
    vids={ix['event_ids'][eid].voice_id for eid in ids}
    return ReferenceSet(part_ids=[p for p in ix['part_ids'] if p in {ix['voice_ids'][v].part_id for v in vids}|{ix['measure_ids'][m].part_id for m in mids}],
        voice_ids=[v for v in ix['voice_ids'] if v in vids],measure_ids=[m for m in ix['measure_ids'] if m in mids],
        event_ids=[e for e in ix['event_ids'] if e in ids],source_segment_ids=[s.id for s in segments],
        chord_group_ids=[g for g in ix['chord_group_ids'] if any(e in ids for e in ix['chord_group_ids'][g].member_event_ids)],
        region_ids=[r for r in ix['region_ids'] if r in regions])
def scope(score,a,b,ids=(),regions=()):return AnalysisScope(kind='span',start=rt(a),end=rt(b),references=references(score,ids,a,b,regions))
def confidence(level='high',reason='m3_observed_pitch_relation'):
    return Confidence(level=level,reason_code=reason,explanation='Confidence concerns the stated observation under the explicit policy, not score quality or intent.')
def warn(rule,s,e,explanation,level='high'):
    return Diagnostic(id='pending',rule_id=rule,category='theory_issue',severity='warning',confidence=confidence(level),scope=s,evidence=e,explanation=explanation,assumption_ids=[])

def unit_key(unit,*items):
    def convert(x):
        if isinstance(x,Fraction):return rt(x).model_dump(mode='json')
        if isinstance(x,(tuple,list)):return [convert(y) for y in x]
        return x
    return json.dumps(convert([unit,*items]),sort_keys=True,separators=(',',':'))

def unit_sort_key(text,sort_time=None):
    """Exact bounds first, then numeric source traversal; never rational JSON text."""
    value=json.loads(text);kind=value[0]
    def times(interval):return tuple(Fraction(t['numerator'],t['denominator']) for t in interval)
    if kind=='pitched_event':time=tuple(sort_time or ());ids=value[1:]
    elif kind=='voice_pair_transition':time=times(value[3])+times(value[4]);ids=value[1:3]
    elif kind=='ordered_voice_pair_snapshot':time=times(value[3]);ids=value[1:3]
    elif kind=='seventh_voice_harmony_transition':time=times(value[1]);ids=value[2:]
    elif kind=='candidate_ending_boundary':time=times(value[1]);ids=value[2]
    else:time=times(value[1]);ids=[]
    return time,tuple(tuple(int(n) for n in re.findall(r'\d+',i)) for i in ids)


@dataclass(frozen=True)
class EvaluationUnit:
    key: str
    eligible: bool
    checked: bool
    flagged: bool=False
    reason: str | None=None
    evidence: tuple=()
    sort_key: tuple=()

class _FreshLedgerUnits(list):
    """Identify the unaliased default without changing the public list type."""


@dataclass
class Ledger:
    rule: str
    units: list[EvaluationUnit]=field(default_factory=_FreshLedgerUnits)
    def __post_init__(self):
        state=vars(self)
        if type(state['units']) is _FreshLedgerUnits:
            state['units']=list(state['units'])
            state['_keys']=set()
    def __getattribute__(self,name):
        if name=='units':
            # An escaped list can later change through an alias, even at the
            # same length. Discard the index before exposing that authority.
            object.__getattribute__(self,'__dict__').pop('_keys',None)
        return object.__getattribute__(self,name)
    def __setattr__(self,name,value):
        if name=='units':object.__getattribute__(self,'__dict__').pop('_keys',None)
        object.__setattr__(self,name,value)
    def add(self,key,eligible,checked=None,flagged=False,reason=None,evidence=(),sort_time=None):
        units=object.__getattribute__(self,'units');keys=vars(self).get('_keys')
        if keys is not None and type(key) is not str:
            # Preserve legacy equality/error behavior for noncanonical callers.
            vars(self).pop('_keys');keys=None
        checked=eligible if checked is None else checked
        if (key in keys if keys is not None else any(u.key==key for u in units)):raise ValueError('duplicate ledger unit')
        if checked and not eligible or flagged and not checked or not checked and not reason:raise ValueError('invalid ledger decision')
        unit=EvaluationUnit(key,eligible,checked,flagged,reason,tuple(evidence),unit_sort_key(key,sort_time))
        try:
            if keys is not None:keys.add(key)
            units.append(unit)
        except BaseException:
            # An interrupted/partially successful append must leave the list
            # authoritative; subsequent calls use the original duplicate scan.
            vars(self).pop('_keys',None)
            raise
    def fields(self):
        c=len(self.units);e=sum(u.eligible for u in self.units);k=sum(u.checked for u in self.units);f=sum(u.flagged for u in self.units)
        exclusions=[]
        ordered=sorted(self.units,key=lambda u:u.sort_key)
        for reason in reasons([u.reason for u in ordered if not u.checked]):
            units=[u for u in ordered if not u.checked and u.reason==reason]
            ev=[]
            for u in units:
                for x in u.evidence:
                    if x not in ev:ev.append(x)
            exclusions.append(CoverageExclusion(unit_ids=[u.key for u in units],count=len(units),reason_code=reason,evidence=ev))
        return dict(status='complete' if k and k==c else ('partial' if k else 'skipped'),candidate_count=c,eligible_count=e,checked_count=k,skipped_count=c-k,flagged_count=f,reason_codes=reasons(['executed'] if k==c and c else ([u.reason for u in self.units if not u.checked] if c else ['no_candidate_units'])),exclusions=exclusions)
    def coverage(self):
        i=RULE_IDS.index(self.rule)
        return Coverage(id=f'cv{i+1:04d}',rule_id=self.rule,implementation_state='implemented',unit=UNITS[i],scope=global_scope(),**self.fields())
    def stage(self,name):return StageCoverage(stage=name,unit='harmonic_span',**self.fields())

@dataclass
class MusicalResult:
    ledgers: dict=field(default_factory=dict)
    diagnostics: list=field(default_factory=list)
    limitations: list=field(default_factory=list)
    harmony: list=field(default_factory=list)
    cadences: list=field(default_factory=list)

BLOCK_PRIORITY=('no_region_context','rest','silence','unknown_sounding_pitch','invalid_tie_chain','unknown_voice_identity','overlapping_events','chordal_texture')
def primary_block(regions):
    values=[r for x in regions if x is not None for r in x.reason_codes]
    if any(x is None for x in regions):values.append('no_region_context')
    return next((r for r in BLOCK_PRIORITY if r in values),'incomplete_sounding_evidence')
def key_guard(key):
    if key.selected_key is None:return 'missing_key'
    from composerlint.engine.key_analysis import metadata_reasons
    meta=metadata_reasons(key.source_context)
    if key.confidence.level=='low' or any(r!='no_key_declarations' for r in meta):return 'key_context_uncertain'
    return None
