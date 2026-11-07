"""Focused compatibility and work-count checks for ledger key membership."""
from dataclasses import asdict, replace
from fractions import Fraction
from pathlib import Path

import pytest

from composerlint.engine import musical
from composerlint.engine.musical import EvaluationUnit, Ledger, unit_key, unit_sort_key
from composerlint.review import canonical
from composerlint.service import analyze_score


def legacy_add(self, key, eligible, checked=None, flagged=False, reason=None,
               evidence=(), sort_time=None):
    """The pre-optimization implementation, retained only as a test oracle."""
    checked = eligible if checked is None else checked
    if any(u.key == key for u in self.units):
        raise ValueError('duplicate ledger unit')
    if checked and not eligible or flagged and not checked or not checked and not reason:
        raise ValueError('invalid ledger decision')
    self.units.append(musical.EvaluationUnit(
        key, eligible, checked, flagged, reason, tuple(evidence), unit_sort_key(key, sort_time)))


def key(n):
    return unit_key('pitched_event', f'p0001:v0001:e{n:06d}')


def insert(add, ledger, n, **kwargs):
    add(ledger, key(n), sort_time=(Fraction(n), Fraction(n + 1)), **kwargs)


def test_units_counts_order_and_serialization_match_legacy():
    ledgers = []
    for add in (legacy_add, Ledger.add):
        ledger = Ledger('range_issue')
        insert(add, ledger, 3, eligible=False, reason='missing_range')
        insert(add, ledger, 1, eligible=True, flagged=True)
        insert(add, ledger, 2, eligible=True)
        with pytest.raises(ValueError, match='^duplicate ledger unit$'):
            insert(add, ledger, 1, eligible=False)
        ledgers.append(ledger)
    old, new = ledgers
    assert type(new.units) is list
    assert asdict(old) == asdict(new)
    assert repr(old) == repr(new)
    assert [u.key for u in new.units] == [key(3), key(1), key(2)]
    assert canonical(old.coverage().model_dump(mode='json')) == canonical(new.coverage().model_dump(mode='json'))
    assert set(asdict(new)) == {'rule', 'units'}


def test_initialization_isolation_and_aliased_mutation_match_legacy():
    outcomes = []
    for add in (legacy_add, Ledger.add):
        seed = EvaluationUnit(key(1), True, True)
        supplied = [seed, seed]  # Construction historically permits duplicates.
        ledger = Ledger('range_issue', supplied)
        assert ledger.units is supplied
        with pytest.raises(ValueError, match='^duplicate ledger unit$'):
            insert(add, ledger, 1, eligible=True)
        supplied[:] = [replace(seed, key=key(2))]
        insert(add, ledger, 1, eligible=True)
        separate = Ledger('range_issue')
        insert(add, separate, 1, eligible=True)
        empty = Ledger('range_issue')
        assert empty.units == []
        outcomes.append((asdict(ledger), asdict(separate)))
    assert outcomes[0] == outcomes[1]


def test_escaped_or_replaced_list_cannot_leave_stale_membership():
    outcomes = []
    for add in (legacy_add, Ledger.add):
        ledger = Ledger('range_issue')
        insert(add, ledger, 1, eligible=True)
        alias = ledger.units
        alias[0] = replace(alias[0], key=key(2))  # Same length, different keys.
        insert(add, ledger, 1, eligible=True)
        with pytest.raises(ValueError, match='^duplicate ledger unit$'):
            insert(add, ledger, 2, eligible=True)
        alias.clear()
        insert(add, ledger, 2, eligible=True)
        replacement = [replace(alias[0], key=key(3))]
        ledger.units = replacement
        insert(add, ledger, 2, eligible=True)
        replacement[0] = replace(replacement[0], key=key(4))
        insert(add, ledger, 3, eligible=True)
        outcomes.append(asdict(ledger))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize('failure', ['decision', 'sort', 'evidence', 'append', 'append_after'])
def test_failed_insertions_and_recovery_match_legacy(monkeypatch, failure):
    outcomes = []
    for add in (legacy_add, Ledger.add):
        ledger = Ledger('range_issue')
        if failure == 'decision':
            with pytest.raises(ValueError, match='^invalid ledger decision$'):
                insert(add, ledger, 1, eligible=False)
        elif failure == 'sort':
            def fail(*args, **kwargs):
                raise ValueError('sort failed')
            # Both implementations resolve the same module-level helper.
            with monkeypatch.context() as patch:
                patch.setattr(musical, 'unit_sort_key', fail)
                patch.setitem(legacy_add.__globals__, 'unit_sort_key', fail)
                with pytest.raises(ValueError, match='^sort failed$'):
                    insert(add, ledger, 1, eligible=True)
        elif failure == 'evidence':
            def broken():
                yield 'first'
                raise ValueError('evidence failed')
            with pytest.raises(ValueError, match='^evidence failed$'):
                insert(add, ledger, 1, eligible=True, evidence=broken())
        else:
            class RejectAppend(list):
                def append(self, unit):
                    if failure == 'append_after':
                        super().append(unit)
                    raise ValueError('append failed')
            # Inject append failure without discarding the optimized index.
            object.__setattr__(ledger, 'units', RejectAppend())
            with pytest.raises(ValueError, match='^append failed$'):
                insert(add, ledger, 1, eligible=True)
            if failure == 'append_after':
                with pytest.raises(ValueError, match='^duplicate ledger unit$'):
                    insert(add, ledger, 1, eligible=True)
            ledger.units = []
        insert(add, ledger, 1, eligible=True)
        outcomes.append(asdict(ledger))
    assert outcomes[0] == outcomes[1]


def test_unhashable_string_subclass_preserves_legacy_equality():
    class UnhashableString(str):
        __hash__ = None
    outcomes = []
    for add in (legacy_add, Ledger.add):
        ledger = Ledger('range_issue')
        add(ledger, UnhashableString(key(1)), True)
        with pytest.raises(ValueError, match='^duplicate ledger unit$'):
            insert(add, ledger, 1, eligible=True)
        outcomes.append(asdict(ledger))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize('count', [128, 512])
def test_duplicate_check_work_does_not_scan_prior_units(monkeypatch, count):
    reads = [0]
    class CountedUnit(EvaluationUnit):
        def __getattribute__(self, name):
            if name == 'key':
                reads[0] += 1
            return object.__getattribute__(self, name)
    monkeypatch.setattr(musical, 'EvaluationUnit', CountedUnit)
    observed = []
    for add in (legacy_add, Ledger.add):
        ledger = Ledger('range_issue')
        reads[0] = 0
        for n in range(count):
            insert(add, ledger, n, eligible=False, reason='missing_range')
        observed.append(reads[0])
    assert observed == [count * (count - 1) // 2, 0]


FIXTURES = Path(__file__).parent / 'fixtures'
@pytest.mark.parametrize('source,options', [
    ('m3/fifths_ties.musicxml', {}),
    ('m3/fifths_async.musicxml', {}),
    ('m3/fifths_spelling.musicxml', {}),
    ('m3/fifths_unrelated_voice.musicxml', {}),
    ('m3/octaves_enharmonic.musicxml', {}),
    ('m3/crossing_chordal.musicxml', {'voice_order': ['p0001:v0001', 'p0002:v0001']}),
    ('m3/crossing_positive.musicxml', {'voice_order': ['p0001:v0001', 'p0002:v0001']}),
    ('m3/range_chord_tie.musicxml', {'ranges': {'p0001': {'minimum': 60, 'maximum': 84}}}),
    ('m3/chords_unknown_lower.musicxml', {}),
    ('m3/chords_spelling.musicxml', {}),
    ('m3/seventh_positive_up.musicxml', {}),
    ('m3/cadence_silence_exact.musicxml', {}),
    ('invalid_ties.musicxml', {}),
    ('transposing.musicxml', {}),
    ('piano_multivoice.musicxml', {}),
])
def test_complete_canonical_reports_match_legacy(monkeypatch, source, options):
    optimized = analyze_score(FIXTURES / source, options).canonical_json()
    with monkeypatch.context() as patch:
        patch.setattr(Ledger, 'add', legacy_add)
        original = analyze_score(FIXTURES / source, options).canonical_json()
    assert optimized == original
