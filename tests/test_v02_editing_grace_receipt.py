"""Untouched original grace notation: receipt checks, not grace editing support.

All MusicXML inputs are authored original controls. Negative controls alter the
MEI receipt only; no edited MusicXML or candidate is generated.
"""
import xml.etree.ElementTree as ET

import pytest
from composerlint import service
from composerlint.editing_source import resolve
from composerlint.editing_verify import verify_mei
from composerlint.renderers.verovio import render
from composerlint.viewer_source import source_index, aligned_range, ViewerFailure


@pytest.fixture(scope='module')
def original(tmp_path_factory):
    path=tmp_path_factory.mktemp('original-grace')/'authored.musicxml'
    data=b'''<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Authored line</part-name></score-part></part-list>
    <part id="P1"><measure number="1"><attributes><divisions>4</divisions><time><beats>1</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>
    <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>quarter</type></note></measure>
    <measure number="2"><note><grace/><pitch><step>E</step><alter>-1</alter><octave>4</octave></pitch><voice>1</voice><type>16th</type></note>
    <note><grace slash="yes"/><pitch><step>F</step><octave>4</octave></pitch><voice>1</voice><type>eighth</type><dot/></note>
    <note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>quarter</type></note></measure></part></score-partwise>'''
    path.write_bytes(data)
    report=service.analyze_score(path)
    root,measures,notes=source_index(data,report.score.model_dump(mode='json'))
    xml,selected,required=aligned_range(root,measures,notes,['p0001'],'p0001',1,2,bounded=False)
    receipt=render(xml,width=1800)
    return path,data,report,required,receipt['mei']


def test_original_grace_shapes_zero_timing_and_unchanged_source(original):
    path,data,report,notes,mei=original
    result=verify_mei(data,mei,notes,report.score)
    assert result['checked_source_segments']==4 and result['checked_grace_segments']==2
    assert result['grace_policy']=='unchanged_inspection_only_zero_rhythmic_duration'
    assert path.read_bytes()==data


@pytest.mark.parametrize('change',['missing_grace','wrong_grace','shape','dots','positive_time','missing_time','pitch','accidental','ordinary_as_grace','ordinary_duration','missing_id'])
def test_corrupt_rendered_receipt_still_refuses(original,change):
    _,data,report,notes,mei=original
    root=ET.fromstring(mei)
    by_id={e.get('{http://www.w3.org/XML/1998/namespace}id'):e for e in root.iter()}
    ordinary,grace,slashed=[by_id[k] for k in notes][:3]
    if change=='missing_grace':grace.attrib.pop('grace')
    if change=='wrong_grace':grace.set('grace','unacc')
    if change=='shape':grace.set('dur','8')
    if change=='dots':slashed.set('dots','0')
    if change=='positive_time':grace.set('dur.ppq','1')
    if change=='missing_time':grace.attrib.pop('dur.ppq')
    if change=='pitch':grace.set('pname','f')
    if change=='accidental':grace.set('accid.ges','n')
    if change=='ordinary_as_grace':ordinary.set('grace','acc')
    if change=='ordinary_duration':ordinary.set('dur','8')
    if change=='missing_id':grace.attrib.pop('{http://www.w3.org/XML/1998/namespace}id')
    with pytest.raises(ViewerFailure,match='Rendered|rendered|Source note missing'):
        verify_mei(data,ET.tostring(root,encoding='unicode'),notes,report.score)


def test_grace_remains_ineligible_edit_target(original):
    _,data,report,_,_=original
    target={'part_id':'p0001','lane_mode':'unique'}
    safe=resolve(data,report,{'start_measure_index':1,'end_measure_index':1},target)
    assert [n['ordinal'] for n in safe['notes']]==[1]
    with pytest.raises(ViewerFailure,match='Grace'):
        resolve(data,report,{'start_measure_index':2,'end_measure_index':2},target)


def test_unmodeled_grace_performance_attributes_remain_unsupported(original):
    _,data,report,notes,mei=original
    # Detached input for admission only; never write or render changed MusicXML.
    root=ET.fromstring(data);root.find('.//grace').set('steal-time-following','20')
    with pytest.raises(ViewerFailure,match='grace classification'):
        verify_mei(ET.tostring(root),mei,notes,report.score)
