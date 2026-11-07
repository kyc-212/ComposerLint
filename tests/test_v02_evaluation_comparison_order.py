"""Authored comparison-order controls: covering bounds do not reorder A and B."""
from fractions import Fraction

import pytest

from composerlint.engine.evaluation import compute
from composerlint.evaluation_review import EvaluationGraph
from composerlint.tool_contracts import dispatch
from v02_advanced_helpers import write_score
from v02_evaluation_helpers import query_for, values


@pytest.fixture
def comparison_source(tmp_path):
    # Independent 4/4 specification, quarter-time zero at m1:
    # m1-4: 11 attacks / 16 quarters, weighted MIDI sum 1096 / 16.
    # m9-12: 10 attacks / 16 quarters, weighted MIDI sum 1149 / 16.
    # C4-D4-E4 from m1 returns inside m10; m9's G5-A5-B5 never
    # occurs in m1-4. Thus reversing the search changes its answer.
    # m3-4: 5 attacks / 8 quarters, register 589/8;
    # m1-2: 6 attacks / 8 quarters, register 507/8.
    quarters = lambda *pitches: [(p, 1) for p in pitches]
    opening = quarters('C4', 'D4', 'E4', 'G4')
    bars = [opening, [('C4', 2), ('G4', 2)],
            quarters('C5', 'D5', 'E5', 'G5'), [('C5', 4)]]
    bars += [[(None, 4)] for _ in range(4)]
    bars += [quarters('G5', 'A5', 'B5', 'F5'), opening,
             [('C5', 4)], [('C5', 4)]]
    path = tmp_path / 'comparison-order.musicxml'
    write_score(path, [{'bars': bars}])
    return path


@pytest.mark.parametrize('first,second,bounds,onsets,attacks,rates,registers,change,direction,forward_return', [
    ((9, 12), (1, 4), (0, 48), (32, 0), (10, 11),
     (Fraction(5, 8), Fraction(11, 16)),
     (Fraction(1149, 16), Fraction(137, 2)), Fraction(-53, 16), 'up', True),
    ((3, 4), (1, 2), (0, 16), (8, 0), (5, 6),
     (Fraction(5, 8), Fraction(3, 4)),
     (Fraction(589, 8), Fraction(507, 8)), Fraction(-41, 4), 'up', False),
], ids=['separated', 'adjacent'])
def test_reverse_comparison_locations_and_direction(
    comparison_source, first, second, bounds, onsets, attacks, rates,
    registers, change, direction, forward_return,
):
    path = comparison_source
    bundle = compute(path, query=query_for(path, first, second))
    score = bundle.advanced.report.score
    events = {e.id: e for p in score.parts for e in p.events}
    expected_measures = {f'p0001:m{m:04d}' for a, b in (first, second)
                         for m in range(a, b + 1)}
    indicators = [o for o in bundle.snapshot.indicators if o.feature in ('E04', 'E06')]
    assert {o.feature for o in indicators} == {'E04', 'E06'}
    for indicator in indicators:
        assert (indicator.span.start.fraction(), indicator.span.end.fraction()) == bounds
        assert {m for s in indicator.support for m in s.measure_ids} == expected_measures
        assert {s.event_id for s in indicator.support} == {
            e.id for e in events.values() if any(
                (a - 1) * 4 <= e.onset.fraction() < b * 4 for a, b in (first, second)
            )
        }
        closure = {r.record_ref for r in EvaluationGraph(bundle).closure(indicator.id)}
        assert {'measure:' + m for m in expected_measures} <= closure
        assert {'event:' + s.event_id for s in indicator.support} <= closure
    comparison = bundle.snapshot.requested_query.comparison
    assert (comparison.first.start_measure, comparison.first.end_measure) == first
    assert (comparison.second.start_measure, comparison.second.end_measure) == second
    passage = values(bundle, 'E04')[0]
    assert [events[group[0]].onset.fraction() for group in passage.opening_event_ids] == list(onsets)
    assert passage.complete and passage.search_complete and not passage.exact_return_found
    activity = values(bundle, 'E06')[0]
    assert activity.complete
    assert [s.attacks for s in activity.spans] == list(attacks)
    assert [s.attack_rate.fraction() for s in activity.spans] == list(rates)
    assert [s.weighted_register.fraction() for s in activity.spans] == list(registers)
    assert [s.occupied_fraction.fraction() for s in activity.spans] == [1, 1]
    assert activity.register_change.fraction() == change
    assert activity.activity_direction == direction
    # Explicitly swapping the request, unlike normalizing only its covering
    # span, must reverse directional metrics and the declared search roles.
    forward = compute(path, query=query_for(path, second, first))
    forward_activity = values(forward, 'E06')[0]
    assert [s.attacks for s in forward_activity.spans] == list(reversed(attacks))
    assert forward_activity.register_change.fraction() == -change
    assert forward_activity.activity_direction == 'down'
    assert values(forward, 'E04')[0].exact_return_found == forward_return


@pytest.mark.parametrize('first,second', [((9, 12), (1, 4)), ((3, 4), (1, 2))],
                         ids=['separated', 'adjacent'])
def test_reverse_comparison_public_worker_succeeds(comparison_source, first, second):
    path = comparison_source
    query = query_for(path, first, second)
    page, error = dispatch('get_musical_evaluation', {'path': str(path), **query})
    assert not error
    assert page.section == 'overview' and page.returned_count > 0
