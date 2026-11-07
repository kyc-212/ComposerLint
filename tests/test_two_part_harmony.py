"""Eight small musical cases; source arithmetic specified independently of E12."""
from copy import deepcopy
import xml.etree.ElementTree as ET
import pytest
from composerlint.engine.evaluation import compute, assemble
from composerlint.evaluation_models import EvaluationArguments, EvaluationQuery, EvaluationFailure
from composerlint.evaluation_review import EvaluationGraph, project_evaluation, validate_evaluation
from composerlint.review import digest
from composerlint.service import analyze_score
from v02_advanced_helpers import write_score

REF = {'tonic': 'C', 'collection': 'major', 'provenance': 'user_declared',
       'wording': 'Compare conditionally to C major; do not infer a local key.'}
PAIR = {'upper_voice_id': 'p0001:v0001', 'lower_voice_id': 'p0002:v0001'}


def run(path, reference=None, **extra):
    base = digest(analyze_score(path).model_dump(mode='json'))
    query = dict(expected_base_snapshot=base, two_part=PAIR,
                 analysis_options={'neighbor_measures': 0, 'pattern_min_attacks': 8, 'pattern_max_attacks': 8}, **extra)
    if reference is not None: query['analysis_context'] = reference
    return compute(path, query=query)


def records(bundle, relation=None):
    return [o for o in bundle.snapshot.indicators if o.feature == 'E12'
            and (relation is None or o.values.relation == relation)]


def arrival_score(path, upper_target='C5', third=None):
    parts = [{'bars': [[('B4', 2), (upper_target, 2)]]},
             {'bars': [[('G2', 2), ('C3', 2)]]}]
    if third: parts.append({'bars': [[(third, 4)]]})
    write_score(path, parts)


def test_1_contrary_semitones_are_local_resolution_not_prepared_suspension(tmp_path):
    p = tmp_path/'motion.musicxml'
    write_score(p, [{'bars': [[('F5', 2), ('E5', 2)]]}, {'bars': [[('B3', 2), ('C4', 2)]]}])
    before = p.read_bytes(); b = run(p, refinement='v02_classical_refinement_v1')
    row = records(b, 'motion')[0]; v = row.values
    assert (v.upper_motion.semitones, v.bass_motion.semitones) == (-1, 1)
    assert (v.source_interval.name, v.source_interval.semitones) == ('d12', 18)
    assert (v.target_interval.name, v.target_interval.semitones) == ('M10', 16)
    assert v.motion == 'contrary' and v.interpretation == 'pair_interval_resolution'
    assert v.interpretation_state == 'supported_under_policy'
    assert not any(o.values.kind == 'dissonance_relationship' and o.values.relation == 'suspension' for o in b.snapshot.indicators)
    assert not v.harmonic_function_inferred and not v.cadence_confirmed and p.read_bytes() == before
    # Rehashed false arithmetic must still fail semantic reconstruction.
    fake = deepcopy(b); r = records(fake, 'motion')[0]; old = r.id
    r.values.upper_motion.semitones = -2
    r.id = 'eval_indicator:' + digest({'source': fake.snapshot.source_sha256, **r.model_dump(mode='json', exclude={'id'})})
    cv = next(c for c in fake.snapshot.coverage if c.feature == 'E12')
    next(u for u in cv.units if u.id == 'eval_unit:' + digest(old)).id = 'eval_unit:' + digest(r.id)
    fake.snapshot.evaluation_snapshot_id = digest(fake.snapshot.model_dump(mode='json', exclude={'evaluation_snapshot_id'}))
    with pytest.raises(EvaluationFailure): validate_evaluation(fake)


def test_2_reference_relative_dyadic_arrival_is_conditional_and_default_unchanged(tmp_path):
    p = tmp_path/'arrival.musicxml'; arrival_score(p)
    b = run(p, REF); v = records(b, 'arrival_approach')[0].values
    assert v.degree_cues == {'lower': [5, 1], 'upper': [7, 1]}
    assert (v.bass_motion.semitones, v.upper_motion.semitones) == (5, 1)
    assert v.articulation_cues == ['explicit_ending_barline', 'score_end']
    assert v.interpretation == 'reference_relative_arrival_candidate' and v.interpretation_state == 'supported_under_policy'
    assert not v.cadence_confirmed and all(h.chord_label is None for h in b.advanced.report.harmony)
    no_reference = run(p); n = records(no_reference, 'arrival_approach')[0].values
    assert n.interpretation_state == 'tentative' and 'tonal_reference_not_declared' in n.reasons
    q = no_reference.snapshot.requested_query.model_copy(update={'two_part': None})
    original = assemble(no_reference.advanced, q)
    assert 'two_part' not in q.model_dump(mode='json') and 'two_part_harmony' not in original.snapshot.versions
    assert [o for o in no_reference.snapshot.indicators if o.feature != 'E12'] == original.snapshot.indicators
    assert no_reference.snapshot.styles == original.snapshot.styles and no_reference.snapshot.ratings == original.snapshot.ratings
    assert 'two_part' not in EvaluationQuery().model_dump(mode='json')


def test_3_crop_and_full_source_continuation_cannot_supply_an_arrival(tmp_path):
    p = tmp_path/'continuing.musicxml'
    write_score(p, [{'bars': [[('B4', 2), ('C5', 2)], [('D5', 4)]]},
                    {'bars': [[('G2', 2), ('C3', 2)], [('G2', 4)]]}])
    b = run(p, REF, scope={'kind': 'measure_range', 'reference_part_id': 'p0001', 'start_measure': 1, 'end_measure': 1})
    v = records(b, 'arrival_approach')[0].values
    assert v.interpretation_state == 'unavailable' and v.continuation_event_ids
    assert 'no_actual_articulation_cue' in v.reasons and 'full_source_continues_at_endpoint' in v.reasons


def test_4_filtered_principal_pair_retains_every_other_sounding_note_and_closure(tmp_path):
    p = tmp_path/'extra.musicxml'; arrival_score(p, third='D2')
    b = run(p, REF, scope={'kind': 'measure_range', 'reference_part_id': 'p0001', 'start_measure': 1, 'end_measure': 1,
                          'voice_ids': ['p0001:v0001', 'p0002:v0001']})
    row = records(b, 'arrival_approach')[0]; v = row.values
    outsider = 'p0003:v0001:e000001'
    assert all(outsider in cell.event_ids for cell in v.context_cells)
    assert outsider in [s.event_id for s in row.support] and outsider not in b.advanced.snapshot.resolved_scope.primary_event_ids
    assert v.lower_motion is not None and v.bass_motion is None and v.lower_is_sounding_bass is False
    assert v.interpretation_state == 'tentative' and 'additional_sounding_lanes_retained' in v.reasons
    args = dict(path=str(p), **b.snapshot.requested_query.model_dump(mode='json'), section='evidence', record_ref=row.id,
                expected_evaluation_snapshot=b.snapshot.evaluation_snapshot_id, page_size=50, max_bytes=32768)
    seen = []; first = None
    while True:
        page = project_evaluation(b, EvaluationArguments(**args)); first = first or page
        assert page.start_index == len(seen); seen.extend(page.items)
        if not page.next_cursor: break
        args['cursor'] = page.next_cursor
    refs = {x.record_ref for x in seen}
    assert len(seen) == len(refs) == first.total_items
    assert 'event:' + outsider in refs and any(r.startswith('segment:p0003:') for r in refs)
    with pytest.raises(EvaluationFailure):
        project_evaluation(b, EvaluationArguments(**{**args, 'expected_evaluation_snapshot': '0'*64}))


def test_5_unknown_omitted_occupant_blocks_dependent_interpretation(tmp_path):
    p = tmp_path/'unknown.musicxml'; arrival_score(p, third='D#2')
    root = ET.fromstring(p.read_bytes()); root.find('./part[@id="P3"]//alter').text = '0.5'
    p.write_bytes(ET.tostring(root)); b = run(p, REF, scope={'kind': 'measure_range', 'reference_part_id': 'p0001',
                'start_measure': 1, 'end_measure': 1, 'voice_ids': ['p0001:v0001', 'p0002:v0001']})
    v = records(b, 'arrival_approach')[0].values
    assert v.pair_pitches_verified and not v.full_context_verified
    assert any(cell.unknown_event_ids for cell in v.context_cells)
    assert v.interpretation_state == 'unavailable' and 'full_sounding_context_unverified' in v.reasons


def test_6_upper_chord_does_not_invent_a_unique_melodic_continuation(tmp_path):
    p = tmp_path/'chord.musicxml'; arrival_score(p, upper_target=['E4', 'G4', 'C5'])
    b = run(p, REF); v = records(b, 'arrival_approach')[0].values
    assert v.bass_motion.semitones == 5 and v.upper_motion is None
    assert v.interpretation_state == 'tentative' and 'upper_continuation_ambiguous' in v.reasons
    assert v.target_harmony_refs and any(h.chord_label == 'C:major' for h in b.advanced.report.harmony)
    assert not v.harmonic_function_inferred and not v.cadence_confirmed


def suspension_score(path, reattack=False):
    write_score(path, [{'bars': [[('F5', 2)], [('F5', 1), ('E5', 1)]]},
                       {'bars': [[('D4', 2)], [('C4', 2)]]}], meter=(2, 4))
    if not reattack:
        root = ET.fromstring(path.read_bytes()); notes = root.findall('./part[@id="P1"]//note')
        ET.SubElement(notes[0], 'tie', type='start'); ET.SubElement(notes[1], 'tie', type='stop')
        path.write_bytes(ET.tostring(root))


def test_7_existing_E10_already_recognizes_prepared_dyadic_4_3(tmp_path):
    p = tmp_path/'prepared.musicxml'; suspension_score(p)
    b = run(p, refinement='v02_classical_refinement_v1')
    suspensions = [o.values for o in b.snapshot.indicators if o.feature == 'E10' and o.values.relation == 'suspension']
    assert len(suspensions) == 1 and suspensions[0].compound_intervals == [10, 11, 10]
    assert suspensions[0].semitones == [-1] and all(h.chord_label is None for h in b.advanced.report.harmony)


def test_8_reattacked_dyadic_dissonance_is_not_a_prepared_suspension(tmp_path):
    p = tmp_path/'reattack.musicxml'; suspension_score(p, reattack=True)
    b = run(p, refinement='v02_classical_refinement_v1')
    assert not any(o.feature == 'E10' and o.values.relation == 'suspension' for o in b.snapshot.indicators)
