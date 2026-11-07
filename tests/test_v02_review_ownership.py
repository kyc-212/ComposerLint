"""Fresh overview ownership, full validation, and legacy projection parity."""
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from composerlint import service, review, report_reuse
from composerlint.analysis_models import AnalysisReport
from composerlint.musical_validation import validate_snapshot
from composerlint.review_models import ReviewArguments, ReviewFailure
from composerlint.tool_contracts import dispatch


F = Path(__file__).parent / 'fixtures'


def legacy_overview(path, options=None, **selection):
    """The previous path-based sequence, using the unchanged public boundaries."""
    args = ReviewArguments(path=str(path), **selection)
    report = service.analyze_score(path, options)
    page = review.project_review(report, args)
    report_reuse.publish(path, report)
    return page


def counted(call):
    codes = {AnalysisReport.integrity.__code__: 'integrity',
             validate_snapshot.__code__: 'semantic'}
    counts = dict.fromkeys(codes.values(), 0)
    previous = sys.getprofile()
    def observer(frame, event, value):
        if event == 'call' and frame.f_code in codes:
            counts[codes[frame.f_code]] += 1
    try:
        sys.setprofile(observer)
        result = call()
    finally:
        sys.setprofile(previous)
    return result, counts


def test_fresh_overview_has_one_complete_validation_instead_of_two():
    path = F / 'm3/fifths_positive.musicxml'
    old, before = counted(lambda: legacy_overview(path, page_size=50))
    new, after = counted(lambda: service.get_review_page(path, page_size=50))
    assert before == {'integrity': 2, 'semantic': 2}
    assert after == {'integrity': 1, 'semantic': 1}
    assert old.canonical_json() == new.canonical_json()


@pytest.mark.parametrize('route', ['public_projection', 'completed_report'])
def test_public_report_boundaries_still_fully_validate(route, monkeypatch):
    report = service.analyze_score(F / 'm3/fifths_positive.musicxml')
    before = report.canonical_json()
    def forbidden(*args, **kwargs):
        raise AssertionError('caller report must not be treated as a fresh source')
    monkeypatch.setattr(service, '_build_analysis_fields', forbidden)
    call = (lambda r: review.project_review(r, ReviewArguments(path='<caller>'))
            ) if route == 'public_projection' else service.get_review_page
    _, counts = counted(lambda: call(report))
    assert counts == {'integrity': 1, 'semantic': 1}
    assert report.canonical_json() == before
    mutations = []
    forged = report.model_copy(deep=True)
    forged.score.parts[0].events[0].source_segment_ids.clear()
    mutations.append(forged)
    mutations.append(report.model_copy(update={
        'summary': report.summary.model_copy(update={'diagnostic_count': 999})}))
    mutations.append(AnalysisReport.model_construct(**{
        **vars(report), 'options': report.options.model_copy(update={'voice_order': ['p9999:v0001']})}))
    for forged in mutations:
        with pytest.raises((ValidationError, ValueError)):
            call(forged)


@pytest.mark.parametrize('damage', ['nested_type', 'semantic', 'source'])
def test_fresh_field_assembly_cannot_bypass_validation(monkeypatch, damage):
    build = service._build_analysis_fields
    def altered(*args, **kwargs):
        fields = build(*args, **kwargs)
        if damage == 'nested_type':
            fields['summary'] = fields['summary'].model_copy(update={'diagnostic_count': True})
        elif damage == 'semantic':
            fields['score'].parts[0].events[0].source_segment_ids.clear()
        else:
            fields['score'] = fields['score'].model_copy(update={'source_sha256': '0' * 64})
        return fields
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid report reached private projection')
    monkeypatch.setattr(service, '_build_analysis_fields', altered)
    monkeypatch.setattr(review, '_project_validated_review', forbidden)
    with pytest.raises(ValidationError):
        service.get_review_page(F / 'm3/fifths_positive.musicxml')


def test_fresh_report_is_detached_from_all_builder_aliases(monkeypatch):
    path = F / 'm3/fifths_positive.musicxml'
    expected = legacy_overview(path, page_size=50).canonical_json()
    build = service._build_analysis_fields
    project = review._project_validated_review
    captured = {}
    def capture(*args, **kwargs):
        fields = build(*args, **kwargs)
        captured.update(fields)
        return fields
    def isolated(report, args):
        assert report.score is not captured['score']
        assert report.score.parts[0] is not captured['score'].parts[0]
        assert report.score.parts[0].events[0] is not captured['score'].parts[0].events[0]
        assert report.coverage is not captured['coverage']
        assert report.coverage[0] is not captured['coverage'][0]
        assert report.diagnostics is not captured['diagnostics']
        captured['score'].parts[0].events[0].source_segment_ids.clear()
        captured['coverage'].clear()
        return project(report, args)
    monkeypatch.setattr(service, '_build_analysis_fields', capture)
    monkeypatch.setattr(review, '_project_validated_review', isolated)
    assert service.get_review_page(path, page_size=50).canonical_json() == expected
    assert report_reuse.take(path)['report']['score']['parts'][0]['events'][0]['source_segment_ids']


@pytest.mark.parametrize('name', [
    'classical_intelligence/mozart_positive.musicxml',
    'classical_intelligence/beethoven_positive.musicxml',
    'classical_intelligence/romantic_positive.musicxml',
    'v02_m4/romantic_ambiguous.musicxml',
    'v02_m3/variants/solo_horn.musicxml',
    'v02_m3/trio.musicxml',
    'm3/fifths_ties.musicxml',
    'piano_multivoice.musicxml',
])
def test_overview_pages_cursors_and_evidence_match_old_path(name, monkeypatch):
    path = F / name
    source = path.read_bytes()
    reports = []
    project = review._project_validated_review
    def capture(report, args):
        reports.append(report.canonical_json().encode())
        return project(report, args)
    monkeypatch.setattr(review, '_project_validated_review', capture)
    old = legacy_overview(path, page_size=1)
    new = service.get_review_page(path, page_size=1)
    assert reports[0] == reports[1]
    assert new.canonical_json() == old.canonical_json()
    selection = dict(page_size=1, expected_snapshot=old.snapshot_id, cursor=old.next_cursor)
    assert service.get_review_page(path, **selection).canonical_json() == legacy_overview(path, **selection).canonical_json()
    report = service.analyze_score(path)
    args = dict(section='evidence', record_ref='event:' + report.score.parts[0].events[0].id,
                expected_snapshot=old.snapshot_id, page_size=50, max_bytes=32768)
    expected = review.project_review(report, ReviewArguments(path=str(path), **args))
    assert service.get_review_page(path, **args).canonical_json() == expected.canonical_json()
    assert path.read_bytes() == source


@pytest.mark.parametrize('error', ['snapshot', 'cursor'])
def test_overview_errors_match_old_path(error):
    path = F / 'basic.musicxml'
    snapshot = legacy_overview(path).snapshot_id
    selection = dict(expected_snapshot='0' * 64) if error == 'snapshot' else dict(expected_snapshot=snapshot, cursor='!')
    outcomes = []
    for call in (legacy_overview, service.get_review_page):
        with pytest.raises(ReviewFailure) as exc:
            call(path, **selection)
        outcomes.append((exc.value.code, exc.value.reason))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize('flag', ['already_validated', 'trusted', 'owned'])
def test_no_caller_controlled_validation_bypass(flag):
    out, error = dispatch('get_review_page', {'path': str(F / 'basic.musicxml'), flag: True})
    assert error and out.error.code == 'invalid_options'
