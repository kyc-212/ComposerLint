"""Real stdio transport, independent processes and guarded read-only advanced operations."""
from pathlib import Path
import asyncio,json,sys,hashlib
import jsonschema
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from composerlint.tool_contracts import TOOLS,input_schema,output_schema
ROOT=Path(__file__).parents[1];F=ROOT/'tests/fixtures/v02_m3'


def test_actual_sdk_advanced_calls_and_reconnection(tmp_path):
    sources=[F/'solo.musicxml',F/'ambiguity.musicxml',F/'long.musicxml',F/'orchestra.musicxml'];before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    async def run():
        identities=[];calls=[]
        for repeat in range(2):
            with (tmp_path/f'stderr{repeat}.txt').open('w+') as err:
                async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','composerlint.server'],cwd=ROOT,env={'PYTHONDONTWRITEBYTECODE':'1'}),errlog=err) as (r,w):
                    async with ClientSession(r,w,read_timeout_seconds=30) as client:
                        await client.initialize();listed=(await client.list_tools()).tools
                        assert [t.name for t in listed]==[t.name for t in TOOLS]
                        new=listed[-1];assert new.input_schema==input_schema(TOOLS[-1]) and new.annotations.read_only_hint and new.annotations.idempotent_hint and not new.annotations.destructive_hint and not new.annotations.open_world_hint
                        async def call(args):
                            response=await client.call_tool('get_advanced_analysis',args)
                            assert response.content[0].text==json.dumps(response.structured_content,sort_keys=True,separators=(',',':'),allow_nan=False)
                            jsonschema.validate(response.structured_content,output_schema(TOOLS[-1]));calls.append(args)
                            return response
                        current=[]
                        for p in sources:
                            first=await call({'path':str(p),'page_size':50});assert not first.is_error
                            payload=first.structured_content;current.append(payload['advanced_snapshot_id'])
                            args={'path':str(p),'expected_advanced_snapshot':payload['advanced_snapshot_id'],'section':'observations','page_size':1}
                            observations=await call(args);assert not observations.is_error
                            ref=observations.structured_content['items'][0]['record_ref']
                            evidence=await call({'path':str(p),'expected_advanced_snapshot':payload['advanced_snapshot_id'],'section':'evidence','record_ref':ref,'page_size':50});assert not evidence.is_error
                            assert any(i['record_ref'].startswith('transpose:') for i in evidence.structured_content['items'])
                            bad=await call({'path':str(p),'expected_advanced_snapshot':'0'*64,'section':'observations'});assert bad.is_error
                        identities.append(current)
            assert err.closed and not (tmp_path/f'stderr{repeat}.txt').read_text()
        assert identities[0]==identities[1]
        assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    asyncio.run(run())
