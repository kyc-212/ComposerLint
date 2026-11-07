"""Advanced evidence closure, independent ownership checks and bounded stateless retrieval."""
from composerlint.advanced_models import AdvancedFailure,AdvancedSnapshot
from composerlint.review import digest


def validate_analysis(bundle):
    s=AdvancedSnapshot.model_validate(bundle.snapshot.model_dump(mode='json'))
    data=bundle.report.model_dump(mode='json')
    if digest(data)!=s.base_snapshot_id or bundle.report.score.source_sha256!=s.source_sha256:raise AdvancedFailure('unavailable_evidence','Mismatched legacy/advanced source identity.')
    if digest(s.model_dump(mode='json',exclude={'advanced_snapshot_id'}))!=s.advanced_snapshot_id:raise AdvancedFailure('unavailable_evidence','Advanced content identity does not match.')
    return validate_observations(bundle.report.score,s)


def validate_observations(score,s,*,occupancy_event_ids=None):
    """Source arithmetic/ownership checks shared without an M1 prerequisite."""
    events={e.id:(p,e) for p in score.parts for e in p.events}
    segments={x.id:(p,x) for p in score.parts for x in p.source_segments}
    observations={o.id:o for o in s.observations}
    if len(observations)!=len(s.observations):raise AdvancedFailure('unavailable_evidence','Duplicate advanced IDs.')
    audits={a.event_id:a for a in s.transposition_audit}
    for o in s.observations:
        fact=o.fact
        required=set()
        if fact.kind=='note':required.add(fact.event_id)
        elif fact.kind=='interval':required.update([fact.from_event_id,fact.to_event_id])
        elif fact.kind=='sonority':required.update([m.event_id for m in fact.members]+fact.unknown_event_ids+fact.bass_event_ids)
        elif fact.kind=='sonority_transition':required.update(fact.held_event_ids+fact.reattacked_event_ids)
        elif fact.kind=='continuity_break':required.update(x for x in (fact.before_event_id,fact.after_event_id) if x is not None)
        elif fact.kind=='register':required.update(fact.minimum_event_ids+fact.maximum_event_ids)
        elif fact.kind=='rhythm':required.update(fact.event_ids)
        elif fact.kind=='pattern':required.update(e for occurrence in fact.occurrences for e in occurrence.event_ids)
        elif fact.kind=='passage':required.update(fact.first_event_ids+fact.second_event_ids)
        elif fact.kind=='ensemble':required.update(fact.from_event_ids+fact.to_event_ids+fact.unknown_event_ids)
        if not required <= {x.event_id for x in o.support}:raise AdvancedFailure('unavailable_evidence','Factual event references lack declared source support.')
        typed_premises=[]
        if fact.kind=='sonority_transition':typed_premises=[fact.from_observation,fact.to_observation]
        elif fact.kind in ('role','passage'):typed_premises=fact.supporting_observation_ids
        if not set(typed_premises)<=set(o.premise_ids):raise AdvancedFailure('unavailable_evidence','Factual premises are not closed.')
        for premise in o.premise_ids:
            if premise not in observations:raise AdvancedFailure('unavailable_evidence','Missing advanced premise.',premise)
        for sup in o.support:
            if sup.event_id not in events:raise AdvancedFailure('unavailable_evidence','Unknown source event.')
            p,e=events[sup.event_id];v=next(v for v in p.voices if v.id==e.voice_id)
            mids=list(dict.fromkeys(segments[sid][1].location.measure_id for sid in e.source_segment_ids))
            if (sup.part_id,sup.voice_id,sup.staff,sup.source_segment_ids,sup.measure_ids)!=(p.id,e.voice_id,v.source_staff,e.source_segment_ids,mids):raise AdvancedFailure('unavailable_evidence','Wrong part/voice/staff/segment/measure ownership.')
            if sup.role!=('primary' if e.id in s.resolved_scope.primary_event_ids else 'context'):raise AdvancedFailure('unavailable_evidence','Wrong primary/context role.')
        if o.id!='adv:'+digest({'source':s.source_sha256,**o.model_dump(mode='json',exclude={'id'})}):raise AdvancedFailure('unavailable_evidence','Observation identity does not match content.')
        if fact.kind=='note':
            e=events[fact.event_id][1];a=audits[fact.event_id]
            expected=(getattr(e,'written_pitch',None),getattr(e,'sounding_pitch',None) if a.status=='verified' else None,e.onset,e.end,e.duration,e.kind)
            if (fact.written_pitch,fact.sounding_pitch,fact.onset,fact.end,fact.duration,fact.event_kind)!=expected or o.span.start!=e.onset or o.span.end!=e.end:raise AdvancedFailure('unavailable_evidence','Note facts disagree with source/audit.')
        elif fact.kind=='interval':
            from composerlint.engine.advanced_context import diatonic,chromatic
            a,b=[events[x][1] for x in (fact.from_event_id,fact.to_event_id)]
            p,q=[getattr(e,o.pitch_domain+'_pitch') for e in (a,b)]
            if p is None or q is None or o.pitch_domain=='sounding' and any(audits[e.id].status!='verified' for e in (a,b)):raise AdvancedFailure('unavailable_evidence','Interval requires reliable pitches.')
            if (fact.interval.diatonic_displacement,fact.interval.semitones,fact.interval.generic_size)!=(diatonic(q)-diatonic(p),chromatic(q)-chromatic(p),abs(diatonic(q)-diatonic(p))+1):raise AdvancedFailure('unavailable_evidence','Interval arithmetic disagrees with source.')
            if fact.relation=='melodic' and (a.voice_id!=b.voice_id or a.end!=b.onset or any('invalid_tie_chain' in e.reason_codes for e in (a,b))):raise AdvancedFailure('unavailable_evidence','Melodic interval crosses a continuity break.')
        elif fact.kind=='sonority':
            from composerlint.engine.advanced_context import chromatic
            occupied=[e for _,e in events.values() if e.id in (s.resolved_scope.primary_event_ids if occupancy_event_ids is None else occupancy_event_ids) and e.duration.fraction()>0 and e.onset.fraction()<=o.span.start.fraction() and e.end.fraction()>=o.span.end.fraction()]
            if {e.id for e in occupied}!={x.event_id for x in o.support}:raise AdvancedFailure('unavailable_evidence','Sonority omitted occupied source time.')
            valid=[e for e in occupied if e.kind=='pitched' and audits[e.id].status=='verified']
            unknown=[e.id for e in occupied if e.kind=='pitched' and audits[e.id].status!='verified']
            if {m.event_id for m in fact.members}!={e.id for e in valid} or set(fact.unknown_event_ids)!=set(unknown):raise AdvancedFailure('unavailable_evidence','Sonority pitch slots disagree with audit.')
            for member in fact.members:
                p=events[member.event_id][1].sounding_pitch
                if (member.pitch.step,member.pitch.alteration,member.pitch.octave,member.pitch.chromatic_number)!=(p.step,p.alteration,p.octave,p.chromatic_number):raise AdvancedFailure('unavailable_evidence','Sonority pitch changed from source.')
            if unknown and (fact.complete or fact.bass_event_ids or fact.alternatives):raise AdvancedFailure('unavailable_evidence','Incomplete sounding evidence cannot support a complete chord.')
        elif fact.kind=='pattern':
            used=set()
            for occurrence in fact.occurrences:
                if not used.isdisjoint(occurrence.event_ids):raise AdvancedFailure('unavailable_evidence','Pattern recurrence shares source events.')
                used.update(occurrence.event_ids)
                es=[events[e][1] for e in occurrence.event_ids]
                if any(e.voice_id!=occurrence.voice_id for e in es) or occurrence.span.start!=es[0].onset or occurrence.span.end!=es[-1].end or any(a.end!=b.onset for a,b in zip(es,es[1:])):raise AdvancedFailure('unavailable_evidence','Pattern mapping has wrong lane, time or continuity.')
            from composerlint.engine.advanced_context import diatonic,chromatic
            sequences=[]
            for occurrence in fact.occurrences:
                es=[events[e][1] for e in occurrence.event_ids]
                ps=[getattr(e,o.pitch_domain+'_pitch') for e in es]
                if any(p is None for p in ps) or o.pitch_domain=='sounding' and any(audits[e.id].status!='verified' for e in es):raise AdvancedFailure('unavailable_evidence','Pattern contains an unaudited pitch.')
                sequences.append((es,ps))
            es,ps=sequences[0];dur=[e.duration.fraction() for e in es];on=[e.onset.fraction()-es[0].onset.fraction() for e in es]
            shiftpairs=[]
            for occurrence,(other,qs) in zip(fact.occurrences,sequences):
                if len(other)!=len(es):raise AdvancedFailure('unavailable_evidence','Pattern lengths disagree.')
                dd=[diatonic(q)-diatonic(p) for p,q in zip(ps,qs)];cc=[chromatic(q)-chromatic(p) for p,q in zip(ps,qs)]
                duration=[e.duration.fraction() for e in other];offset=[e.onset.fraction()-other[0].onset.fraction() for e in other]
                scale=duration[0]/dur[0];same_pitch=all(p==q for p,q in zip([(p.step,p.alteration,p.octave) for p in ps],[(q.step,q.alteration,q.octave) for q in qs]))
                uniform=len(set(dd))==1 and len(set(cc))==1
                relation=fact.relation
                okay=True
                if relation=='exact_recurrence':okay=same_pitch and duration==dur and offset==on
                elif relation in ('transposed_recurrence','sequence'):okay=uniform and duration==dur and offset==on
                elif relation=='rhythmic_recurrence':okay=duration==dur and offset==on
                elif relation=='scaled_rhythm':okay=all(b==a*scale for a,b in zip(dur,duration)) and all(b==a*scale for a,b in zip(on,offset))
                elif relation in ('diatonic_pattern','diatonic_sequence'):okay=len(set(dd))==1 and duration==dur and offset==on
                elif occurrence!=fact.occurrences[0] and relation=='pitch_variation':okay=sum((p.step,p.alteration,p.octave)!=(q.step,q.alteration,q.octave) for p,q in zip(ps,qs))==1 and duration==dur and offset==on
                elif occurrence!=fact.occurrences[0] and relation=='duration_variation':
                    changed=[i for i,(a,b) in enumerate(zip(dur,duration)) if a!=b]
                    okay=same_pitch and len(changed)==1 and all(b-a==(duration[changed[0]]-dur[changed[0]] if i>changed[0] else 0) for i,(a,b) in enumerate(zip(on,offset)))
                if not okay or (occurrence.diatonic_shift,occurrence.semitone_shift,occurrence.rhythm_scale.fraction())!=(dd[0],cc[0],scale):raise AdvancedFailure('unavailable_evidence','Pattern transformation fails independent source arithmetic.')
                shiftpairs.append((dd[0],cc[0]))
            if fact.relation in ('sequence','diatonic_sequence'):
                deltas=[(b[0]-a[0],b[1]-a[1]) for a,b in zip(shiftpairs,shiftpairs[1:])]
                if len(sequences)<3 or len(set(deltas))!=1 or deltas[0]==(0,0) or any(a.span.end!=b.span.start or a.voice_id!=b.voice_id for a,b in zip(fact.occurrences,fact.occurrences[1:])):raise AdvancedFailure('unavailable_evidence','Sequence lacks three adjacent consistently transformed windows.')
    for c in s.coverage:
        for u in c.units:
            if not set(u.event_ids)<=set(events):raise AdvancedFailure('unavailable_evidence','Dangling coverage source.')
    return True


from collections import deque
import base64
import json
from composerlint.review import RecordGraph,canonical
from composerlint.review_models import ReviewFailure,ReviewItem
from composerlint.advanced_models import AdvancedPage,AdvancedItem


class AdvancedGraph(RecordGraph):
    """Read-only composition with the M1 graph. Advanced edges are explicitly enumerated."""
    def __init__(self,bundle):
        super().__init__(bundle.report.model_dump(mode='json'))
        s=bundle.snapshot
        self.index_advanced(bundle.report.score,s,{'base_snapshot_id':s.base_snapshot_id,'advanced_snapshot_id':s.advanced_snapshot_id})

    def index_advanced(self,score,s,identity):
        self.snapshot=s;self.extra_edges={}
        self.raw['advanced:query']={'query':s.requested_query.model_dump(mode='json'),'effective_features':s.effective_features,'resolved_scope':s.resolved_scope.model_dump(mode='json'),'policy':s.policy,'component_versions':s.component_versions,'assumptions':s.assumptions}
        self.raw['advanced:overview']={'source_sha256':s.source_sha256,**identity,'query_ref':'advanced:query',
            'observation_count':len(s.observations),'observation_refs':[o.id for o in s.observations],
            'coverage_refs':['advanced_coverage:'+c.feature for c in s.coverage],
            'notice':'Notes, pattern matches, role and boundary candidates are neutral evidence, not a quality rating.'}
        groups={}
        for o in s.observations:
            relation=getattr(o.fact,'relation',None)
            key=(o.feature,o.fact.kind+(('.'+relation) if relation else ''))
            groups.setdefault(key,[]).append(o)
        self.index_refs=[]
        index_summaries=[]
        for (feature,kind),records in groups.items():
            ref='advanced_index:'+feature+':'+kind;self.index_refs.append(ref)
            rows=[{'record_ref':o.id,'span':o.span.model_dump(mode='json'),'part_ids':list(dict.fromkeys(x.part_id for x in o.support))} for o in records]
            self.raw[ref]={'feature':feature,'kind':kind,'total_observations':len(records),'first_observation_ref':records[0].id,'records':rows}
            index_summaries.append({'record_ref':ref,'feature':feature,'kind':kind,'count':len(records),'first_observation_ref':records[0].id})
        self.raw['advanced:overview'].pop('observation_refs')
        self.raw['advanced:overview']['indexes']=index_summaries
        self.extra_edges['advanced:overview']={'advanced:query'}
        for a in s.transposition_audit:self.raw['transpose:'+a.event_id]=a.model_dump(mode='json')
        for c in s.coverage:self.raw['advanced_coverage:'+c.feature]=c.model_dump(mode='json')
        for o in s.observations:
            self.raw[o.id]=o.model_dump(mode='json')
            edges=set(o.premise_ids)|{'advanced_coverage:'+o.feature,'advanced:query'}
            for support in o.support:
                edges|={'event:'+support.event_id,'voice:'+support.voice_id,'part:'+support.part_id,'transpose:'+support.event_id}
                edges.update('segment:'+x for x in support.source_segment_ids)
                edges.update('measure:'+x for x in support.measure_ids)
            if o.fact.kind=='legacy_context':edges.add(o.fact.record_ref)
            self.extra_edges[o.id]=edges
        # Scope anchors remain evidence even when the reference part is excluded by a part filter.
        anchors=set()
        scopes=([s.requested_query.comparison.first,s.requested_query.comparison.second] if s.requested_query.comparison else [s.requested_query.scope])
        for scope in scopes:
            if scope.kind=='measure_range':
                part=next(p for p in score.parts if p.id==scope.reference_part_id)
                n=s.requested_query.analysis_options.neighbor_measures
                anchors.add('part:'+part.id)
                anchors.update('measure:'+m.id for m in part.measures[max(0,scope.start_measure-1-n):scope.end_measure+n])
        for role in s.requested_query.analysis_options.declared_roles:
            part=next(p for p in score.parts if p.id==role.part_id)
            anchors.add('part:'+part.id)
            if role.voice_id is not None:anchors.add('voice:'+role.voice_id)
            anchors.update('measure:'+m.id for m in part.measures[role.start_measure-1:role.end_measure])
        self.extra_edges['advanced:query']=anchors

    def materialize(self,ref):
        if ref in self.items:return
        super().materialize(ref)
        self.links[ref]=sorted(set(self.links[ref])|self.extra_edges.get(ref,set()))

    def evidence(self,ref):
        # Parent resolves deterministic indexed children without extending its legacy catalog.
        try:
            return self.closure(ref)
        except ReviewFailure as e:raise AdvancedFailure(e.code,e.reason,e.record_ref) from e

    def advanced_rows(self,args):
        if args.section=='evidence':return [AdvancedItem.model_validate(x.model_dump()) for x in self.evidence(args.record_ref)]
        refs=['advanced:overview','advanced:query',*self.index_refs] if args.section=='overview' else [o.id for o in self.snapshot.observations] if args.section=='observations' else ['advanced_coverage:'+c.feature for c in self.snapshot.coverage]
        rows=[]
        for ref in refs:
            self.materialize(ref);rows.append(AdvancedItem.model_validate(self.items[ref].model_dump()))
        return rows


def project_advanced(bundle,args):
    validate_analysis(bundle);s=bundle.snapshot
    if args.expected_advanced_snapshot is not None and args.expected_advanced_snapshot!=s.advanced_snapshot_id:raise AdvancedFailure('stale_advanced_snapshot','Advanced query/source/options/policy changed; begin a new overview.')
    binding=digest({'snapshot':s.advanced_snapshot_id,'section':args.section,'record_ref':args.record_ref,'page_size':args.page_size,'max_bytes':args.max_bytes})
    start=0
    if args.cursor:
        try:
            token=json.loads(base64.b64decode(args.cursor,altchars=b'-_',validate=True))
            if set(token)!={'binding','offset'} or token['binding']!=binding or type(token['offset']) is not int or token['offset']<=0:raise ValueError()
            start=token['offset']
        except (ValueError,TypeError,UnicodeError):raise AdvancedFailure('invalid_cursor','Cursor does not match query, source, section, or response options.') from None
    graph=AdvancedGraph(bundle)
    try:rows=graph.advanced_rows(args)
    except ReviewFailure as e:raise AdvancedFailure(e.code,e.reason,e.record_ref) from e
    if start>len(rows) or start==len(rows) and start!=0:raise AdvancedFailure('invalid_cursor','Cursor outside available rows.')
    def page(end):
        cursor=None if end==len(rows) else base64.urlsafe_b64encode(canonical({'binding':binding,'offset':end}).encode()).decode()
        return AdvancedPage(source_sha256=s.source_sha256,base_snapshot_id=s.base_snapshot_id,options_sha256=s.options_sha256,query_sha256=s.query_sha256,advanced_snapshot_id=s.advanced_snapshot_id,
            section=args.section,total_items=len(rows),start_index=start,returned_count=end-start,items=rows[start:end],next_cursor=cursor,section_exhausted=end==len(rows),evidence_complete_in_this_response=args.section=='evidence' and start==0 and end==len(rows))
    end=start
    for candidate in range(start+1,min(start+args.page_size,len(rows))+1):
        if len(canonical(page(candidate).model_dump(mode='json')).encode())>args.max_bytes:break
        end=candidate
    result=page(end)
    if end==start and start<len(rows) or len(canonical(result.model_dump(mode='json')).encode())>args.max_bytes:raise AdvancedFailure('oversized_record','An indivisible record/envelope exceeds max_bytes; use a larger budget or narrower query.',rows[start].record_ref if start<len(rows) else None)
    return result


def verify_advanced_claim(bundle,observation_id,*,event_id,source_segment_id,part_id,voice_id,measure_index,printed_label,pitch_domain,pitch,advanced_snapshot_id,base_snapshot_id):
    """Independent structured location/pitch audit; no unrestricted prose-truth claim."""
    validate_analysis(bundle);s=bundle.snapshot
    if (advanced_snapshot_id,base_snapshot_id)!=(s.advanced_snapshot_id,s.base_snapshot_id):raise AdvancedFailure('stale_advanced_snapshot','Claim snapshot mismatch.')
    graph=AdvancedGraph(bundle);refs={i.record_ref for i in graph.evidence(observation_id)}
    if 'event:'+event_id not in refs or 'segment:'+source_segment_id not in refs:raise AdvancedFailure('unavailable_evidence','Claim lacks source support.')
    if pitch_domain not in ('written','sounding'):raise AdvancedFailure('unavailable_evidence','Unknown pitch domain.')
    event=graph.raw['event:'+event_id];seg=graph.raw['segment:'+source_segment_id];loc=seg['location'];m=graph.measures[loc['measure_id']]
    p=event.get(pitch_domain+'_pitch');actual=None if p is None else p['step']+('#'*p['alteration'] if p['alteration']>=0 else 'b'*-p['alteration'])+str(p['octave'])
    if pitch_domain=='sounding':
        a=graph.raw.get('transpose:'+event_id)
        if a is None or a['status']!='verified':actual=None
    label=None if m['original_number'] is None else m['original_number']+(m['original_suffix'] or '')
    if seg['event_id']!=event_id or (part_id,voice_id,measure_index,printed_label,pitch)!=(loc['part_id'],loc['voice_id'],loc['measure_index'],label,actual):raise AdvancedFailure('unavailable_evidence','Claim pitch/location/ownership disagrees with source.')
    return True


def run(args):
    from composerlint.advanced_runtime import page
    return page(args)
