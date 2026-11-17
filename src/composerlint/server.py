"""Local stdio MCP integration; importing this module starts no transport."""
import asyncio
import logging
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, INTERNAL_ERROR
from composerlint.tool_contracts import TOOLS, TOOL_BY_NAME, dispatch, input_schema, output_schema, canonical_payload

logger = logging.getLogger(__name__)


def create_server():
    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(
            name=t.name,description=t.description,input_schema=input_schema(t),output_schema=output_schema(t),
            annotations=types.ToolAnnotations(read_only_hint=t.read_only,destructive_hint=False,idempotent_hint=t.idempotent if t.idempotent is not None else t.name != 'get_score_view',open_world_hint=False)
        ) for t in TOOLS])

    async def call_tool(ctx, params):
        if params.name not in TOOL_BY_NAME:
            raise MCPError(INVALID_PARAMS, 'Unknown ComposerLint tool: '+params.name)
        try:
            result,is_error=dispatch(params.name,params.arguments)
            payload=result.model_dump(mode='json')
            return types.CallToolResult(is_error=is_error,structured_content=payload,
                content=[types.TextContent(type='text',text=canonical_payload(result))])
        except Exception as exc:
            logger.exception('Unexpected ComposerLint implementation failure')
            raise MCPError(INTERNAL_ERROR,'ComposerLint internal failure; see server stderr') from exc

    async def read_resource(ctx, params):
        from composerlint.viewer_runtime import runtime
        from composerlint.viewer_source import ViewerFailure
        try:
            data, mime = runtime.resource(str(params.uri))
        except ViewerFailure as exc:
            raise MCPError(INVALID_PARAMS, exc.reason) from exc
        return types.ReadResourceResult(contents=[types.TextResourceContents(uri=str(params.uri), mime_type=mime, text=data.decode())])

    async def list_resources(ctx, params):
        # The pinned SDK advertises resources only when resources/list exists.
        # URI handles are returned by get_score_view; never enumerate other views.
        return types.ListResourcesResult(resources=[])

    return Server('ComposerLint',version='0.2.0',on_list_tools=list_tools,on_call_tool=call_tool,on_read_resource=read_resource,on_list_resources=list_resources,
                  get_tool_input_schema=lambda name: input_schema(TOOL_BY_NAME[name]) if name in TOOL_BY_NAME else None)


async def run_stdio():
    server=create_server()
    async with stdio_server() as (read_stream,write_stream):
        try:
            await server.run(read_stream,write_stream,server.create_initialization_options(),raise_exceptions=True)
        finally:
            # Legacy-only sessions never import or start the presentation runtime.
            import sys
            if 'composerlint.passage_session' in sys.modules:
                sys.modules['composerlint.passage_session'].worker.close()
            if 'composerlint.editing_runtime' in sys.modules:
                sys.modules['composerlint.editing_runtime'].editing.close()
            if 'composerlint.viewer_runtime' in sys.modules:
                sys.modules['composerlint.viewer_runtime'].runtime.close()


def main():
    try:
        asyncio.run(run_stdio())
    except KeyboardInterrupt:
        pass


if __name__=='__main__':
    main()
