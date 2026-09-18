"""Real MCP stdio server. stdout is reserved exclusively for the protocol."""
import argparse
import os
from .identity import IdentityRegistry
import asyncio
import json
from pydantic import ValidationError
from .backend import BusinessError
from .policy.engine import PolicyBlocked
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from .backend import Backend
from .service import Service
from .contracts import TOOLS
from .binding.manifest import visible_tools

def make_server(service):
    async def list_tools(context, params):
        return types.ListToolsResult(tools=[types.Tool(name=name,description=spec[5],inputSchema=spec[0].model_json_schema(),
                           outputSchema=spec[1].model_json_schema(),
                           annotations=types.ToolAnnotations(readOnlyHint=name in {'crm.get_customer','ticket.read','database.query'},
                                                             destructiveHint=name in {'ticket.close','refund.issue'},openWorldHint=False),
                           meta={'action':spec[2],'required_capability':spec[2],'resource_type':spec[3],'risk_level':spec[4]})
                for name in visible_tools(service) for spec in [service.tools[name]]])

    async def call_tool(context, params):
        try:
            result = service.call(params.name, params.arguments or {})
            return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result))],structuredContent=result,isError=False)
        except PolicyBlocked as exc:
            return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(exc.decision.model_dump()))],isError=True)
        except (ValidationError, BusinessError, ValueError) as exc:
            code = 'INVALID_ARGUMENTS' if isinstance(exc, ValidationError) else str(exc)
            body={'decision':'DENY','reason_code':code}
            if getattr(exc,'diagnostic',None): body['diagnostic']=exc.diagnostic
            return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(body))],isError=True)

    return Server('enterprise-support-phase9',on_list_tools=list_tools,on_call_tool=call_tool)

async def serve(args):
    backend=Backend(args.db)
    if args.e2b_analysis:
        from .analysis.backend import AnalysisBackend
        from .analysis.e2b_runner import E2BRunner
        backend=AnalysisBackend(backend,E2BRunner())
    if args.sandbox_query:
        from .sandbox import DockerSandbox, SandboxedQueryBackend
        backend=SandboxedQueryBackend(backend,DockerSandbox(args.sandbox_image))
    service = Service(backend,args.audit,args.session_id,registry=IdentityRegistry(args.registry),token=os.environ.pop("GATEWAY_SESSION_TOKEN", ""))
    server = make_server(service)
    async with stdio_server() as (read,write):
        await server.run(read,write,server.create_initialization_options())

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--e2b-analysis',action='store_true')
    parser.add_argument('--sandbox-query',action='store_true')
    parser.add_argument('--sandbox-image',default='python:3.12-slim')
    parser.add_argument('--registry',required=True)
    parser.add_argument('--db',required=True)
    parser.add_argument('--audit',required=True)
    parser.add_argument('--session-id',default='phase1-local')
    asyncio.run(serve(parser.parse_args()))

if __name__ == '__main__':
    main()
