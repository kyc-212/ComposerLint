"""Source-owned M4→M3→source closure and stateless bounded projection."""
import base64,json
from composerlint.review import canonical,digest
from composerlint.review_models import ReviewFailure
from composerlint.advanced_models import AdvancedItem
from composerlint.advanced_review import AdvancedGraph,validate_analysis
from composerlint.evaluation_models import EvaluationSnapshot,EvaluationPage,EvaluationFailure


def validate_evaluation(bundle,*,semantic=True):
    _validated_graph(bundle,semantic=semantic)
    return True


def _validated_graph(bundle,*,semantic=True):
    validate_analysis(bundle.advanced)
    s=EvaluationSnapshot.model_validate(bundle.snapshot.model_dump(mode='json'));a=bundle.advanced.snapshot
    if (s.source_sha256,s.base_snapshot_id,s.advanced_snapshot_id,s.options_sha256)!=(a.source_sha256,a.base_snapshot_id,a.advanced_snapshot_id,a.options_sha256):raise EvaluationFailure('unavailable_evidence','M4 ownership does not match its M3/base source.')
    if digest(s.requested_query.model_dump(mode='json'))!=s.query_sha256 or digest(s.model_dump(mode='json',exclude={'evaluation_snapshot_id'}))!=s.evaluation_snapshot_id:raise EvaluationFailure('unavailable_evidence','M4 query/content identity mismatch.')
    effective=s.requested_query.advanced_query()
    if effective!=a.requested_query:raise EvaluationFailure('unavailable_evidence','M4 query does not own this effective M3 scope/options.')
    graph=EvaluationGraph(bundle)
    allrows=[*s.indicators,*s.styles,*s.criteria,*s.ratings,*s.expressive,*s.feedback,*s.relationships,*s.diagnostics,*s.local_contexts]
    ids=[r.id for r in allrows]
    if len(ids)!=len(set(ids)):raise EvaluationFailure('unavailable_evidence','Duplicate M4 record ID.')
    for row in allrows:
        graph.materialize(row.id)
        if any(ref not in graph.raw for ref in graph.links[row.id]):raise EvaluationFailure('unavailable_evidence','Unresolved M4 evidence edge.',row.id)
    if semantic:
        # Reconstruct only the pure additive layer over validated owned M3, never reparse/reanalyze a crop.
        from composerlint.engine.evaluation import assemble
        expected=assemble(bundle.advanced,s.requested_query)
        if expected.snapshot!=s:raise EvaluationFailure('unavailable_evidence','M4 indicator/judgment differs from the bound policies and source evidence.')
    return graph


def _coverage_unit_index(units):
    index={}
    for position,unit in enumerate(units):
        index.setdefault(frozenset(unit.event_ids),[]).append((position,unit))
    return index


def _matching_units(index,event_sets):
    # Match the former set equality, retaining all duplicates in source order.
    matches=[entry for key in {frozenset(ids) for ids in event_sets} for entry in index.get(key,())]
    return [unit for _,unit in sorted(matches,key=lambda entry:entry[0])]


class EvaluationGraph(AdvancedGraph):
    def __init__(self,bundle):
        super().__init__(bundle.advanced);s=bundle.snapshot;self.evaluation=s;self.bundle=bundle
        # Presentation only: preserve M1/M3 source dictionaries and add the exact
        # per-part printed-label occurrence from the whole owned source traversal.
        for part in bundle.advanced.report.score.parts:
            counts={}
            for measure in part.measures:
                label=None if measure.original_number is None else measure.original_number+(measure.original_suffix or '')
                if label is not None:counts[label]=counts.get(label,0)+1
                ref='measure:'+measure.id
                self.raw[ref]={**self.raw[ref],'printed_label':label,'label_occurrence':counts[label] if label is not None else None}
        self.raw['evaluation:query']={'query':s.requested_query.model_dump(mode='json'),'versions':s.versions,'execution_complete':s.execution_complete,'limitations':s.limitations}
        self.extra_edges['evaluation:query']={'advanced:query'}
        if s.requested_query.analysis_context and s.requested_query.analysis_context.provenance=='inherited_global_exploratory':self.extra_edges['evaluation:query'].add('key:global')
        for cv in s.coverage:
            ref='evaluation_coverage:'+cv.feature;self.raw[ref]=cv.model_dump(mode='json');self.extra_edges[ref]={'evaluation:query'}
        # Claim-focused inherited coverage: retain exact global counts and the units
        # belonging to the asserted occurrence. Unrelated windows remain retrievable
        # in the unchanged advanced coverage interface, not forced into this claim.
        for cv in bundle.advanced.snapshot.coverage:
            if cv.feature!='patterns':continue
            for unit in cv.units:
                if unit.id!='unit:'+digest(['patterns',['window',unit.event_ids]]):continue
                ref='evaluation_window:'+unit.id
                self.raw[ref]={'unit':unit.model_dump(mode='json'),'unit_definition':cv.unit_definition,'policy':bundle.advanced.snapshot.policy}
                self.extra_edges[ref]={'advanced:query'}
        coverage={}
        for cv in bundle.advanced.snapshot.coverage:
            if cv.feature not in coverage:coverage[cv.feature]=(cv,_coverage_unit_index(cv.units))
        for observation in bundle.advanced.snapshot.observations:
            cv,index=coverage[observation.feature]
            sets=[{sup.event_id for sup in observation.support}]
            if observation.fact.kind=='pattern':sets=[set(x.event_ids) for x in observation.fact.occurrences]
            relevant=_matching_units(index,sets)
            ref='evaluation_m3_coverage:'+observation.id
            self.raw[ref]={**cv.model_dump(mode='json',exclude={'units'}),
                'relevant_units':[u.model_dump(mode='json') for u in relevant],
                'scope_notice':'Global feature counts retained; units delivered here are the exact asserted occurrence units. Other feature units are outside this claim.',
                'full_feature_ref':'advanced_coverage:'+cv.feature}
            self.extra_edges[ref]={'advanced:query'}
            self.extra_edges[observation.id].discard('advanced_coverage:'+cv.feature)
            self.extra_edges[observation.id].add(ref)
        for o in s.indicators:
            self.raw[o.id]=o.model_dump(mode='json')
            cv=next(cv for cv in s.coverage if cv.feature==o.feature)
            unit=next(u for u in cv.units if u.id=='eval_unit:'+digest(o.id))
            ref='evaluation_claim_coverage:'+o.id
            self.raw[ref]={**cv.model_dump(mode='json',exclude={'units'}),'claim_unit':unit.model_dump(mode='json'),'full_feature_ref':'evaluation_coverage:'+o.feature,
                'scope_notice':'Global counts and exact claim unit; unrelated derived units are separately retrievable.'}
            self.extra_edges[ref]={'evaluation:query'}
            edges=set(o.premise_ids)|{'evaluation:query',ref}
            for sup in o.support:
                edges|={'event:'+sup.event_id,'voice:'+sup.voice_id,'part:'+sup.part_id,'transpose:'+sup.event_id}
                edges.update('segment:'+x for x in sup.source_segment_ids);edges.update('measure:'+x for x in sup.measure_ids)
            self.extra_edges[o.id]=edges
        for row in [*s.styles,*s.criteria,*s.ratings,*s.expressive,*s.feedback]:
            self.raw[row.id]=row.model_dump(mode='json');edges={'evaluation:query'}
            for name in ('supporting_units','counterevidence','shared_units','indicator_ids','criterion_ids','countervailing_ids'):
                edges.update(getattr(row,name,[]))
            self.extra_edges[row.id]=edges
        recommended=[f.id for f in s.feedback[:3]]
        if not recommended:
            recommended=[o.id for o in s.indicators if o.feature in ('E04','E06')][:2] or [o.id for o in s.indicators[:2]]
        if s.requested_query.two_part:
            pairs=[o for o in s.indicators if o.feature=='E12']
            local=[o.id for o in pairs if o.values.interpretation=='pair_interval_resolution']
            arrivals=[o.id for o in pairs if o.values.relation=='arrival_approach']
            recommended=list(dict.fromkeys((local[:1] or [o.id for o in pairs[:1]])+arrivals[:1]))
        self.raw['evaluation:review']={'claim_refs':recommended,'notice':'Small recommended review bundle; read complete closure, including coverage.'}
        self.extra_edges['evaluation:review']=set(recommended)|{'evaluation:query'}
        indexes=[]
        for feature in [cv.feature for cv in s.coverage]:
            refs=[o.id for o in s.indicators if o.feature==feature];ref='evaluation_index:'+feature
            self.raw[ref]={'feature':feature,'total_items':len(refs),'record_refs':refs}
            indexes.append({'record_ref':ref,'feature':feature,'count':len(refs),'first_record_ref':refs[0] if refs else None})
        self.raw['evaluation:overview']={'style_summary':s.style_summary,'style_qualifiers':s.style_qualifiers,
            'families':[{'family':x.family,'state':x.state,'record_ref':x.id} for x in s.styles],
            'categories':[{'category':x.category,'state':x.state,'label':x.label,'reasons':x.reasons,'record_ref':x.id} for x in s.ratings],
            'indicator_indexes':indexes,'review_bundle_ref':'evaluation:review','feedback_total':s.feedback_total,'priority_feedback_refs':[f.id for f in s.feedback[:3]],
            'query_ref':'evaluation:query','execution_complete':s.execution_complete,'limitations':s.limitations,'notice':'No historical style match is a quality penalty. No overall grade. Overview is not evidence closure.'}
        self.extra_edges['evaluation:overview']={'evaluation:query'}
        for row in [*s.relationships,*s.diagnostics,*s.local_contexts]:
            self.raw[row.id]=row.model_dump(mode='json')
            edges=set(row.premise_ids)|{'evaluation:query'}
            if row.level in ('relationship_assessment','local_tonal_context'):
                if row.level=='relationship_assessment':edges.update(row.goal_criterion_ids)
                for sup in row.support:
                    edges|={'event:'+sup.event_id,'voice:'+sup.voice_id,'part:'+sup.part_id,'transpose:'+sup.event_id}
                    edges.update('segment:'+sid for sid in sup.source_segment_ids)
                    edges.update('measure:'+mid for mid in sup.measure_ids)
            self.extra_edges[row.id]=edges
        if s.evaluation_schema_version==2:
            overview=self.raw['evaluation:overview']
            explicit={r.id for r in s.ratings if r.criterion_ids}
            overview['categories']=[r for r in overview['categories'] if r['record_ref'] in explicit]
            overview['relationships']=[{'record_ref':r.id,'category':r.category,'criterion':r.criterion,'state':r.state,'priority_basis':r.priority_basis} for r in s.relationships]
            overview['diagnostics']=[{'record_ref':d.id,'cause':d.cause,'conclusion':d.conclusion} for d in s.diagnostics]
            if 'arrival' in s.requested_query.assessment_focus:
                overview['local_contexts']=[{'record_ref':x.id,'state':x.state,'reference':x.reference.model_dump(mode='json') if x.reference else None} for x in s.local_contexts]
            overview['notice']='Named relationship assessments use disclosed analytical criteria without inventing composer intention. Existing explicit-goal ratings retain their meanings. No overall grade; overview is not evidence closure.'
            recommended=[r.id for r in s.relationships] or [d.id for d in s.diagnostics[:3]]
            self.raw['evaluation:review']['claim_refs']=recommended
            self.extra_edges['evaluation:review']=set(recommended)|{'evaluation:query'}
        from composerlint.engine.composer_feedback import guide
        if guide(s.requested_query) is not None:self.raw['evaluation:overview']['composer_criteria']=guide(s.requested_query)

    def rows(self,args):
        s=self.evaluation
        if args.section=='claim':
            from composerlint.evaluation_projection import claim_rows
            return claim_rows(self.bundle,self,args.record_ref)
        if args.section=='evidence':refs=None
        elif args.section=='overview':refs=['evaluation:overview','evaluation:query']
        elif args.section=='coverage':refs=['evaluation_coverage:'+c.feature for c in s.coverage]
        elif args.section=='style':refs=[r.id for r in s.styles]
        elif args.section=='ratings':
            refs=[r.id for r in s.ratings]
            if s.evaluation_schema_version==2:
                refs=[r.id for r in s.relationships]+[d.id for d in s.diagnostics]+[x.id for x in s.local_contexts]+[r.id for r in s.ratings if r.criterion_ids]
        elif args.section=='expressive':refs=[r.id for r in s.expressive]
        else:refs=[r.id for r in s.feedback]
        if refs is None:return [AdvancedItem.model_validate(x.model_dump()) for x in self.closure(args.record_ref)]
        result=[]
        for ref in refs:self.materialize(ref);result.append(AdvancedItem.model_validate(self.items[ref].model_dump()))
        return result


def project_evaluation(bundle,args):
    graph=_validated_graph(bundle);s=bundle.snapshot
    if args.query().model_copy(update={'expected_base_snapshot':s.base_snapshot_id})!=s.requested_query:raise EvaluationFailure('stale_evaluation_snapshot','Projection query differs from the owned evaluation.')
    if args.expected_evaluation_snapshot is not None and args.expected_evaluation_snapshot!=s.evaluation_snapshot_id:raise EvaluationFailure('stale_evaluation_snapshot','Source/options/scope/reference/intent/policy changed; start a fresh overview.')
    binding_data={'snapshot':s.evaluation_snapshot_id,'section':args.section,'record_ref':args.record_ref,'page_size':args.page_size,'max_bytes':args.max_bytes}
    if args.section=='claim':
        from composerlint.evaluation_projection import POLICY
        binding_data['projection_policy']=POLICY
    binding=digest(binding_data)
    start=0
    if args.cursor:
        try:
            token=json.loads(base64.b64decode(args.cursor,altchars=b'-_',validate=True))
            if set(token)!={'binding','offset'} or token['binding']!=binding or type(token['offset']) is not int or token['offset']<=0:raise ValueError()
            start=token['offset']
        except (ValueError,TypeError,UnicodeError):raise EvaluationFailure('invalid_cursor','Cursor does not bind this source/query/section/budget.') from None
    try:rows=graph.rows(args)
    except ReviewFailure as exc:raise EvaluationFailure(exc.code,exc.reason,exc.record_ref) from exc
    if start>len(rows) or start==len(rows) and start!=0:raise EvaluationFailure('invalid_cursor','Cursor offset outside the complete section.')
    def page(end):
        cursor=None if end==len(rows) else base64.urlsafe_b64encode(canonical({'binding':binding,'offset':end}).encode()).decode()
        return EvaluationPage(evaluation_schema_version=s.evaluation_schema_version,evaluation_snapshot_id=s.evaluation_snapshot_id,source_sha256=s.source_sha256,base_snapshot_id=s.base_snapshot_id,advanced_snapshot_id=s.advanced_snapshot_id,options_sha256=s.options_sha256,query_sha256=s.query_sha256,versions=s.versions,
            section=args.section,total_items=len(rows),start_index=start,returned_count=end-start,items=rows[start:end],next_cursor=cursor,section_exhausted=end==len(rows),evidence_complete_in_this_response=args.section=='evidence' and start==0 and end==len(rows),
            **({'completeness_notice':'Claim projection: retrieve every page for the stated source facts and limitations. The complete snapshot is validated internally; the full verification closure remains in section=evidence. This is not full-closure delivery or a style/quality judgment.'} if args.section=='claim' else {'completeness_notice':'Version 2 uses named source-backed relationship assessments and optional real composer intention. Retrieve complete relevant support before musical claims; engine completion and delivery completeness are distinct. No overall grade.'} if s.evaluation_schema_version==2 else {}))
    end=start
    for candidate in range(start+1,min(start+args.page_size,len(rows))+1):
        if len(canonical(page(candidate).model_dump(mode='json')).encode())>args.max_bytes:break
        end=candidate
    result=page(end)
    if end==start and start<len(rows) or len(canonical(result.model_dump(mode='json')).encode())>args.max_bytes:raise EvaluationFailure('oversized_record','An indivisible atom/envelope exceeds max_bytes; use a larger budget or narrower query.',rows[start].record_ref if start<len(rows) else None)
    return result


def run(args):
    from composerlint.evaluation_runtime import page
    return page(args)
