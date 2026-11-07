"""Real negotiated SDK-client pipes; no mocked MCP handlers."""
from pathlib import Path
import asyncio,hashlib,json,os,signal,subprocess,sys,tempfile,time
import pytest,jsonschema
from mcp import ClientSession,StdioServerParameters,types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
from composerlint.tool_contracts import LEGACY_TOOLS as TOOLS, TOOLS as ALL_TOOLS,dispatch,input_schema,output_schema
from composerlint.analysis_models import AnalysisReport
ROOT=Path(__file__).parents[1];F=ROOT/'tests/fixtures';NAMES=[t.name for t in TOOLS]

def arguments(name,path):
    data={'path':str(path)}
    if name=='inspect_measure':data['measure_index']=1
    return data


def parameters(args=None):return StdioServerParameters(command=sys.executable,args=args or ['-B','-m','composerlint.server'],cwd=ROOT,env={'PYTHONDONTWRITEBYTECODE':'1'})


def verify(result,tool):
    assert not result.is_error and len(result.content)==1
    payload=result.structured_content
    assert result.content[0].text==json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    jsonschema.validate(payload,output_schema(tool))
    return payload


def test_T02_T04_T07_T09_real_roundtrip(tmp_path):
    async def run():
        stderr=tmp_path/'stderr.txt';sources=[F/'m4/four_episodes.musicxml',F/'m3/fifths_positive.mid'];before={p:p.read_bytes() for p in sources}
        with stderr.open('w+') as err:
            async with stdio_client(parameters(),errlog=err) as (r,w):
                async with ClientSession(r,w,read_timeout_seconds=45) as client:
                    info=await client.initialize();caps=info.capabilities.model_dump(exclude_none=True)
                    assert set(caps)=={'tools','resources'}
                    listed=await client.list_tools();assert [t.name for t in listed.tools]==[t.name for t in ALL_TOOLS]
                    for actual,expected in zip(listed.tools,TOOLS):
                        assert actual.input_schema==input_schema(expected) and actual.output_schema==output_schema(expected)
                        assert actual.annotations.model_dump(exclude_none=True)==dict(read_only_hint=True,destructive_hint=False,idempotent_hint=True,open_world_hint=False)
                    for path in sources:
                        for tool in TOOLS:
                            params=arguments(tool.name,path);result=await client.call_tool(tool.name,params);payload=verify(result,tool)
                            direct,error=dispatch(tool.name,params);assert not error and payload==direct.model_dump(mode='json')
                            repeated=await client.call_tool(tool.name,params);assert repeated.structured_content==payload and repeated.content==result.content
                    errors=[('analyze_score',{'path':'/missing.musicxml'},'file_not_found'),('analyze_score',{'path':'https://example.com/x.xml'},'unsupported_format'),('analyze_score',{'path':str(F/'empty.musicxml'),'options':{'target_style':'other'}},'unsupported_style'),('analyze_score',{'path':str(F/'basic.mid'),'extra':True},'invalid_options'),('inspect_measure',{'path':str(sources[0]),'measure_index':999},'measure_not_found'),('inspect_measure',{'path':str(sources[0]),'measure_index':1,'part_id':'unknown'},'unknown_voice')]
                    malformed=tmp_path/'broken.musicxml';malformed.write_bytes(b'garbage');errors.append(('analyze_score',{'path':str(malformed)},'parse_failed'))
                    for name,params,code in errors:
                        result=await client.call_tool(name,params);assert result.is_error and result.structured_content['error']['code']==code
                        assert set(result.structured_content)=={'schema_version','error'}
                        assert json.loads(result.content[0].text)==result.structured_content
                        jsonschema.validate(result.structured_content,next(t.output_schema for t in listed.tools if t.name==name))
                    with pytest.raises(MCPError) as exc:await client.call_tool('repair_score',{'path':str(sources[0])})
                    assert exc.value.code==-32602
                    # Malformed outer parameters are protocol failures, distinct from typed domain outcomes.
                    with pytest.raises(MCPError) as malformed_error:
                        await client._dispatcher.send_raw_request('tools/call', {'name':3}, {'timeout':30})
                    assert malformed_error.value.code==-32602
                    result=await client.call_tool('detect_style',arguments('detect_style',sources[0]));assert not result.is_error
                    concurrent=await asyncio.gather(client.call_tool('analyze_score',arguments('analyze_score',sources[0])),client.call_tool('analyze_score',dict(arguments('analyze_score',sources[1]),options={'midi':{'quantize_post':False}})))
                    for result,path in zip(concurrent,sources):assert result.structured_content['score']['source_sha256']==hashlib.sha256(before[path]).hexdigest()
        assert all(p.read_bytes()==data for p,data in before.items())
        assert 'Traceback' not in stderr.read_text()
    asyncio.run(run())


def test_T07_internal_protocol_fault_stderr_visible(tmp_path):
    bootstrap=tmp_path/'fault.py';bootstrap.write_text('''from composerlint import service
from composerlint.server import main
original=service.analyze_score
def fail(path,options=None):
 if path=="INJECT_INTERNAL_FAULT":raise RuntimeError("injected implementation failure")
 if path=="INJECT_RESULT_FAULT":
  class Bad:
   def model_dump(self,**kwargs):return {}
  return Bad()
 return original(path,options)
service.analyze_score=fail
main()
''')
    async def run():
        stderr=tmp_path/'internal-stderr.txt'
        with stderr.open('w+') as err:
            async with stdio_client(parameters(['-B',str(bootstrap)]),errlog=err) as (r,w):
                async with ClientSession(r,w,read_timeout_seconds=30) as client:
                    await client.initialize()
                    with pytest.raises(MCPError) as exc:await client.call_tool('analyze_score',{'path':'INJECT_INTERNAL_FAULT'})
                    assert exc.value.code==-32603 and 'internal failure' in exc.value.message
                    with pytest.raises(MCPError) as result_fault:await client.call_tool('analyze_score',{'path':'INJECT_RESULT_FAULT'})
                    assert result_fault.value.code==-32603
                    assert not (await client.call_tool('detect_style',{'path':str(F/'all_rest.musicxml')})).is_error
        assert 'Traceback' in stderr.read_text() and 'injected implementation failure' in stderr.read_text()
    asyncio.run(run())


def test_T08_runtime_no_network_writes_shell(tmp_path):
    bootstrap=tmp_path/'readonly.py';bootstrap.write_text('''import sys,os
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
        stderr=tmp_path/'readonly-stderr.txt'
        with stderr.open('w+') as err:
            async with stdio_client(parameters(['-B',str(bootstrap)]),errlog=err) as (r,w):
                async with ClientSession(r,w,read_timeout_seconds=30) as client:
                    await client.initialize();await client.list_tools()
                    for name in NAMES:
                        assert not (await client.call_tool(name,arguments(name,F/'m3/fifths_positive.mid'))).is_error
                        result=await client.call_tool(name,arguments(name,'https://example.com/x.xml'));assert result.is_error
        assert stderr.read_text()==''
    asyncio.run(run())


def test_T10_large_untruncated_roundtrip(tmp_path):
    from xml.etree import ElementTree as ET
    import copy
    tree=ET.parse(F/'m4/four_episodes.musicxml');root=tree.getroot()
    for part in root.findall('part'):
        measures=list(part.findall('measure'))
        for repeat in range(1,16):
            for m in measures:
                m=copy.deepcopy(m);m.set('number',str(len(part.findall('measure'))+1));part.append(m)
    p=tmp_path/'large.musicxml';tree.write(p,encoding='utf-8',xml_declaration=True)
    source=p.read_bytes();expected,error=dispatch('analyze_score',{'path':str(p)});assert not error
    canonical=expected.canonical_json();assert len(canonical.encode())>1024*1024
    print("Large-result canonical UTF-8 bytes:",len(canonical.encode()),"SHA256:",hashlib.sha256(canonical.encode()).hexdigest())
    async def run():
        with (tmp_path/'large-stderr.txt').open('w+') as err:
            async with stdio_client(parameters(),errlog=err) as (r,w):
                async with ClientSession(r,w,read_timeout_seconds=90) as client:
                    await client.initialize();await client.list_tools()
                    result=await client.call_tool('analyze_score',{'path':str(p)});assert not result.is_error
                    assert result.content[0].text==canonical and result.structured_content==expected.model_dump(mode='json')
                    assert AnalysisReport.model_validate(result.structured_content).canonical_json()==canonical
                    for name in ['check_harmony','check_voice_leading']:
                        focused=await client.call_tool(name,{'path':str(p)});model,_=dispatch(name,{'path':str(p)});assert focused.structured_content==model.model_dump(mode='json')
        assert p.read_bytes()==source
    asyncio.run(run())


def test_T12_eof_interrupt_no_orphan():
    p=subprocess.Popen([sys.executable,'-B','-m','composerlint.server'],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    out,err=p.communicate(timeout=10);assert p.returncode==0 and not out and not err
    p=subprocess.Popen([sys.executable,'-B','-m','composerlint.server'],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    import select
    try:
        request={'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':types.LATEST_PROTOCOL_VERSION,'capabilities':{},
            'clientInfo':{'name':'composerlint-test','version':'0.1'}}}
        p.stdin.write((json.dumps(request)+'\n').encode());p.stdin.flush()
        deadline=time.monotonic()+30;response=b''
        while not response.endswith(b'\n'):
            remaining=deadline-time.monotonic()
            assert remaining>0,'MCP initialize readiness timed out'
            readable,_,_=select.select([p.stdout],[],[],remaining)
            assert readable,'MCP initialize readiness timed out'
            chunk=os.read(p.stdout.fileno(),1)
            assert chunk,'Server exited before MCP initialize response'
            response+=chunk
        message=types.JSONRPCResponse.model_validate_json(response)
        assert message.id==1
        types.InitializeResult.model_validate(message.result)
        p.send_signal(signal.SIGINT);out,err=p.communicate(timeout=10)
        assert p.returncode==0 and not out and b'Traceback' not in err
        with pytest.raises(ProcessLookupError):os.kill(p.pid,0)
    finally:
        if p.poll() is None:
            p.kill();p.communicate(timeout=10)
