"""Validate discussion intentions against owned evidence, without transforming XML.

This is a workspace helper, not an analysis service or a mood-to-edit engine.
Codex supplies the musical reading; existing predicates supply its premises.
"""
from types import SimpleNamespace
from composerlint.review import digest
from composerlint.viewer_source import fail
from composerlint.editing_source import resolve

DEFAULT_LOCKS = [
    'All untargeted parts and lanes, and material outside the selected window.',
    'All note/rest identities, onsets, durations, rhythm and complete tie topology.',
    'Score structure, instrumentation, voices/staves, meter, key and transposition.',
    'All notation outside explicitly named pitch or simple marking intentions.',
]
FUTURE_CHECKS = [
    'No candidate exists: no expressive improvement or hard-lock preservation has been verified on changed music.',
    'M6.3 must independently verify exact changes, every lock, unrelated bytes, timing, ties and whole-source structure.',
    'M6.3 must reanalyze an actual candidate and verify its notation correspondence before any preview.',
    'Expression remains a conditional artistic reading, without a mood score or guaranteed emotional result.',
]


def context(store, arguments, path, options):
    """Private worker boundary: query only server-owned, freshly verified M6.1 state."""
    from composerlint.expressive_models import BriefArguments
    from composerlint.advanced_models import PassageArguments, AdvancedFailure
    from composerlint.passage_session import fresh_owned
    from composerlint.report_reuse import code_identity
    from composerlint.viewer_source import ViewerFailure
    args = BriefArguments.model_validate(arguments)
    draft = args.draft
    query = draft.evidence_query
    request = PassageArguments(path=path, options=options,
        section='overview', expected_passage_snapshot=draft.expected_passage_snapshot,
        **query.model_dump(mode='json'))
    # run verifies source/query seals and refuses absent/expired passage snapshots.
    store.run(request)
    _, owned = store.checked_source(request, {})
    verified = owned.passages[digest(query.model_dump(mode='json'))]
    try:
        result = validate(owned.source, verified.bundle.snapshot, verified.graph, args)
    except ViewerFailure as exc:
        raise AdvancedFailure(exc.code, exc.reason) from exc
    finally:
        # Failed intentions must not poison valid evidence materialized before refusal.
        verified.seal = store.sign(store.passage_state(verified))
        store.bound(next(k for k, value in store.sources.items() if value is owned))
    fresh_owned(owned.source)
    if store.clock() >= owned.expires or code_identity() != owned.source.code_id:
        raise AdvancedFailure('stale_snapshot', 'Evidence expired or implementation changed while checking the brief.')
    return result


def validate(source, snapshot, graph, args):
    """No transformation, reanalysis, renderer, candidate path or file writes."""
    draft, selection = args.draft, args.selection
    scope = selection.model_dump(mode='json')
    start, end = selection.start_measure_index, selection.end_measure_index
    if end - start > 3:
        fail('unsupported_brief_scope', 'M6.2 supports 1–4 contiguous measures. Select a smaller passage explicitly.')
    if (source.score.source_sha256, digest(source.score.options.model_dump(mode='json'))) != (
            args.expected_source_sha256, args.expected_options_sha256):
        fail('stale_snapshot', 'Evidence source/options differ from the workspace.')
    q = draft.evidence_query.scope
    if draft.creation_policy:
        if (q.reference_part_id != selection.reference_part_id or not max(1,start-3) <= q.start_measure < start
                or not end <= q.end_measure <= end+1 or q.end_measure-q.start_measure > 7):
            fail('incomplete_brief_context', 'Creation needs the target and 1–3 preceding measures, optionally one following measure, within eight measures.')
    elif (q.reference_part_id, q.start_measure, q.end_measure) != (selection.reference_part_id, start, end):
        fail('stale_selection', 'Evidence must cover this exact reference part and selected measure range.')
    if q.part_ids is not None or q.voice_ids is not None:
        fail('incomplete_brief_context', 'Retrieve the passage without part/lane filters; retain the complete musical setting.')
    parts = {p.id: p for p in source.score.parts}
    if selection.reference_part_id not in parts or args.target.part_id not in parts:
        fail('ambiguous_target', 'Name one exact source part from the workspace.')
    reference, part = parts[selection.reference_part_id], parts[args.target.part_id]
    if end > len(reference.measures) or end > len(part.measures):
        fail('invalid_selection', 'Target measures are outside the source.')
    a, b = reference.measures[start-1].start, reference.measures[end-1].end
    if (part.measures[start-1].start, part.measures[end-1].end) != (a, b):
        fail('unsupported_brief_scope', 'Reference and target measures are not time-aligned; clarify the target.')
    resolver=resolve
    if draft.creation_policy:
        from composerlint.editing_creation import resolve as resolver
    target = resolver(source.raw, SimpleNamespace(score=source.score), scope, args.target.model_dump(mode='json'))
    if len(target['voices']) != 1 or target['voices'][0]['identity'] not in ('explicit', 'implicit'):
        fail('ambiguous_target', 'M6.2 requires one reliable explicit source lane.')
    rows = {r['ordinal']: r for r in target['notes']}
    baselines, locked, bounds = [], {}, {}
    for lock in draft.protected_material:
        p = parts.get(lock.part_id)
        if p is None or lock.voice_id and lock.voice_id not in {v.id for v in p.voices}:
            fail('ambiguous_constraint', 'A protected part/lane does not resolve. Clarify the hard constraint.')
        segments = [s for s in p.source_segments if a.fraction() <= s.original_onset.fraction() < b.fraction()
                    and (lock.voice_id is None or s.location.voice_id == lock.voice_id)]
        events = {e.id: e for e in p.events}
        by_ordinal = {s.timing_provenance.source_note_ordinal: s for s in segments}
        if lock.notes:
            if len({n.source_note_ordinal for n in lock.notes}) != len(lock.notes):
                fail('ambiguous_constraint', 'A protected note identity is repeated.')
            segments = []
            for note in lock.notes:
                seg = by_ordinal.get(note.source_note_ordinal)
                if seg is None:
                    fail('ambiguous_constraint', 'Protected note is outside its confirmed part/lane/window.')
                check_note(note, events[seg.event_id].written_pitch)
                segments.append(seg)
        if not segments:
            fail('ambiguous_constraint', 'Protected material resolves to no source segments.')
        # Commit the exact unchanged source facts. Whole timing is also locked by default.
        facts = dict(segments=[dict(segment=s.model_dump(mode='json'), event=events[s.event_id].model_dump(mode='json')) for s in segments],
            markings=[m.model_dump(mode='json') for m in snapshot.expression_context.markings
                if m.part_id == p.id and ((lock.kind == 'dynamics' and m.kind == 'dynamic')
                    or (lock.kind == 'articulations' and m.kind == 'articulation'
                        and set(m.source_segment_ids).intersection(s.id for s in segments)))])
        baselines.append(dict(constraint=lock.model_dump(mode='json'), source_record_count=len(segments)+len(facts['markings']),
            baseline_sha256=digest(facts), status='source_baseline_bound_not_candidate_verified'))
        if p.id == part.id:
            locked.setdefault(lock.kind, set()).update(s.timing_provenance.source_note_ordinal for s in segments)
            if lock.kind == 'register_bounds':
                for seg in segments:
                    n = seg.timing_provenance.source_note_ordinal
                    lo, hi = bounds.get(n, (0, 127))
                    limits = (max(lo, lock.minimum_written_midi), min(hi, lock.maximum_written_midi))
                    if limits[0] > limits[1]:
                        fail('constraint_conflict', 'The explicit register bounds contradict one another.')
                    bounds[n] = limits
    evidence = []
    observations = {o.id: o for o in snapshot.observations}
    future = list(FUTURE_CHECKS)
    for direction in draft.directions:
        if len(set(direction.evidence_refs)) != len(direction.evidence_refs):
            fail('invalid_brief', 'Direction evidence references must be distinct.')
        # At least one actual analytical relationship in the passage, not just metadata or an adjective.
        related = [observations[r] for r in direction.evidence_refs if r in observations]
        target_events = {r['event_id'] for r in rows.values()}
        if draft.creation_policy:
            pitched={e.id for e in part.events if e.kind=='pitched'}
            target_events={s.event_id for s in part.source_segments if s.event_id in pitched
                and s.location.voice_id==target['voices'][0]['voice_id']
                and q.start_measure <= s.location.measure_index < start}
        if not any(o.fact.kind != 'note' and target_events.intersection(s.event_id for s in o.support) for o in related):
            fail('ungrounded_direction', 'Cite an existing musical relationship involving the actual target, with its complete premises.')
        for ref in direction.evidence_refs:
            closure = graph.evidence(ref)
            evidence.append(dict(direction_id=direction.direction_id, record_ref=ref,
                root_fact=closure[0].data, closure_record_count=len(closure),
                closure_sha256=digest([r.model_dump(mode='json') for r in closure]),
                status='source_premises_verified_interpretation_not_certified'))
        if draft.creation_policy:
            from composerlint.editing_creation import admit
            _, _, plans, _ = admit(source.raw, SimpleNamespace(score=source.score), scope,
                args.target.model_dump(mode='json'), [o.model_dump(mode='json') for o in direction.operations])
            for op,row in plans:
                ordinal=op['source']['source_note_ordinal']
                if ordinal in locked.get('timing',set()) | locked.get('pitches',set()):
                    fail('constraint_conflict','Creation intersects an explicit pitch or timing lock.')
                if ordinal in bounds:
                    from composerlint.editing_pitch import coordinates
                    if any(v['kind']=='pitched' and not bounds[ordinal][0] <= coordinates(v['written'])[1] <= bounds[ordinal][1] for v in op['results']):
                        fail('constraint_conflict','Created pitch exceeds protected register bounds.')
            continue
        if draft.rhythm_policy:
            from composerlint.editing_rhythm import admit
            _, rhythm_rows, plans, _ = admit(source.raw, SimpleNamespace(score=source.score), scope,
                args.target.model_dump(mode='json'), [o.model_dump(mode='json') for o in direction.operations])
            touched={r['segment'].timing_provenance.source_note_ordinal for _, rs, _ in plans for r in rs}
            if touched & locked.get('timing', set()):
                fail('constraint_conflict', 'Rhythm intentions intersect an explicit protected timing lock.')
            for op, rs, _ in plans:
                if op['kind']=='subdivide_note' and touched & locked.get('pitches', set()):
                    fail('constraint_conflict', 'Subdivision changes explicitly protected note identities.')
            continue
        pitch_ordinals, marked, mark_count = set(), set(), 0
        pitch_operations = []
        for operation in direction.operations:
            if operation.family not in draft.allowed_families:
                fail('constraint_conflict', 'The proposed direction uses an operation family the composer did not allow.')
            notes = operation.change.notes if operation.family == 'pitch_register' else (
                [operation.anchor] if operation.family == 'dynamics' else operation.notes)
            ordinals = set()
            for note in notes:
                row = rows.get(note.source_note_ordinal)
                if row is None:
                    fail('ambiguous_target', 'An intended operation names material outside this exact part/lane/window.')
                check_note(note, row['written'])
                if note.source_note_ordinal in ordinals:
                    fail('constraint_conflict', 'A note is repeated in one intended operation.')
                ordinals.add(note.source_note_ordinal)
            kind = 'pitches' if operation.family == 'pitch_register' else (
                'dynamics' if operation.family == 'dynamics' else 'articulations')
            if ordinals & locked.get(kind, set()):
                fail('constraint_conflict', 'The direction would change explicitly protected '+kind+'. Revise the direction or clarify the lock.')
            if operation.family == 'pitch_register':
                if pitch_ordinals & ordinals:
                    fail('constraint_conflict', 'Overlapping pitch intentions are not sequential changes.')
                pitch_ordinals |= ordinals
                pitch_operations.append(operation)
                # Check the stated interval/endpoint analytically; do not compile
                # an edit batch, generate replacement music or transform bytes.
                from composerlint.editing_pitch import coordinates, spelled
                change = operation.change
                for ordinal in ordinals:
                    old = rows[ordinal]
                    wd, wc = coordinates(old['written']); sd, sc = coordinates(old['sounding'])
                    if change.kind == 'transpose':
                        nd, nc = wd + change.diatonic_steps, wc + change.semitones
                    else:
                        nd, nc = coordinates(change.destination.model_dump(mode='json'))
                        if change.pitch_domain == 'sounding':
                            nd, nc = nd-(sd-wd), nc-(sc-wc)
                    spelled(nd, nc); spelled(nd+sd-wd, nc+sc-wc)
                    if ordinal in bounds and not bounds[ordinal][0] <= nc <= bounds[ordinal][1]:
                        fail('constraint_conflict', 'The intended pitch instruction contradicts the protected written-register bounds.')
                if ordinals & locked.get('register_bounds', set()):
                    future.append('M6.3 must independently check the selected written-register bounds against actual candidate pitches.')
            else:
                mark_count += len(ordinals)
                keys = {(kind, n) for n in ordinals}
                if marked & keys:
                    fail('constraint_conflict', 'Overlapping marking intentions need one unambiguous final instruction.')
                marked |= keys
                validate_marking(source, part, snapshot, rows, operation, ordinals)
                if operation.family == 'dynamics':
                    # A restoration can consume an additional operation within the 16-mark envelope.
                    mark_count += 1
                    future.append('Dynamics are conditional: M6.3 must prove a known effective baseline, exact scope and boundary restoration inside the window; refuse if unavailable.')
        if len(pitch_ordinals) > 64 or mark_count > 16:
            fail('unsupported_brief_scope', 'Use at most 64 pitch segments and 16 marking operations, including a reserved dynamic restoration.')
        for operation in pitch_operations:
            selected = {n.source_note_ordinal for n in operation.change.notes}
            for ordinal in selected:
                chain = {n for n, row in rows.items() if row['event_id'] == rows[ordinal]['event_id']}
                if not chain <= selected:
                    fail('boundary_tie', 'One pitch intention must name every segment of the complete tied event.')
            if operation.change.kind == 'replace':
                from composerlint.editing_pitch import coordinates
                # Only validate the instruction; do not compile a destination batch or change source bytes.
                for note in operation.change.notes:
                    old = rows[note.source_note_ordinal]
                    original = old['written'] if operation.change.pitch_domain == 'written' else old['sounding']
                    if coordinates(original) == coordinates(operation.change.destination.model_dump(mode='json')):
                        fail('no_pitch_change', 'The proposed replacement already matches the source pitch.')
    # Exact changed-note intentions live in the draft. Do not duplicate the entire
    # lane's notes in each brief; M6.3 must freshly resolve the immutable source.
    return dict(target={k:v for k,v in target.items() if k != 'notes'}, evidence=evidence, constraint_baselines=baselines,
        future_verification_required=list(dict.fromkeys(future)), default_preservation=(['Only selected whole-measure rests may become new notes and rhythms; exact measure occupancy is required.', 'All unrelated source bytes, other lanes, meter, voice/staff assignments and existing material remain unchanged.'] if draft.creation_policy else ['All timing outside explicitly authorized substitutions; fixed substitution boundaries.', 'Pitch order/spelling, untargeted material, meter, voices/staves and complete existing ties.'] if draft.rhythm_policy else DEFAULT_LOCKS),
        source_snapshot_id=source.source_snapshot_id, passage_snapshot_id=snapshot.passage_snapshot_id,
        code_identity=source.code_id)


def check_note(note, pitch):
    p = pitch.model_dump(mode='json') if hasattr(pitch, 'model_dump') else pitch
    if p is None or any(p[k] != v for k, v in note.expected_written.model_dump(mode='json').items()):
        fail('stale_note', 'Expected written pitch differs from its exact source note identity.')


def validate_marking(source, part, snapshot, rows, operation, ordinals):
    if operation.family == 'dynamics':
        from composerlint.editing_source import safe_xml
        xp = safe_xml(source.raw).findall('part')[part.ordinal-1]
        if len(part.voices) != 1 or len({n.findtext('staff') for n in xp.findall('measure/note')}) != 1:
            fail('unsupported_brief_scope', 'Simple dynamic proposals require a single-voice, single-staff part; do not imply lane-local dynamics.')
        return
    marks = snapshot.expression_context.markings
    for ordinal in ordinals:
        row = rows[ordinal]
        event = next(e for e in part.events if e.id == row['event_id'])
        if len(event.source_segment_ids) != 1:
            fail('unsupported_brief_scope', 'Articulation proposals on tied events are deferred; choose an eligible untied attack.')
        existing = [m for m in marks if m.kind == 'articulation' and row['segment_id'] in m.source_segment_ids]
        if any(m.status != 'located' or m.name not in ('staccato','tenuto','accent') for m in existing):
            fail('unsupported_brief_scope', 'Unknown or incompatible articulation context requires clarification.')
        names = [m.name for m in existing]
        if operation.action == 'remove':
            if names != [operation.symbol] or operation.replaces is not None:
                fail('constraint_conflict', 'Removing an articulation must name its unique existing symbol.')
        elif operation.replaces is not None:
            if names != [operation.replaces] or operation.replaces == operation.symbol:
                fail('constraint_conflict', 'Name the exact unique articulation being replaced.')
        elif names:
            fail('constraint_conflict', 'An existing articulation needs an explicit replacement, not an overlapping marking.')
