import json
from datetime import datetime, timezone
from agent_gateway.contracts import TOOLS
from agent_gateway.identity import IdentityError
from agent_gateway.backend import BusinessError

# Existence probes are authorization-only, never executed. With the current
# upper-bound-only money grammar, one minor unit witnesses a nonempty range.
def probe_parameters(tool, resource_id, context):
    if tool=='crm.get_customer': return {'customer_id':resource_id}
    if tool=='ticket.read': return {'ticket_id':resource_id}
    if tool=='ticket.close': return {'ticket_id':resource_id,'resolution':'visibility probe','expected_version':1}
    if tool=='refund.issue': return {'order_id':resource_id,'amount_minor':1,'currency':context.task_refund_currency,'reason':'visibility probe'}
    if tool in {'database.query','analysis.run'}:
        if not resource_id.startswith('orders_'): return None
        if tool=='analysis.run': return {'customer_id':resource_id[len('orders_'):],'limit':1,'code':'print("{}")'}
        return {'query_id':'orders_by_customer','customer_id':resource_id[len('orders_'):],'limit':1}
    return None

def visible_tools(service):
    visible=[]; reason='FILTERED'; context=None
    now=datetime.now(timezone.utc).timestamp()
    try:
        context=service.registry.resolve(service.token,now=now)
        if context.session_id!=service.session_id: raise IdentityError('SESSION_MISMATCH')
        for name,spec in service.tools.items():
            for scoped in context.task_scope:
                kind,separator,rid=scoped.partition(':')
                if not separator or kind!=spec[3]: continue
                p=probe_parameters(name,rid,context)
                if p is None: continue
                try: resource=service.backend.resolve_resource(name,p)
                except BusinessError: continue
                decision=service.policy.evaluate(context,name,p,resource,now=now)
                if decision.decision in {'ALLOW','REQUIRE_APPROVAL'}:
                    visible.append(name);break
    except IdentityError as exc:
        reason=str(exc);visible=[]
    # Audit discovery separately from execution; never log a session token.
    event={'timestamp':datetime.now(timezone.utc).isoformat(),'session_id':service.session_id,
           'visible_tools':visible,'visible_count':len(visible),'total_count':len(service.tools),
           'exposure_reduction':1-len(visible)/len(service.tools),'reason':reason}
    if context: event.update(user_id=context.user_id,agent_id=context.agent_id,task_id=context.task_id)
    path=service.audit_path.with_name(service.audit_path.stem+'.binding.jsonl')
    with path.open('a',encoding='utf-8') as f: f.write(json.dumps(event)+'\n')
    return visible
