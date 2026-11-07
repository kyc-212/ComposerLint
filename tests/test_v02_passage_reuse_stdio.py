"""One authored six-measure SDK acceptance slice; never opens a personal score."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import xml.etree.ElementTree as ET

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from composerlint.advanced_models import SharedPassagePage
from composerlint.review import canonical
from passage_trial_observer import SharedProofObserver,audited_events

ROOT=Path(__file__).parents[1]


def authored_horn_piano(path):
    root=ET.Element('score-partwise',version='4.0');headers=ET.SubElement(root,'part-list')
    for part,name in [('P1','Authored Horn in F'),('P2','Authored Piano')]:
        ET.SubElement(ET.SubElement(headers,'score-part',id=part),'part-name').text=name
    def add_note(measure,step,octave,duration,voice,staff=None,alter=None,ties=(),chord=False):
        note=ET.SubElement(measure,'note')
        if chord:ET.SubElement(note,'chord')
        pitch=ET.SubElement(note,'pitch');ET.SubElement(pitch,'step').text=step
        if alter is not None:ET.SubElement(pitch,'alter').text=str(alter)
        ET.SubElement(pitch,'octave').text=str(octave)
        ET.SubElement(note,'duration').text=str(int(duration*4))
        for typ in ties:ET.SubElement(note,'tie',type=typ)
        ET.SubElement(note,'voice').text=str(voice)
        ET.SubElement(note,'type').text={.5:'eighth',1:'quarter',2:'half',4:'whole'}[duration]
        if staff:ET.SubElement(note,'staff').text=str(staff)
        if ties:
            notations=ET.SubElement(note,'notations')
            for typ in ties:ET.SubElement(notations,'tied',type=typ)
    for part_id in ('P1','P2'):
        part=ET.SubElement(root,'part',id=part_id)
        for number in range(1,8):
            measure=ET.SubElement(part,'measure',number=str(number))
            if number==1:
                attr=ET.SubElement(measure,'attributes');ET.SubElement(attr,'divisions').text='4'
                meter=ET.SubElement(attr,'time');ET.SubElement(meter,'beats').text='4';ET.SubElement(meter,'beat-type').text='4'
                if part_id=='P1':
                    tr=ET.SubElement(attr,'transpose');ET.SubElement(tr,'diatonic').text='-4';ET.SubElement(tr,'chromatic').text='-7'
                else:ET.SubElement(attr,'staves').text='2'
            if part_id=='P1':
                if number in (4,5,6):
                    if number==4:
                        for step in ('C','D'):add_note(measure,step,5,1,1)
                    add_note(measure,'B',3,{4:2,5:4,6:1}[number],1,alter=-1,
                             ties={4:('start',),5:('stop','start'),6:('stop',)}[number])
                    if number==6:
                        for step in ('C','D','E'):add_note(measure,step,5,1,1)
                else:
                    for step in ('C','D','E','G'):add_note(measure,step,5,1,1)
            else:
                for step,octave in [('F',4),('G',4),('A',4),('C',5)]*2:add_note(measure,step,octave,.5,1,1)
                ET.SubElement(ET.SubElement(measure,'backup'),'duration').text='16'
                for k,step in enumerate(('F','A','C')):add_note(measure,step,3,4,1,2,chord=k>0)
                ET.SubElement(ET.SubElement(measure,'backup'),'duration').text='16'
                for step in ('F','C','A','C')*2:add_note(measure,step,4,.5,5,2)
    path.write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))


def test_actual_sdk_shared_evidence_and_observer(tmp_path):
    path=tmp_path/'authored.musicxml';authored_horn_piano(path);before=path.read_bytes()
    pid_file=tmp_path/'server.json';events=tmp_path/'process-events.jsonl'
    program='''import os,sys,json\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp()}))\ndef audit(event,args):\n if event=='subprocess.Popen':\n  with Path(sys.argv[2]).open('a') as f:f.write(json.dumps({'argv':list(args[1])})+'\\n')\nsys.addaudithook(audit)\nfrom composerlint.server import main\nmain()\n'''
    receipt={'kind':'authored_fixture_only','calls':[],'complete':False};started=time.monotonic()
    async def run():
        with (tmp_path/'server-stderr.txt').open('w+') as err:
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-c',program,str(pid_file),str(events)],cwd=ROOT,env={'PYTHONDONTWRITEBYTECODE':'1'}),errlog=err) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=30) as client:
                    await client.initialize();await client.list_tools()
                    receipt['initialization_seconds']=time.monotonic()-started
                    async def call(args):
                        assert len(receipt['calls'])<12
                        row=dict(section=args.get('section','source'),completed=False)
                        receipt['calls'].append(row)
                        t=time.monotonic();reply=await client.call_tool('get_advanced_analysis',args)
                        row.update(seconds=time.monotonic()-t,elapsed=time.monotonic()-started,is_error=reply.is_error)
                        assert not reply.is_error,reply
                        value=reply.structured_content;assert json.loads(reply.content[0].text)==value
                        page=SharedPassagePage.model_validate(value)
                        row.update(completed=True,timings=page.timings,
                            response_bytes=len(canonical(value).encode()),records=page.logical_returned_count)
                        assert row['response_bytes']<=32768
                        return page
                    common=dict(path=str(path),execution_policy='source_bound_passage_v1',delivery_policy='shared_evidence_v2')
                    source=await call(common)
                    args={**common,'section':'overview','expected_source_snapshot':source.source_snapshot_id,
                        'scope':dict(reference_part_id='p0001',start_measure=1,end_measure=6)}
                    overview=await call(args)
                    assert len(overview.finding_previews)>=2
                    receipt['first_verified_findings_seconds']=time.monotonic()-started
                    observer=SharedProofObserver();roots=[p['record_ref'] for p in overview.finding_previews]
                    args.update(section='evidence',record_refs=roots,expected_passage_snapshot=overview.passage_snapshot_id)
                    while True:
                        page=await call(args);observer.add(page)
                        if not page.next_cursor:break
                        args['cursor']=page.next_cursor
                    assert observer.complete
                    records={r['record_ref']:r['data'] for r in observer.rows}
                    audited=audited_events(records)
                    horn=[x for x in audited if x['part']['id']=='p0001']
                    assert horn and any(x['event']['written_pitch']['step']=='C' and x['event']['written_pitch']['octave']==5
                        and x['event']['sounding_pitch']['step']=='F' and x['event']['sounding_pitch']['octave']==4 for x in horn)
                    held=next(x for x in horn if len(x['segments'])==3)
                    assert held['event']['duration']=={'numerator':7,'denominator':1}
                    assert [s['location']['measure_index'] for s in held['segments']]==[4,5,6]
                    assert records['metadata:analysis']['global_musical_report_constructed'] is False
                    receipt.update(complete=True,relationship_count=len(roots),complete_records=len(observer.rows),
                        complete_proof_seconds=time.monotonic()-started,proof_sha256=page.proof_sha256,
                        uncompressed_proof_bytes=page.decoded_bytes,encoded_proof_bytes=page.encoded_total_bytes,
                        target_event_count=records['advanced:query']['resolved_scope']['occupancy_event_ids']['total_items'],
                        record_pages_v1_minimum=(len(observer.rows)+49)//50,
                        verified_audited_events=len(audited),source_hash=hashlib.sha256(before).hexdigest())
    try:asyncio.run(asyncio.wait_for(run(),timeout=230))
    finally:
        if pid_file.exists():
            info=json.loads(pid_file.read_text());receipt['server']=info
            try:os.killpg(info['pgid'],0)
            except ProcessLookupError:receipt['process_group_terminated']=True
            else:
                os.killpg(info['pgid'],signal.SIGKILL);receipt['process_group_terminated']=False
        receipt['elapsed_including_shutdown_seconds']=time.monotonic()-started
        receipt['score_unchanged']=path.read_bytes()==before
        receipt['musical_requests']=len(receipt['calls'])
        receipt['process_events']=[json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
        output=os.environ.get('COMPOSERLINT_REUSE_RECEIPT')
        if output:Path(output).write_text(json.dumps(receipt,indent=2))
    assert receipt['process_group_terminated'] and receipt['score_unchanged']
    assert len(receipt['process_events'])==1 and receipt['process_events'][0]['argv'][-1]=='--passage-session'
    assert sum(c['timings']['source_loads'] for c in receipt['calls'])==1
    assert sum(c['timings']['scoped_predicate_passes'] for c in receipt['calls'])==2
    assert sum(c['timings']['normalized_source_revalidations'] for c in receipt['calls'])==0
    assert sum(c['timings']['source_validation_passes'] for c in receipt['calls'])==1
    assert receipt['first_verified_findings_seconds']<30 and receipt['complete_proof_seconds']<90
    assert receipt['elapsed_including_shutdown_seconds']<240
