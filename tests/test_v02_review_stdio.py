"""Real local MCP negotiation and guarded review execution; no mock server handlers."""
from pathlib import Path
import asyncio,hashlib,json,sys
import pytest,jsonschema
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from composerlint.tool_contracts import TOOLS,input_schema,output_schema,dispatch
ROOT=Path(__file__).parents[1];F=ROOT/'tests/fixtures'


def test_real_stdio_pages_and_readonly_guard(tmp_path):
    bootstrap=tmp_path/'guard.py'
    bootstrap.write_text('''import sys,os
from composerlint.server import main
def audit(event,args):
 if event=="open":
  path,mode,flags=args
  if not isinstance(path,int) and path!="/dev/null" and (isinstance(mode,str) and any(c in mode for c in "wax+") or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):raise AssertionError("runtime file write")
 if event in ("socket.connect","socket.bind","subprocess.Popen","os.system","os.remove","os.rename","os.mkdir"):raise AssertionError("runtime side effect: "+event)
sys.addaudithook(audit)
main()
''')
    async def run():
        sources=[F/'m3/fifths_positive.musicxml',F/'ugly_ambiguous.mid'];before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        with (tmp_path/'stderr.txt').open('w+') as err:
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B',str(bootstrap)],cwd=ROOT,env={'PYTHONDONTWRITEBYTECODE':'1'}),errlog=err) as (r,w):
                async with ClientSession(r,w,read_timeout_seconds=45) as client:
                    await client.initialize();listed=(await client.list_tools()).tools
                    assert [t.name for t in listed]==[t.name for t in TOOLS]
                    actual=next(t for t in listed if t.name=='get_review_page');assert actual.input_schema==input_schema(next(t for t in TOOLS if t.name=='get_review_page'))
                    assert actual.annotations.read_only_hint and not actual.annotations.destructive_hint and not actual.annotations.open_world_hint
                    async def call(args):
                        response=await client.call_tool('get_review_page',args)
                        jsonschema.validate(response.structured_content,output_schema(next(t for t in TOOLS if t.name=='get_review_page')))
                        assert response.content[0].text==json.dumps(response.structured_content,sort_keys=True,separators=(',',':'),allow_nan=False)
                        expected,error=dispatch('get_review_page',args);assert response.is_error==error and response.structured_content==expected.model_dump(mode='json')
                        return response
                    for path in sources:
                        a=await call({'path':str(path),'page_size':50});assert not a.is_error
                        snapshot=a.structured_content['snapshot_id']
                        first=await call({'path':str(path),'section':'findings','expected_snapshot':snapshot,'page_size':1})
                        cursor=first.structured_content['next_cursor']
                        if cursor:
                            second=await call({'path':str(path),'section':'findings','expected_snapshot':snapshot,'page_size':1,'cursor':cursor});assert second.structured_content['start_index']==1
                        if path.suffix=='.musicxml':
                            ref=first.structured_content['items'][0]['record_ref']
                            evidence=await call({'path':str(path),'section':'evidence','record_ref':ref,'expected_snapshot':snapshot,'page_size':50});assert not evidence.is_error
                            assert evidence.structured_content['evidence_complete_in_this_response']
                        stale=await call({'path':str(path),'section':'findings','expected_snapshot':'0'*64});assert stale.is_error
                        assert not (await call({'path':str(path)})).is_error
                    assert (await call({'path':'/missing.musicxml'})).is_error
        assert err.closed and 'Traceback' not in (tmp_path/'stderr.txt').read_text()
        assert {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}==before
    asyncio.run(run())
