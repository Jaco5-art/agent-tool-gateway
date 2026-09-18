import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv
from agents import Agent, FunctionTool, Runner, ModelSettings, set_tracing_disabled
from .client import connect

async def bind_tools(session):
    manifest = await session.list_tools()
    tools = []
    for item in manifest.tools:
        # Dotted canonical MCP names map to explicit, collision-checked API aliases.
        alias = item.name.replace('.', '__')
        if any(t.name == alias for t in tools):
            raise ValueError('TOOL_ALIAS_COLLISION')
        def bind(name):
            async def invoke(context, arguments):
                result = await session.call_tool(name,json.loads(arguments))
                return json.dumps({'is_error':bool(result.is_error),'data':result.structured_content,
                                   'content':[c.model_dump() for c in result.content]})
            return invoke
        def enabled(name):
            async def check(context, agent):
                current=await session.list_tools()
                return any(tool.name==name for tool in current.tools)
            return check
        tools.append(FunctionTool(name=alias,description=item.description or item.name,
                                  params_json_schema=item.input_schema,on_invoke_tool=bind(item.name),is_enabled=enabled(item.name),strict_json_schema=False))
    return tools

async def run_agent(request, directory, model, *, sandbox_query=False, sandbox_image="python:3.12-slim"):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    session_id='session_'+uuid4().hex
    set_tracing_disabled(True)  # Local audit is sufficient for Phase 1; no hosted trace upload.
    async with connect(directory/'business.sqlite',directory/'audit.jsonl',session_id,sandbox_query=sandbox_query,sandbox_image=sandbox_image) as session:
        agent = Agent(name='Support Agent Phase 9',model=model,
            instructions='You operate a SIMULATED support system. Use tools to answer and execute requested simulated actions. Never claim a real refund. Do not invent resource IDs. Read tickets before closing to obtain expected_version. Monetary tool inputs use integer minor units (GBP 1 = 100 pence). Do not retry mutations blindly. Report tool errors truthfully. Identity is attached by the runtime; do not add identity fields to tool arguments. The gateway enforces permissions independently. REQUIRE_APPROVAL means no execution occurred; report pending approval and stop. Return the approval_id when pending. A separate local operator reviews and resumes the gateway action; you cannot approve it yourself. Do not claim a refund was issued while it is pending.',
            tools=await bind_tools(session), model_settings=ModelSettings(parallel_tool_calls=False))
        result=await Runner.run(agent,request,max_turns=8)
        return {'session_id':session_id,'model':model,'final_output':result.final_output}

def main():
    load_dotenv()
    parser=argparse.ArgumentParser()
    parser.add_argument('request')
    parser.add_argument('--sandbox-query',action='store_true')
    parser.add_argument('--sandbox-image',default='python:3.12-slim')
    parser.add_argument('--workdir',default='results/local-demo')
    args=parser.parse_args()
    if not os.getenv('OPENAI_API_KEY'):
        parser.exit(2,'OPENAI_API_KEY is missing. Set it in your local .env; never send the key in chat.\n')
    print(json.dumps(asyncio.run(run_agent(args.request,args.workdir,os.getenv('OPENAI_MODEL','gpt-5-mini'),sandbox_query=args.sandbox_query,sandbox_image=args.sandbox_image)),ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
