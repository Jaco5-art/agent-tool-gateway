import sys
import os
from pathlib import Path
from .identity import local_session
from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

@asynccontextmanager
async def connect(db, audit, session_id='phase1-local', *, registry=None, token=None, e2b_analysis=False, sandbox_query=False, sandbox_image="python:3.12-slim"):
    if registry is None and token is None:
        if e2b_analysis:
            from .analysis.fixtures import analysis_session
            registry,token=analysis_session(Path(db).with_suffix('.identity.sqlite'),session_id)
        else:
            registry,token=local_session(Path(db).with_suffix('.identity.sqlite'),session_id)
    if registry is None or token is None:
        raise ValueError('Registry and token must be supplied together')
    params = StdioServerParameters(command=sys.executable,args=['-m','agent_gateway.server','--db',str(db),'--audit',str(audit),'--session-id',session_id,'--registry',str(registry.path)],env={'GATEWAY_SESSION_TOKEN':token})
    if e2b_analysis:
        params.args.append('--e2b-analysis')
        if os.getenv('E2B_API_KEY'): params.env['E2B_API_KEY']=os.environ['E2B_API_KEY']
    if sandbox_query: params.args.extend(['--sandbox-query','--sandbox-image',sandbox_image])
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=120 if e2b_analysis else 30) as session:
            await session.initialize()
            yield session
