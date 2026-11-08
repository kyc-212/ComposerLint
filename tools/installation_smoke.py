"""Small local installation probe: authored analysis and actual stdio discovery."""
import asyncio
import os
from pathlib import Path
import sys

from composerlint.service import analyze_score
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    source = Path(__file__).resolve().parents[1] / 'tests/fixtures/basic.musicxml'
    before = source.read_bytes()
    report = analyze_score(str(source), {})
    assert report.canonical_json()
    assert source.read_bytes() == before
    params = StdioServerParameters(command=sys.executable,
                                  args=['-I', '-B', '-m', 'composerlint.server'], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=30) as client:
            initialized = await client.initialize()
            assert initialized.server_info.version == '0.2.0'
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert len(names) == 18 and {'analyze_score', 'get_score_view', 'import_score'} <= names
    print('Authored analysis, unchanged source and real MCP version/tool discovery passed.')


if __name__ == '__main__':
    asyncio.run(main())
