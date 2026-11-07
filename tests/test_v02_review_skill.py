"""Static/discovery and authored assertion checks; live activation is tested separately."""
from pathlib import Path
from composerlint.tool_contracts import TOOLS
ROOT=Path(__file__).parents[1]


def test_project_skill_single_authority_and_review_routing():
    source=ROOT/'skills/composer-review/SKILL.md';link=ROOT/'.agents/skills/composer-review'
    assert link.is_symlink() and (link/'SKILL.md').resolve()==source.resolve()
    text=source.read_text();assert text.startswith('---\nname: composer-review\n')
    assert 'Review my piece' in text.split('---',2)[1]
    assert all(t.name in text for t in TOOLS)
    assert all(x in text for x in ['engine','coverage','snapshot_id','printed_measure_label','next_cursor','potentially intentional','MIDI','Never edit the score'])
    assert 'Do not blindly pass printed 7 as `measure_index=7`' in text
    assert 'A1' in text and 'do not read development roadmaps' in text


def test_support_all_fixture_diagnostics():
    from composerlint.service import analyze_score
    from composerlint.review import RecordGraph
    for name in ['m3/fifths_positive.musicxml','m3/fifths_ties.musicxml','m3/seventh_positive_up.musicxml','m3/cadence_authentic_end.musicxml','m4/four_episodes.musicxml','ugly_ambiguous.mid']:
        report=analyze_score(ROOT/'tests/fixtures'/name)
        graph=RecordGraph(report.model_dump(mode='json'))
        for d in report.diagnostics:
            rows=graph.closure('diagnostic:'+d.id)
            assert len({r.record_ref for r in rows})==len(rows)
            assert rows[0].record_ref=='diagnostic:'+d.id
            assert set(graph.links[rows[0].record_ref])<=set(i.record_ref for i in rows)
