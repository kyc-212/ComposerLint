"""Real additive MCP discovery/resources/selection and optional-absent guards."""
from pathlib import Path
import asyncio
import hashlib
import json
import sys
import urllib.request
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import jsonschema
from composerlint.tool_contracts import TOOLS

ROOT=Path(__file__).parents[1]


def test_actual_stdio_view_resources_and_followup(tmp_path):
    async def check():
        p=ROOT/'tests/fixtures/v02_m2/duet.musicxml';before=p.read_bytes()
        with (tmp_path/'stderr').open('w+') as err:
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','composerlint.server'],cwd=ROOT,env={'PYTHONDONTWRITEBYTECODE':'1'}),errlog=err) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=45) as client:
                    await client.initialize();listed=(await client.list_tools()).tools
                    assert (await client.list_resources()).resources == []
                    assert [t.name for t in listed]==[t.name for t in TOOLS]
                    tools={t.name:t for t in listed}
                    assert not tools['get_score_view'].annotations.idempotent_hint
                    assert tools['resolve_score_selection'].annotations.read_only_hint
                    overview=(await client.call_tool('get_review_page',{'path':str(p)})).structured_content
                    args=dict(path=str(p),expected_snapshot=overview['snapshot_id'],reference_part_id='p0001',start_measure_index=1,end_measure_index=4,presentation='interactive',page_size=1)
                    first=(await client.call_tool('get_score_view',args)).structured_content
                    jsonschema.validate(first,tools['get_score_view'].output_schema)
                    assert first['status']=='mapped'
                    image=(await client.read_resource(first['svg_uri'])).contents[0]
                    assert image.mime_type=='image/svg+xml' and hashlib.sha256(image.text.encode()).hexdigest()==first['svg_sha256']
                    manifest=(await client.read_resource(first['manifest_uri'])).contents[0]
                    assert manifest.mime_type=='application/json' and len(json.loads(manifest.text)['groups'])==4
                    rows=first['items'];current=first
                    while current['next_cursor']:
                        current=(await client.call_tool('get_score_view',dict(args,cursor=current['next_cursor'],view_instance_id=first['view_instance_id']))).structured_content
                        assert current['view_instance_id']==first['view_instance_id']
                        rows+=current['items']
                    assert len(rows)==first['total_items']
                    second=(await client.call_tool('get_score_view',args)).structured_content
                    assert second['view_key']==first['view_key'] and second['view_instance_id']!=first['view_instance_id']
                    body=json.dumps(dict(start=2,end=2,revision=1)).encode()
                    req=urllib.request.Request(first['viewer_url']+'selection',data=body,headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(req) as response:selection=json.load(response)
                    result=await client.call_tool('resolve_score_selection',dict(path=str(p),expected_snapshot=overview['snapshot_id'],selection_ref=selection['selection_ref'],view_instance_id=selection['view_instance_id'],revision=1))
                    assert not result.is_error
                    jsonschema.validate(result.structured_content,tools['resolve_score_selection'].output_schema)
                    finding=next(i for i in result.structured_content['items'] if i['record_ref'].startswith('diagnostic:'))
                    evidence=await client.call_tool('get_review_page',dict(path=str(p),expected_snapshot=overview['snapshot_id'],section='evidence',record_ref=finding['record_ref'],page_size=50))
                    assert not evidence.is_error and evidence.structured_content['evidence_complete_in_this_response']
                    stale=await client.call_tool('resolve_score_selection',dict(path=str(p),expected_snapshot=overview['snapshot_id'],selection_ref=selection['selection_ref'],view_instance_id=second['view_instance_id'],revision=1))
                    assert stale.is_error and stale.structured_content['error']['code']=='stale_selection'
        assert before==p.read_bytes() and 'Traceback' not in (tmp_path/'stderr').read_text()
    asyncio.run(check())


def test_actual_stdio_whole_score_pages_no_range_and_live_ninth(tmp_path):
    async def check():
        p=ROOT/'tests/fixtures/v02_m2/multipage.musicxml'
        with (tmp_path/'stderr').open('w+') as err:
            async with stdio_client(StdioServerParameters(command=sys.executable,args=['-B','-m','composerlint.server'],cwd=ROOT,env={'PYTHONDONTWRITEBYTECODE':'1'}),errlog=err) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=45) as client:
                    initialized=await client.initialize()
                    assert initialized.capabilities.resources is not None
                    tools={t.name:t for t in (await client.list_tools()).tools}
                    schema=tools['get_score_view'].input_schema
                    assert set(schema['$defs']['ViewArguments']['required'])=={'path','expected_snapshot'}
                    assert set(schema['$defs']['SourceViewArguments']['required'])=={'path'}
                    snapshot=(await client.call_tool('get_review_page',{'path':str(p)})).structured_content['snapshot_id']
                    args=dict(path=str(p),expected_snapshot=snapshot,presentation='interactive',page_size=1)
                    result=await client.call_tool('get_score_view',args)
                    assert not result.is_error
                    first=result.structured_content
                    jsonschema.validate(first,tools['get_score_view'].output_schema)
                    assert first['page_count']>=3 and first['notation_page']==1 and first['selection'] is None
                    manifest=json.loads((await client.read_resource(first['manifest_uri'])).contents[0].text)
                    assert any(len(p['groups'])>8 for p in manifest['pages'])
                    for number in range(1,first['page_count']+1):
                        page=(await client.call_tool('get_score_view',dict(args,view_instance_id=first['view_instance_id'],notation_page=number))).structured_content
                        asset=(await client.read_resource(page['svg_uri'])).contents[0]
                        assert hashlib.sha256(asset.text.encode()).hexdigest()==page['svg_sha256']
                        assert page['view_instance_id']==first['view_instance_id']
                    req=urllib.request.Request(first['viewer_url']+'selection',data=json.dumps(dict(start=9,end=9,revision=1)).encode(),headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(req) as response:selected=json.load(response)
                    resolved=await client.call_tool('resolve_score_selection',dict(path=str(p),expected_snapshot=snapshot,selection_ref=selected['selection_ref'],view_instance_id=selected['view_instance_id'],revision=1,page_size=50))
                    assert not resolved.is_error and resolved.structured_content['selection']['start_measure_index']==9
                    assert {i['data']['measure_index'] for i in resolved.structured_content['items'] if i['record_ref'].startswith('segment:')}=={9}
                    invalid=await client.call_tool('get_score_view',dict(args,view_instance_id=first['view_instance_id'],notation_page=first['page_count']+1))
                    assert invalid.is_error and invalid.structured_content['error']['code']=='invalid_page'
        assert 'Traceback' not in (tmp_path/'stderr').read_text()
    asyncio.run(check())
