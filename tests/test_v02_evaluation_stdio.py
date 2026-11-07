"""Actual installed MCP SDK, read-only discovery/calls, complete selected closure and reconnect."""
from pathlib import Path
import asyncio,hashlib,json,sys
import jsonschema
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from composerlint.tool_contracts import TOOLS,TOOL_BY_NAME,input_schema,output_schema
ROOT=Path(__file__).parents[1];F=ROOT/'tests/fixtures/v02_m4'


def test_real_sdk_new_tool_and_unchanged_tools(tmp_path):
    sources=[F/'romantic_positive.musicxml',F/'romantic_ambiguous.musicxml']
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    async def run():
        snapshots=[]
        for repeat in range(2):
            with (tmp_path/f'stderr-{repeat}.txt').open('w+') as err:
                async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','composerlint.server'],cwd=ROOT,env={'PYTHONDONTWRITEBYTECODE':'1'}),errlog=err) as (r,w):
                    async with ClientSession(r,w,read_timeout_seconds=30) as client:
                        await client.initialize();listed=(await client.list_tools()).tools
                        assert [t.name for t in listed]==[t.name for t in TOOLS]
                        tool=next(t for t in listed if t.name=='get_musical_evaluation')
                        assert tool.input_schema==input_schema(TOOL_BY_NAME[tool.name])
                        assert tool.annotations.read_only_hint and tool.annotations.idempotent_hint and not tool.annotations.destructive_hint and not tool.annotations.open_world_hint
                        async def call(name,args):
                            response=await client.call_tool(name,args)
                            assert response.content[0].text==json.dumps(response.structured_content,sort_keys=True,separators=(',',':'),allow_nan=False)
                            jsonschema.validate(response.structured_content,output_schema(TOOL_BY_NAME[name]))
                            return response
                        base=await call('get_review_page',{'path':str(sources[0])});assert not base.is_error
                        q={'path':str(sources[0]),'expected_base_snapshot':base.structured_content['snapshot_id'],
                            'comparison':{'first':{'kind':'measure_range','reference_part_id':'p0001','start_measure':1,'end_measure':2},'second':{'kind':'measure_range','reference_part_id':'p0001','start_measure':3,'end_measure':4}},
                            'analysis_context':{'tonic':'C','collection':'major','provenance':'user_declared','wording':'Compare conditionally to C major.'},
                            'intent':{'wording':'I want a mapped development between these two spans.','goals':[{'category':'melodic','criterion':'mapped_development'}]}}
                        first=await call(tool.name,q);assert not first.is_error
                        sid=first.structured_content['evaluation_snapshot_id'];snapshots.append(sid)
                        detail=await call(tool.name,q|{'expected_evaluation_snapshot':sid,'section':'ratings','page_size':50});assert not detail.is_error
                        melodic=next(x for x in detail.structured_content['items'] if x['data']['category']=='melodic')
                        assert melodic['data']['label']=='Supported'
                        if repeat==0:
                            args=q|{'expected_evaluation_snapshot':sid,'section':'evidence','record_ref':melodic['record_ref'],'page_size':50,'max_bytes':32768}
                            seen=[]
                            while True:
                                p=await call(tool.name,args);assert not p.is_error
                                data=p.structured_content;assert data['start_index']==len(seen)
                                seen.extend(i['record_ref'] for i in data['items'])
                                if data['section_exhausted']:break
                                args['cursor']=data['next_cursor']
                            assert len(seen)==len(set(seen))==data['total_items']
                            assert any(x.startswith('event:') for x in seen) and any(x.startswith('transpose:') for x in seen)
                            stale=await call(tool.name,q|{'expected_evaluation_snapshot':'0'*64,'section':'style'});assert stale.is_error
                            no=await call(tool.name,{'path':str(sources[1]),'requested_claim':'overall_grade'});assert no.is_error and no.structured_content['error']['code']=='unsupported_claim'
            assert not (tmp_path/f'stderr-{repeat}.txt').read_text()
        assert snapshots[0]==snapshots[1]
    asyncio.run(run())
    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
