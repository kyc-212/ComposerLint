"""Bounded deterministic views of full, validated analysis; no editing/inference/I/O."""
import base64
import hashlib
import json
from collections import deque
from fractions import Fraction
from composerlint.analysis_models import AnalysisReport
from composerlint.review_models import ReviewArguments, ReviewPage, ReviewItem, ReviewFailure


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


DOMAINS = {'part_ids': 'part', 'part_id': 'part', 'voice_ids': 'voice', 'voice_id': 'voice',
    'measure_ids': 'measure', 'measure_id': 'measure', 'event_ids': 'event', 'event_id': 'event',
    'source_segment_ids': 'segment', 'region_ids': 'region', 'region_id': 'region',
    'chord_group_ids': 'chord_group', 'harmony_ids': 'harmony', 'source_harmony_ids': 'harmony',
    'target_harmony_ids': 'harmony', 'context_harmony_ids': 'harmony', 'coverage_ids': 'coverage',
    'assumption_ids': 'assumption', 'limitation_ids': 'limitation', 'diagnostic_ids': 'diagnostic',
    'cadence_ids': 'cadence', 'observation_ids': 'key_observation', 'selected_observation_ids': 'key_observation'}
DOMAINS.update({k:'event' for k in ('changed_event_ids','held_event_ids','bass_event_ids','member_event_ids',
    'source_occupancy_event_ids','active_sounding_event_ids','blocking_event_ids')})
DOMAINS.update({k:'voice' for k in ('source_voice_id','target_voice_id','high_voice_id','low_voice_id')})


def references(value):
    """Only explicit typed record fields create links; labels/lyrics never do."""
    found = set()
    def visit(v):
        if isinstance(v, dict):
            for key, child in v.items():
                if key in DOMAINS:
                    for ident in child if isinstance(child, list) else [child]:
                        if isinstance(ident, str): found.add(DOMAINS[key] + ':' + ident)
                if key in ('key_reference_id', 'candidate_ids', 'statistics_reference', 'histogram_reference') and child:
                    found.add('key:global')
                visit(child)
        elif isinstance(v, list):
            for child in v: visit(child)
    visit(value)
    return found


def _label(measure):
    n = measure['original_number']
    return None if n is None else n + (measure['original_suffix'] or '')


class RecordGraph:
    """Source records and explicit support; large collections have lossless indexed rows."""
    def __init__(self, data):
        self.raw, self.items, self.links = {}, {}, {}
        self.measures, self.parts = {}, {}
        self.data = data
        for name in ('diagnostics', 'coverage', 'assumptions', 'limitations', 'harmony', 'cadences', 'style_evidence'):
            kind = {'diagnostics':'diagnostic', 'assumptions':'assumption', 'limitations':'limitation',
                    'cadences':'cadence', 'style_evidence':'style_evidence'}.get(name, name)
            for r in data[name]: self.raw[kind + ':' + r['id']] = r
        self.raw.update({'options:effective': data['options'], 'metadata:analysis': data['analysis_metadata'],
            'normalization:score': data['score']['normalization_metadata'], 'key:global': data['key_analysis'],
            'source:assumptions': {'values': data['score']['assumptions']},
            'source:limitations': {'values': data['score']['limitations']}})
        if data['style_analysis'] is not None: self.raw['style:global'] = data['style_analysis']
        for o in data['key_analysis']['source_context']['key_observations']:
            self.raw['key_observation:' + o['id']] = o
        self.index_source(data['score'])
        self.raw['overview:score'] = self.overview_score()

    def index_source(self,score):
        """The unchanged source-record index also serves source-only proofs."""
        for p in score['parts']:
            self.parts[p['id']] = p
            self.raw['part:' + p['id']] = {k:v for k,v in p.items() if k not in ('voices','measures','events','source_segments','chord_groups')}
            for domain, field in [('measure','measures'), ('event','events'), ('segment','source_segments'), ('chord_group','chord_groups')]:
                for r in p[field]: self.raw[domain + ':' + r['id']] = r
            for v in p['voices']:
                self.raw['voice:' + v['id']] = {k:r for k,r in v.items() if k != 'regions'}
                for r in v['regions']: self.raw['region:' + r['id']] = r
            for m in p['measures']: self.measures[m['id']] = m

    def overview_score(self):
        key = self.data['key_analysis']
        value = {'duration':self.data['score']['duration'],
            'part_count':len(self.parts),'event_count':sum(len(p['events']) for p in self.parts.values()),
            'options_ref':'options:effective','normalization_ref':'normalization:score',
            'source_assumptions_ref':'source:assumptions','source_limitations_ref':'source:limitations',
            'key':{k:key[k] for k in ('status','selected_key','selection_method','confidence','reason_codes')},
            'key_ref':'key:global','style_ref':'style:global' if self.data['style_analysis'] else None,
            'assumption_refs':['assumption:'+a['id'] for a in self.data['assumptions']],
            'limitation_refs':['limitation:'+l['id'] for l in self.data['limitations']]}
        if len(canonical(self.data['options']).encode()) <= 2048:
            value['effective_options'] = self.data['options']
        return value

    def materialize(self, ref):
        if ref in self.items: return
        if ref not in self.raw:
            raise ReviewFailure('unavailable_evidence', 'A required record is not present in this snapshot.', ref)
        value = self.raw[ref]
        deps = references(value)
        if ref == 'overview:score':
            deps.update(v for k,v in value.items() if k.endswith('_ref') and isinstance(v,str))
            deps.update(value['assumption_refs']); deps.update(value['limitation_refs'])
        def encode(v, path):
            if isinstance(v, dict): return {k:encode(child, path + '/' + k.replace('~','~0').replace('/','~1')) for k,child in v.items()}
            if isinstance(v, list):
                if len(v) > 12 or len(canonical(v).encode()) > 2048:
                    collection = ref + '#' + path
                    children = []
                    for index, child in enumerate(v):
                        ident = collection + '/' + str(index)
                        self.raw[ident] = child if isinstance(child, dict) else {'value': child}
                        children.append(ident)
                    deps.update(children)
                    return {'collection_ref': collection, 'total_items':len(v), 'item_refs_pattern':collection + '/<zero-based-index>'}
                return [encode(child, path + '/' + str(i)) for i,child in enumerate(v)]
            return v
        encoded = encode(value, '')
        self.items[ref] = ReviewItem(record_ref=ref, data=encoded)
        self.links[ref] = sorted(deps - {ref})

    def closure(self, ref):
        if ref not in self.raw and '#' in ref:
            # Collections advertised in earlier pages can be resolved again in a fresh stateless request.
            prefix = ref.split('#',1)[0]
            if prefix in self.raw:
                self.materialize(prefix)
                while ref not in self.raw:
                    parents=[p for p in self.raw if ref.startswith(p+'#')]
                    if not parents: break
                    parent=max(parents,key=len)
                    if parent in self.items:break
                    self.materialize(parent)
        if ref not in self.raw: raise ReviewFailure('unknown_record','Unknown record reference for this snapshot.',ref)
        queue, seen, result = deque([ref, 'options:effective', 'metadata:analysis', 'normalization:score',
            'source:assumptions', 'source:limitations']), set(), []
        while queue:
            ident = queue.popleft()
            if ident in seen: continue
            seen.add(ident); self.materialize(ident); result.append(self.items[ident])
            queue.extend(self.links[ident])
        return result

    def locations(self, diagnostic):
        refs = references(diagnostic)
        mids = {r.split(':',1)[1] for r in refs if r.startswith('measure:')}
        for r in refs:
            if r.startswith('segment:') and r in self.raw: mids.add(self.raw[r]['location']['measure_id'])
            if r.startswith('event:') and r in self.raw:
                for sid in self.raw[r]['source_segment_ids']: mids.add(self.raw['segment:' + sid]['location']['measure_id'])
        return sorted((self.measures[m] for m in mids), key=lambda m:(m['part_id'],m['ordinal']))

    def matches(self, locations, args):
        return [m for m in locations if (args.part_id is None or m['part_id']==args.part_id)
            and (args.measure_index is None or m['ordinal']==args.measure_index)
            and (args.printed_measure_label is None or _label(m)==args.printed_measure_label)]

    def rows(self, args):
        if args.part_id is not None and args.part_id not in self.parts:
            raise ReviewFailure('unknown_voice', 'Unknown reference part ID.')
        if args.section == 'evidence': return self.closure(args.record_ref)
        if args.section == 'measures':
            return [ReviewItem(record_ref='measure:' + m['id'], data={**m, 'printed_label':_label(m),
                'part_label':self.parts[m['part_id']]['source_label']}) for m in self.matches(sorted(self.measures.values(),key=lambda m:(m['part_id'],m['ordinal'])),args)]
        if args.section == 'overview':
            overview = [ReviewItem(record_ref='overview:score', data=self.raw['overview:score'])]
            for c in self.data['coverage']:
                overview.append(ReviewItem(record_ref='coverage:'+c['id'], data={k:c[k] for k in ('id','rule_id','unit','status','candidate_count','eligible_count','checked_count','skipped_count','flagged_count','reason_codes')}))
            return overview
        def priority(d):
            loc = self.locations(d)
            start = d['scope']['start']
            time = Fraction(start['numerator'],start['denominator']) if start else Fraction(-1)
            return (d['severity']!='warning', {'theory_issue':0,'style_observation':1,'analysis_limitation':2}[d['category']],
                {'high':0,'medium':1,'low':2}[d['confidence']['level']], time, d['id'])
        findings=[]
        for d in sorted(self.data['diagnostics'],key=priority):
            loc = self.locations(d)
            if any(v is not None for v in (args.part_id,args.measure_index,args.printed_measure_label)) and not self.matches(loc,args):continue
            findings.append(ReviewItem(record_ref='diagnostic:'+d['id'], data={k:d[k] for k in ('id','rule_id','category','severity','confidence','explanation')} | {
                'support_ref':'diagnostic:'+d['id'],'location_count':len(loc),'location_preview_complete':len(loc)<=4,
                'location_preview':[{'part_id':m['part_id'],'measure_index':m['ordinal'],'printed_label':_label(m),'measure_id':m['id']} for m in loc[:4]]}))
        return findings


def project_review(report, args):
    # Owned model validation authenticates internal closure; the source hash and snapshot bind pages, not user authority.
    report = AnalysisReport.model_validate(report.model_dump(mode='json'))
    return _project_validated_review(report,args)


def _project_validated_review(report,args):
    """Project only a fully validated report still privately owned by the caller."""
    data = report.model_dump(mode='json'); snapshot = digest(data)
    if args.expected_snapshot is not None and args.expected_snapshot != snapshot:
        raise ReviewFailure('stale_snapshot','The source, effective options, policy or analysis result changed; begin a new overview.')
    selection = {k:getattr(args,k) for k in ('record_ref','part_id','measure_index','printed_measure_label')}
    binding = digest({'snapshot':snapshot,'section':args.section,'selection':selection})
    start = 0
    if args.cursor is not None:
        try:
            token = json.loads(base64.urlsafe_b64decode(args.cursor.encode()).decode())
            if set(token)!= {'binding','offset'} or token['binding']!=binding or type(token['offset']) is not int or token['offset']<=0:raise ValueError()
            start=token['offset']
        except (ValueError, TypeError, UnicodeError):
            raise ReviewFailure('invalid_cursor','Cursor does not match this snapshot and selection.') from None
    rows = RecordGraph(data).rows(args)
    if start > len(rows) or start == len(rows) and start != 0:
        raise ReviewFailure('invalid_cursor','Cursor is beyond the available records.')
    def page(end):
        next_cursor = None if end==len(rows) else base64.urlsafe_b64encode(canonical({'binding':binding,'offset':end}).encode()).decode()
        return ReviewPage(source_sha256=report.score.source_sha256,snapshot_id=snapshot,options_sha256=digest(data['options']),
            format=report.score.format,analysis_metadata=report.analysis_metadata,analysis_summary=report.summary,
            section=args.section,selection=selection,total_items=len(rows),start_index=start,returned_count=end-start,
            items=rows[start:end],next_cursor=next_cursor,section_exhausted=end==len(rows),
            evidence_complete_in_this_response=args.section=='evidence' and start==0 and end==len(rows))
    end = start
    for candidate in range(start+1, min(start+args.page_size,len(rows))+1):
        if len(page(candidate).canonical_json().encode())>args.max_bytes:break
        end=candidate
    result=page(end)
    if (end==start and start<len(rows)) or len(result.canonical_json().encode())>args.max_bytes:
        ref=rows[start].record_ref if start<len(rows) else None
        raise ReviewFailure('oversized_record','An indivisible record/envelope exceeds max_bytes; evidence is unavailable at this budget.',ref)
    return result


def verify_claim(report, claim):
    """Verify structured source assertions, not the truth of unrestricted AI prose."""
    from composerlint.review_models import ReviewClaim
    claim = ReviewClaim.model_validate(claim)
    data = report.model_dump(mode='json')
    if claim.snapshot_id != digest(data): raise ReviewFailure('stale_snapshot','Claim belongs to a different snapshot.')
    graph = RecordGraph(data)
    support = {i.record_ref for i in graph.closure('diagnostic:' + claim.diagnostic_id)}
    event_ref, segment_ref = 'event:'+claim.event_id, 'segment:'+claim.source_segment_id
    if event_ref not in support or segment_ref not in support:
        raise ReviewFailure('unavailable_evidence','Claim is not supported by this finding.')
    event, segment = graph.raw[event_ref], graph.raw[segment_ref]
    loc = segment['location']; measure = graph.measures[loc['measure_id']]
    pitch = event.get(claim.pitch_domain + '_pitch')
    spelling = None if pitch is None else pitch['step'] + ('#'*pitch['alteration'] if pitch.get('alteration',0)>0 else 'b'*(-pitch.get('alteration',0))) + str(pitch['octave'])
    expected = (loc['part_id'],loc['voice_id'],loc['measure_index'],_label(measure),spelling)
    actual = (claim.part_id,claim.voice_id,claim.measure_index,claim.printed_measure_label,claim.pitch)
    if segment['event_id'] != claim.event_id or actual != expected:
        raise ReviewFailure('unavailable_evidence','Claim pitch, voice, part or measure disagrees with the source.')
    return True
