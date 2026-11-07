"""M4: pure interpretation of owned M2/M3 facts; no musical execution or I/O."""
from fractions import Fraction
import json
from types import MappingProxyType
from composerlint.models import rt, PitchedEvent
from composerlint.analysis_models import (
    STYLE_RULE_IDS, RULE_IDS, ReferenceSet, StyleSourceReferences, StyleEvidence,
    StyleUnavailable, StyleRuleApplicability, StyleOmission, TonalEpisode,
    StyleAnalysis, ProfileDefinition, CoverageEvidence, Confidence, ExactInterval,
    AnalysisScope, global_scope, score_indexes, record_sort, reasons)
from composerlint.engine.musical import key_guard, union

POLICY = MappingProxyType({'id':'m4_classical_compatibility_v1','profile_id':'classical',
    'version':'1.0','minimum_episodes':4,'minimum_ratio':Fraction(3,5),'minimum_cadences':1})
# Internal definitions are immutable tuples; every public profile is newly owned.
_RULES = (
 ('harmony_classification','Recognized tonal surface episodes',('usable M3 global key','checked chord and Roman stages'),
  'Existing complete in-reference surface collections support a bounded tonal description.',
  'Qualified episode: checked chord/Roman; mapped function or context_dependent_function; no blocking surface flags.',
  'None; unfamiliar collections are never negative style evidence.',
  'Unknown/skipped shape, Roman or function prerequisite; uncertain key; blocked surface context.',
  ('Surface collection is not structural harmony.','Inversion and reattack variants do not multiply episodes.')),
 ('harmonic_function','Existing tonic, predominant or dominant mappings',('qualified episode','existing mapped branch'),
  'Retain mapped branches without inferring a progression or independent votes.',
  'One counted episode with an existing tonic, predominant or dominant branch.',
  'None; unknown function is never a conflicting progression.',
  'Unknown/skipped function or unusable key; context-dependent recognition supplies no function support.',
  ('No applied function or local key is inferred.','Natural-minor v is not promoted to dominant.')),
 ('cadence_detection','Supported existing ending candidates',('usable M3 global key','existing candidate with actual cue'),
  'Observed endings supply bounded candidate evidence.',
  'Existing authentic, half, deceptive or plagal candidate with matching source/target and actual ending cue.',
  'None; missing candidates are never improper endings.',
  'Missing/uncertain key, missing cue or upstream deferral.',
  ('Candidates do not establish phrase significance.','Continuous V–I supplies no new candidate.')),
 ('parallel_fifths','Independent-voice spelled perfect-fifth transitions',('existing theory_issue and interval_motion evidence',),
  'The independent-voice convention treats existing positive relations as contextual tensions.',
  'None; checked negatives are not affirmative style support.',
  'One distinct existing M3 positive transition, including its full before/after identity.',
  'Skipped M1 regions remain unavailable.',
  ('May be intentional and needs context.','No hidden/direct or enharmonic predicate is added.')),
 ('parallel_octaves','Independent-voice spelled octave transitions',('existing theory_issue and interval_motion evidence',),
  'The independent-voice convention treats existing positive relations as contextual tensions.',
  'None; checked negatives are not affirmative style support.',
  'One distinct existing M3 positive transition, including its full before/after identity.',
  'Skipped M1 regions remain unavailable.',
  ('May be intentional and needs context.','Unison is not recast as an octave.')),
 ('unresolved_seventh','Existing same-voice seventh outcomes',('existing bounded seventh evidence',),
  'Immediate conventional resolutions support the convention; existing warnings request contextual review.',
  'One unique resolved obligation in an interpretable existing M3 context.',
  'One unique nonconventional outcome attached to an M3 theory_issue diagnostic.',
  'Unknown, held, delayed, exceptional, rest/gap, blocked or uncertain-key outcomes.',
  ('No remote successor or new warning is inferred.','Equivalent limitation/diagnostic copies form one observation.')))
_ASSUMPTIONS = ('One global M2 reference; no local-key tracking.',
 'M3 surface collections are not structural harmony.',
 'Independent voices only where M3 supports them; source spelling and concert pitch retained.',
 'Actual M3 ending cues required; incomplete contexts remain incomplete.',
 'Provisional engineering convention; one supported profile, no comparison against other styles.',
 'Selected key, recognized chord or supported cadence alone does not establish compatibility.')
COMPATIBLE = 'The available evidence meets the limited Classical compatibility convention. This does not establish a unique style, composer, authorship or musical quality.'
INSUFFICIENT = 'The available evidence is insufficient for this Classical compatibility convention; this does not imply non-Classical music.'
CONFLICT = "This observed relation may conflict with the profile's stated convention; it may be intentional and needs context."
UNAVAILABLE = 'This interpretation is unavailable for the recorded prerequisite/context; it is not a musical fault.'


def profile_data():
    definitions=[]
    for rule,description,prerequisites,rationale,support,conflict,unavailable,limits in _RULES:
        definitions.append(dict(rule_id=rule,description=description,source_component_ids=[rule],
          source_coverage_ids=[f'cv{RULE_IDS.index(rule)+1:04d}'],prerequisites=list(prerequisites),
          rationale=rationale,supportive_predicate=support,conflicting_predicate=conflict,
          unavailable_predicate=unavailable,default_severity='info',limitations=list(limits)))
    return dict(profile_id='classical',profile_version='1.0',policy_id=POLICY['id'],
      description='Compatibility with a limited set of Classical tonal conventions',rules=definitions,
      assumptions=list(_ASSUMPTIONS),minimum_tonal_episodes=4,
      minimum_tonal_ratio=rt(Fraction(3,5)).model_dump(mode='json'),minimum_cadence_candidates=1)


def get_profile(): return ProfileDefinition.model_validate(profile_data())
def interval(a,b): return ExactInterval(start=rt(a),end=rt(b))
def pairs(intervals): return [(i.start.fraction(),i.end.fraction()) for i in intervals]
def duration(intervals): return sum((b-a for a,b in intervals),Fraction())
def minus(left,right):
    result=[]
    for a,b in left:
        cursor=a
        for x,y in right:
            if y<=cursor or x>=b: continue
            if cursor<x: result.append((cursor,min(x,b)))
            cursor=max(cursor,min(y,b))
        if cursor<b: result.append((cursor,b))
    return result

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'))
def source_refs(**kw): return StyleSourceReferences(**kw)
def fact(h):return h.evidence[0]
def qualifying(h,key):
    e=fact(h)
    return (h.scope.kind=='span' and key_guard(key) is None and
      e.chord_state=='checked' and e.chord_reason is None and e.root is not None and
      e.quality is not None and e.inversion is not None and e.roman_state=='checked' and
      e.roman_reason is None and e.figure is not None and h.key_reference_id==key.key_reference_id and
      e.function_state=='checked' and
      ((e.function_branch in ('tonic','predominant','dominant') and e.function_reason is None) or
       (e.function_branch=='unknown' and e.function_reason=='context_dependent_function')) and
      not set(e.surface_flags)&{'invalid_tie_chain','unknown_voice_identity','ornament_context_uncertain','transient_surface_sonority'})

def episodes_for(harmonies,key):
    groups=[]
    for h in harmonies:
        if not qualifying(h,key): continue
        e=fact(h); identity=(e.spelled_classes,e.root,e.quality,h.key_reference_id)
        if groups and groups[-1][-1].scope.end==h.scope.start and (fact(groups[-1][0]).spelled_classes,fact(groups[-1][0]).root,fact(groups[-1][0]).quality,groups[-1][0].key_reference_id)==identity:
            groups[-1].append(h)
        else:groups.append([h])
    return groups

def merged_refs(records,score):
    ix=score_indexes(score)
    refs=[r.scope.references for r in records]
    return ReferenceSet(**{n:[i for i in ix[n] if any(i in getattr(r,n) for r in refs)] for n in ReferenceSet.model_fields})


def _overlap_windows(intervals,windows):
    """Visit ordered, disjoint windows with exact overlap and source-order values."""
    intervals=sorted([(lo,hi,index,value) for index,(lo,hi,value) in enumerate(intervals)],key=lambda row:row[0])
    cursor=0;active=[]
    for x,y in windows:
        active=[row for row in active if row[1]>x]
        while cursor<len(intervals) and intervals[cursor][0]<y:
            row=intervals[cursor];cursor+=1
            if row[1]>x:active.append(row)
        yield x,y,[row[3] for row in sorted(active,key=lambda row:row[2])]


def _omission_exclusions(coverage,windows):
    """Decode each source unit once; retain every overlapping exclusion reason."""
    intervals=[]
    for c in coverage:
        for ex in c.exclusions:
            for unit in ex.unit_ids or []:
                value=json.loads(unit)
                if value[0]=='harmonic_span':
                    lo,hi=(Fraction(t['numerator'],t['denominator']) for t in value[1])
                    intervals.append((lo,hi,ex.reason_code))
    yield from _overlap_windows(intervals,windows)

def extract(snapshot):
    """Read supplied facts once. Validation repeats only this bookkeeping, never M3."""
    score,key=snapshot.score,snapshot.key_analysis
    groups=episodes_for(snapshot.harmony,key); observations=[]; unavailable={r:[] for r in STYLE_RULE_IDS}
    def add(rule,polarity,scope,evidence,refs,unit):
        reason={'supportive':'style_limited_compatibility','conflicting':'style_potentially_intentional_conflict','unavailable':'style_upstream_unavailable'}[polarity]
        observations.append(StyleEvidence(id='pending',profile_id='classical',profile_version='1.0',rule_id=rule,
          polarity=polarity,unit_key=encoded(unit),source_references=refs,scope=scope.model_copy(deep=True),
          evidence=[e.model_copy(deep=True) for e in evidence],confidence=Confidence(level='low',reason_code=reason,
          explanation='Low confidence in the limited V0.1 style interpretation; source facts retain their own confidence.'),
          explanation=CONFLICT if polarity=='conflicting' else (UNAVAILABLE if polarity=='unavailable' else 'Existing validated facts support this limited profile convention.')))
    def withheld(rule,codes,refs,ev,unit_ids,scope=None,unit=None):
        codes=reasons(codes or ['style_upstream_unavailable'])
        unavailable[rule].append(StyleUnavailable(reason_codes=codes,source_references=refs,
          source_unit_ids=unit_ids,evidence=[e.model_copy(deep=True) for e in ev]))
        add(rule,'unavailable',scope or global_scope(),ev,refs,unit or ['unavailable',rule,unit_ids,codes])
    episodes=[]
    for group in groups:
        first,last=group[0],group[-1];e=fact(first);hids=[h.id for h in group]
        episodes.append(TonalEpisode(start=first.scope.start,end=last.scope.end,harmony_ids=hids,
          spelled_classes=list(e.spelled_classes),root=e.root,quality=e.quality,key_reference_id='kref0001'))
        scope=AnalysisScope(kind='span',start=first.scope.start,end=last.scope.end,references=merged_refs(group,score))
        unit=['episode',hids]
        add('harmony_classification','supportive',scope,[fact(h) for h in group],source_refs(harmony_ids=hids,coverage_ids=['cv0004']),unit)
        mapped=[h for h in group if fact(h).function_branch in ('tonic','predominant','dominant') and fact(h).function_reason is None]
        if mapped:add('harmonic_function','supportive',scope,[fact(h) for h in mapped],source_refs(harmony_ids=hids,coverage_ids=['cv0005']),unit)
    for h in snapshot.harmony:
        e=fact(h)
        codes=[e.chord_reason,e.roman_reason,e.function_reason,key_guard(key),*e.surface_flags]
        if not qualifying(h,key):
            withheld('harmony_classification',codes,source_refs(harmony_ids=[h.id],coverage_ids=['cv0004']),[e],[],h.scope,['harmony',h.id])
        if not qualifying(h,key) or e.function_branch=='unknown':
            withheld('harmonic_function',codes,source_refs(harmony_ids=[h.id],coverage_ids=['cv0005']),[e],[],h.scope,['function',h.id])
    cadence_keys={}
    for c in snapshot.cadences:
        ev=next(e for e in c.evidence if e.kind=='cadence_ending')
        if key_guard(key) is not None:continue
        unit=['cadence',ev.boundary.model_dump(mode='json'),ev.pattern,ev.source_harmony_ids,ev.target_harmony_ids]
        k=encoded(unit)
        if k in cadence_keys:
            cadence_keys[k].source_references.cadence_ids.append(c.id)
            continue
        add('cadence_detection','supportive',c.scope,c.evidence,source_refs(cadence_ids=[c.id],harmony_ids=c.harmony_ids,coverage_ids=['cv0011']),unit)
        cadence_keys[k]=observations[-1]
    for rule in ('parallel_fifths','parallel_octaves'):
        units={}
        for d in snapshot.diagnostics:
            if d.rule_id!=rule or d.category!='theory_issue':continue
            ev=next(e for e in d.evidence if e.kind=='interval_motion')
            unit=['parallel',ev.voice_ids,ev.before.model_dump(mode='json'),ev.after.model_dump(mode='json')]
            k=encoded(unit)
            if k in units:
                units[k].source_references.diagnostic_ids.append(d.id)
            else:
                add(rule,'conflicting',d.scope,[ev],source_refs(diagnostic_ids=[d.id],coverage_ids=[f'cv{RULE_IDS.index(rule)+1:04d}']),unit)
                units[k]=observations[-1]
    seventh={}
    for category in ('diagnostics','limitations'):
        for record in getattr(snapshot,category):
            for ev in record.evidence:
                if ev.kind!='seventh_resolution':continue
                unit=['seventh',ev.source_bounds.model_dump(mode='json'),ev.voice_id,ev.obligation_event_id]
                k=encoded(unit)
                if k not in seventh:seventh[k]=[ev,record.scope,[],[],False,[],[]]
                row=seventh[k]
                row[2 if category=='diagnostics' else 3].append(record.id)
                row[4] |= category=='diagnostics' and record.category=='theory_issue' and ev.outcome=='nonconventional'
    for ex in snapshot.coverage[9].exclusions:
        for ev in ex.evidence:
            if ev.kind!='seventh_resolution':continue
            unit=['seventh',ev.source_bounds.model_dump(mode='json'),ev.voice_id,ev.obligation_event_id]
            k=encoded(unit)
            if k not in seventh:
                end=ev.next_bounds.end if ev.next_bounds else ev.source_bounds.end
                scope=AnalysisScope(kind='span',start=ev.source_bounds.start,end=end,references=ev.references.model_copy(deep=True))
                seventh[k]=[ev,scope,[],[],False,[],[]]
            row=seventh[k]
            row[5].extend(u for u in (ex.unit_ids or []) if json.loads(u)[2:]==[ev.voice_id,ev.obligation_event_id] and u not in row[5])
            row[6].append(ex.reason_code)
    for k,(ev,scope,dids,lids,warning,unit_ids,codes) in seventh.items():
        refs=source_refs(harmony_ids=[h.id for h in snapshot.harmony if h.id in ev.source_harmony_ids+ev.next_harmony_ids],diagnostic_ids=dids,limitation_ids=lids,coverage_ids=['cv0010'])
        if ev.outcome=='resolved':add('unresolved_seventh','supportive',scope,[ev],refs,json.loads(k))
        elif ev.outcome=='nonconventional' and warning:add('unresolved_seventh','conflicting',scope,[ev],refs,json.loads(k))
        else:withheld('unresolved_seventh',[*ev.reason_codes,*codes],refs,[ev],unit_ids,scope,json.loads(k))
    # Actual exclusions retain the existing unit keys and exact evidence. No new ledger.
    for rule in STYLE_RULE_IDS:
        coverage=next(c for c in snapshot.coverage if c.rule_id==rule)
        for ex in coverage.exclusions:
            if rule=='unresolved_seventh' and any(e.kind=='seventh_resolution' for e in ex.evidence):continue
            withheld(rule,[ex.reason_code],source_refs(coverage_ids=[coverage.id]),
              [CoverageEvidence(kind='coverage',coverage_ids=[coverage.id]),*ex.evidence],list(ex.unit_ids or []),
              unit=['exclusion',coverage.id,ex.reason_code,list(ex.unit_ids or [])])
        if not coverage.candidate_count:
            withheld(rule,coverage.reason_codes,source_refs(coverage_ids=[coverage.id]),[CoverageEvidence(kind='coverage',coverage_ids=[coverage.id])],[],unit=['empty',coverage.id])
    observations.sort(key=record_sort)
    observations=[o.model_copy(update={'id':f'sty{i:06d}'},deep=True) for i,o in enumerate(observations,1)]
    applicability=[]
    for rule in STYLE_RULE_IDS:
        c=next(c for c in snapshot.coverage if c.rule_id==rule);rows=[o for o in observations if o.rule_id==rule]
        support=sum(o.polarity=='supportive' for o in rows);conflicts=sum(o.polarity=='conflicting' for o in rows)
        usable=bool(support or conflicts or rule in ('parallel_fifths','parallel_octaves','cadence_detection') and c.checked_count)
        status=('partial' if unavailable[rule] else 'available') if usable else 'unavailable'
        applicability.append(StyleRuleApplicability(rule_id=rule,upstream_coverage=[c.model_copy(deep=True)],availability=status,
          supportive_count=support,conflicting_count=conflicts,unavailable=unavailable[rule]))
    D=union([(e.onset.fraction(),e.end.fraction()) for p in score.parts for e in p.events if isinstance(e,PitchedEvent)])
    T=union([(max(a,h.scope.start.fraction()),min(b,h.scope.end.fraction())) for a,b in D for group in groups for h in group])
    U=minus(D,T);P,Q=duration(D),duration(T)
    ix=score_indexes(score);omissions=[]
    def omission_windows():
        for a,b in U:
            boundaries=sorted({a,b,*[x for h in snapshot.harmony for x in (h.scope.start.fraction(),h.scope.end.fraction()) if a<x<b]})
            yield from zip(boundaries,boundaries[1:])
    windows=list(omission_windows())
    harmonies=_overlap_windows([(h.scope.start.fraction(),h.scope.end.fraction(),h) for h in snapshot.harmony],windows)
    occupants=_overlap_windows([(e.onset.fraction(),e.end.fraction(),e.id) for e in ix['event_ids'].values() if isinstance(e,PitchedEvent)],windows)
    for (x,y,excluded_codes),(_,_,hs),(_,_,ids) in zip(_omission_exclusions(snapshot.coverage[3:5],windows),harmonies,occupants):
        codes=[key_guard(key)]
        for h in hs:
            e=fact(h);codes.extend([e.chord_reason,e.roman_reason,e.function_reason,*e.surface_flags])
        if not hs:codes.append('style_unrepresented_pitched_material')
        codes.append('style_upstream_unavailable')
        # Existing exclusions are retained through their coverage provenance.
        codes.extend(excluded_codes)
        omissions.append(StyleOmission(start=rt(x),end=rt(y),reason_codes=reasons(codes),references=ReferenceSet(event_ids=ids),source_references=source_refs(harmony_ids=[h.id for h in hs],coverage_ids=['cv0004','cv0005'])))
    guard=key_guard(key);N=len(groups);C=len(cadence_keys)
    failed=failed_gates(guard,N,P,Q,C)
    conflicts=sum(o.polarity=='conflicting' for o in observations)
    result=StyleAnalysis(source_sha256=score.source_sha256,options=score.options.model_copy(deep=True),
      state='insufficient_evidence' if failed else 'compatible_with_classical',confidence=Confidence(level='low',
      reason_code='style_insufficient_evidence' if failed else 'style_limited_compatibility',explanation='V0.1 uses a provisional limited convention with low style confidence.'),
      explanation=(INSUFFICIENT if failed else COMPATIBLE)+(' Supported features coexist with potentially intentional departures.' if conflicts else ''),
      key_prerequisite_reason=guard,raw_qualifying_span_count=sum(map(len,groups)),tonal_episode_count=N,episodes=episodes,
      pitched_intervals=[interval(a,b) for a,b in D],tonal_intervals=[interval(a,b) for a,b in T],unavailable_intervals=[interval(a,b) for a,b in U],
      pitched_duration=rt(P),recognized_tonal_duration=rt(Q),recognized_tonal_ratio=rt(Q/P) if P else None,
      cadence_candidate_count=C,failed_gates=failed,applicability=applicability,omissions=omissions,
      limitations=reasons(['style_profile_v1','style_limited_compatibility',*(['style_insufficient_evidence'] if failed else []),*(['style_no_pitched_duration'] if not P else []),*(['style_upstream_unavailable'] if any(unavailable.values()) else []),*(['style_potentially_intentional_conflict'] if conflicts else [])]),
      assumptions=list(_ASSUMPTIONS),supportive_observation_count=sum(o.polarity=='supportive' for o in observations),conflicting_observation_count=conflicts,unavailable_observation_count=sum(o.polarity=='unavailable' for o in observations))
    return result,observations


def failed_gates(guard,N,P,Q,C):
    result=[]
    if guard:result.append(guard)
    if N<4:result.append('style_tonal_episode_count_insufficient')
    if not P:result.append('style_no_pitched_duration')
    elif 5*Q<3*P:result.append('style_tonal_duration_insufficient')
    if C<1:result.append('style_cadence_evidence_insufficient')
    return result


def require(value,message):
    if not value:raise ValueError(message)

def validate_result(r):
    D,T,U=map(pairs,(r.pitched_intervals,r.tonal_intervals,r.unavailable_intervals))
    for values in (D,T,U):require(values==union(values),'style intervals must be ordered disjoint canonical unions')
    require(not minus(T,D) and U==minus(D,T),'style D/T/U inconsistent')
    P,Q=duration(D),duration(T)
    require(r.pitched_duration.fraction()==P and r.recognized_tonal_duration.fraction()==Q,'style durations differ from unions')
    require(r.recognized_tonal_ratio==(rt(Q/P) if P else None),'style ratio inconsistent')
    require(r.tonal_episode_count==len(r.episodes) and r.raw_qualifying_span_count==sum(len(e.harmony_ids) for e in r.episodes),'style episode counts inconsistent')
    require(r.episodes==sorted(r.episodes,key=lambda e:e.start.fraction()) and all(a.end.fraction()<=b.start.fraction() for a,b in zip(r.episodes,r.episodes[1:])),'style episode bounds unordered/overlap')
    require(len({i for e in r.episodes for i in e.harmony_ids})==r.raw_qualifying_span_count,'duplicate tonal span')
    require(union(pairs(r.episodes))==T,'episode intervals differ from recognized union')
    require(union(pairs(r.omissions))==U and sum((i.end.fraction()-i.start.fraction() for i in r.omissions),Fraction())==duration(U),'omission partition inconsistent')
    require(r.key_prerequisite_reason in (None,'missing_key','key_context_uncertain'),'invalid style key prerequisite')
    failed=failed_gates(r.key_prerequisite_reason,r.tonal_episode_count,P,Q,r.cadence_candidate_count)
    require(r.failed_gates==failed and r.state==('insufficient_evidence' if failed else 'compatible_with_classical'),'style state/gates inconsistent')
    require(r.confidence.level=='low' and r.confidence.reason_code==('style_insufficient_evidence' if failed else 'style_limited_compatibility'),'style confidence invalid')
    require([a.rule_id for a in r.applicability]==list(STYLE_RULE_IDS),'style applicability catalog invalid')
    require(r.assumptions==list(_ASSUMPTIONS),'style assumptions changed')
    require(r.explanation==(INSUFFICIENT if failed else COMPATIBLE)+(' Supported features coexist with potentially intentional departures.' if r.conflicting_observation_count else ''),'style explanation changed')
    require(r.limitations==reasons(['style_profile_v1','style_limited_compatibility',*(['style_insufficient_evidence'] if failed else []),*(['style_no_pitched_duration'] if not P else []),*(['style_upstream_unavailable'] if any(a.unavailable for a in r.applicability) else []),*(['style_potentially_intentional_conflict'] if r.conflicting_observation_count else [])]),'style limitations inconsistent')
    if r.key_prerequisite_reason:require(not T and not r.episodes and not r.cadence_candidate_count,'conditional key facts cannot support compatibility')
    require(r.supportive_observation_count==sum(a.supportive_count for a in r.applicability) and r.conflicting_observation_count==sum(a.conflicting_count for a in r.applicability),'style observation totals inconsistent')
    require(r.unavailable_observation_count==sum(len(a.unavailable) for a in r.applicability),'style unavailable total inconsistent')
    for a in r.applicability:
        c=a.upstream_coverage[0]
        require(len(a.upstream_coverage)==1 and c.rule_id==a.rule_id and c.id==f'cv{RULE_IDS.index(a.rule_id)+1:04d}','style upstream ledger mismatched')
        usable=bool(a.supportive_count or a.conflicting_count or a.rule_id in ('parallel_fifths','parallel_octaves','cadence_detection') and c.checked_count)
        require(a.availability==(('partial' if a.unavailable else 'available') if usable else 'unavailable'),'style applicability inconsistent')

def validate_snapshot(snapshot,view=False):
    metadata=snapshot.analysis_metadata
    if metadata.style_policy_id is None:
        require(snapshot.style_analysis is None and not snapshot.style_evidence,'style data without policy');return
    require(metadata.musical_policy_id is not None and snapshot.style_analysis is not None,'style requires completed M3 facts')
    r=snapshot.style_analysis;c=snapshot.coverage[-1]
    require((c.implementation_state,c.status,c.candidate_count,c.eligible_count,c.checked_count,c.skipped_count,c.flagged_count,c.reason_codes,c.exclusions)==('implemented','complete',1,1,1,0,int(r.conflicting_observation_count>0),['executed'],[]),'cv0012 must describe completed score evaluation and tension presence')
    require(r.source_sha256==(snapshot.source_sha256 if view else snapshot.score.source_sha256),'style parent hash mismatch')
    require(all(a.upstream_coverage==[next(c for c in snapshot.coverage if c.rule_id==a.rule_id)] for a in r.applicability),'style applicability differs from original upstream ledgers')
    if not view:
        expected,observations=extract(snapshot)
        require(r==expected and snapshot.style_evidence==observations,'style result/evidence differ from supplied M2/M3 facts')
    else:
        validate_global_context(snapshot)
        validate_observations(snapshot.style_evidence,snapshot)
        require(snapshot.style_evidence==sorted(snapshot.style_evidence,key=record_sort),'style view order invalid')

def validate_observations(observations,snapshot):
    catalogs={name:{r.id:r for r in getattr(snapshot,field)} for name,field in (
      ('harmony_ids','harmony'),('cadence_ids','cadences'),('diagnostic_ids','diagnostics'),('limitation_ids','limitations'),('coverage_ids','coverage'))}
    units=set()
    for o in observations:
        require(o.profile_version=='1.0' and o.rule_id in STYLE_RULE_IDS and o.polarity is not None and o.unit_key is not None and o.confidence.level=='low','invalid current style observation')
        require((o.rule_id,o.unit_key) not in units,'duplicate style source unit');units.add((o.rule_id,o.unit_key))
        for n,records in catalogs.items():
            ids=getattr(o.source_references,n)
            # Measure views may label provenance diagnostics as global support records.
            require(ids==[i for i in records if i in ids],'unresolved/unordered style provenance '+n)
        refs=o.source_references
        sources=[catalogs[n][i] for n in ('harmony_ids','cadence_ids','diagnostic_ids','limitation_ids') for i in getattr(refs,n)]
        allfacts=[e for record in sources for e in record.evidence]
        for ev in o.evidence:
            require(ev.kind=='coverage' and all(i in catalogs['coverage_ids'] for i in ev.coverage_ids) or ev in allfacts or any(ev in ex.evidence for c in snapshot.coverage for ex in c.exclusions),'style evidence lacks source fact')
        require(o.source_references.coverage_ids==[f'cv{RULE_IDS.index(o.rule_id)+1:04d}'],'style observation coverage mismatch')
        expected_reason={'supportive':'style_limited_compatibility','conflicting':'style_potentially_intentional_conflict','unavailable':'style_upstream_unavailable'}[o.polarity]
        require(o.confidence.reason_code==expected_reason,'style polarity/confidence mismatch')
        if o.polarity=='unavailable':
            source_facts=[e for e in o.evidence if e.kind!='coverage']
            if o.rule_id in ('harmony_classification','harmonic_function') and o.unit_key.startswith(('["harmony",','["function",')):
                require(refs.harmony_ids and all(not qualifying(catalogs['harmony_ids'][i],snapshot.key_analysis) or o.rule_id=='harmonic_function' and fact(catalogs['harmony_ids'][i]).function_branch=='unknown' for i in refs.harmony_ids),'available harmony mislabeled unavailable')
            elif o.rule_id=='unresolved_seventh' and any(e.kind=='seventh_resolution' for e in source_facts):
                require(all(e.outcome=='unknown' for e in source_facts if e.kind=='seventh_resolution'),'known seventh mislabeled unavailable')
            else:
                c=catalogs['coverage_ids'][refs.coverage_ids[0]]
                require(c.skipped_count or not c.candidate_count,'available checked negatives mislabeled unavailable')
        if o.polarity=='conflicting':
            require(o.rule_id in ('parallel_fifths','parallel_octaves','unresolved_seventh') and any(d.category=='theory_issue' and d.rule_id==o.rule_id for d in sources if hasattr(d,'category')),'conflict lacks existing warning')
        if o.polarity=='supportive':
            if o.rule_id in ('harmony_classification','harmonic_function'):
                require(refs.harmony_ids and all(qualifying(catalogs['harmony_ids'][i],snapshot.key_analysis) for i in refs.harmony_ids),'support from unqualified harmony')
                if o.rule_id=='harmonic_function':require(any(e.kind=='harmony_analysis' and e.function_branch!='unknown' for e in o.evidence),'support from unknown function')
            elif o.rule_id=='cadence_detection':require(refs.cadence_ids and key_guard(snapshot.key_analysis) is None,'cadence support unavailable')
            elif o.rule_id=='unresolved_seventh':require(any(e.kind=='seventh_resolution' and e.outcome=='resolved' for e in o.evidence),'seventh support not resolved')
            else:raise ValueError('parallel negatives cannot supply support')

def validate_evidence_view(view):
    require(view.source_sha256==view.style_analysis.source_sha256==view.key_analysis.source_context.source_sha256 and view.options==view.style_analysis.options,'style view provenance mismatch')
    ids=[d.rule_id for d in view.definitions]
    require(ids==[i for i in STYLE_RULE_IDS if i in ids] and len(ids) in (1,6),'style lookup definitions invalid')
    require(view.definitions==[d for d in view.profile.rules if d.rule_id in ids],'style definitions differ from frozen profile')
    require(view.applicability==[a for a in view.style_analysis.applicability if a.rule_id in ids],'style lookup applicability differs from global result')
    require(all(o.rule_id in ids for o in view.observations) and view.observations==sorted(view.observations,key=record_sort),'style lookup order/filter invalid')
    validate_observations(view.observations,view)
    for a in view.applicability:
        rows=[o for o in view.observations if o.rule_id==a.rule_id]
        require((sum(o.polarity=='supportive' for o in rows),sum(o.polarity=='conflicting' for o in rows),sum(o.polarity=='unavailable' for o in rows))==(a.supportive_count,a.conflicting_count,len(a.unavailable)),'style lookup omitted or added observations')
    validate_global_context(view)
    ix=score_indexes(view)
    from composerlint.musical_validation import validate_evidence
    hs={h.id:h for h in view.harmony}
    for h in view.harmony:
        require(h.scope.references==h.evidence[0].references and h.event_ids==h.evidence[0].references.event_ids,'style source harmony membership changed')
        e=h.evidence[0]
        require(h.roman_numeral==e.figure and h.function==e.function_branch and h.chord_label==(f'{e.root}:{e.quality}' if e.root else None),'style source harmony fields differ')
    for records in (view.harmony,view.cadences,view.diagnostics,view.limitations):
        for r in records:
            for e in r.evidence:validate_evidence(e,ix,view.options,view.key_analysis,hs,True)
    for p in view.parts:
        segments={s.id:s for s in p.source_segments}
        for e in p.events:
            require(all(sid in segments for sid in e.source_segment_ids),'style event segment closure incomplete')
            ss=[segments[sid] for sid in e.source_segment_ids]
            require(e.onset==ss[0].normalized_onset and e.duration.fraction()==sum(s.normalized_duration.fraction() for s in ss),'style event/source times differ')
    for o in view.observations:
        require(o.id.startswith('sty') and o.id[3:].isdigit(),'invalid style ID')
        for refs in [o.scope.references,*[e.references for e in o.evidence if hasattr(e,'references')]]:
            for n in ReferenceSet.model_fields:require(set(getattr(refs,n))<=ix[n].keys(),'style source closure incomplete')
    for h in view.harmony:
        require(set(h.event_ids)<=ix['event_ids'].keys(),'style harmony source closure incomplete')
    require(view.coverage[-1].flagged_count==int(view.style_analysis.conflicting_observation_count>0),'style view global flagged marker inconsistent')


def validate_global_context(view):
    """Global result IDs outside a located view are explicit parent context."""
    import re
    context=view.style_context_source_references
    for n,field,prefix in (('harmony_ids','harmony','h'),('cadence_ids','cadences','cad'),('diagnostic_ids','diagnostics','d'),('limitation_ids','limitations','l'),('coverage_ids','coverage','cv')):
        ids=getattr(context,n);local={r.id for r in getattr(view,field)}
        require(not set(ids)&local and ids==sorted(ids,key=lambda i:int(i[len(prefix):])) and all(re.fullmatch(prefix+r'\d{4,}',i) for i in ids),'invalid global style source context')
    hs={h.id for h in view.harmony}|set(context.harmony_ids)
    require(all(i in hs for e in view.style_analysis.episodes for i in e.harmony_ids),'global episode ID lacks local/context provenance')
    require(all(i in hs for o in view.style_analysis.omissions for i in o.source_references.harmony_ids),'global omission ID lacks local/context provenance')
