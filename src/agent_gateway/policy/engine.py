"""Deterministic authorization. No approval bypass or LLM-supplied identity."""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from .capabilities import CapabilityRequest, CapabilityStore
from agent_gateway.contracts import TOOLS
from .delegation import DelegationStore, DelegationError

POLICY_VERSION='phase9-e2b-v1'

class PolicyDecision(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    decision: Literal['ALLOW','DENY','REQUIRE_APPROVAL']
    reason_code: str
    policy_version: str = POLICY_VERSION
    risk_level: str
    action: str
    resource_type: str
    resource_id: str
    matched_grant_ids: tuple[str,...] = ()
    approval_id: str | None = None
    delegation_chain: tuple[str,...] = ()

class PolicyBlocked(ValueError):
    def __init__(self, decision):
        self.decision=decision
        super().__init__(decision.reason_code)

class PolicyEngine:
    def __init__(self, registry, *, tools=None):
        self.tools=TOOLS if tools is None else tools
        self.registry=registry
        self.store=CapabilityStore(registry.path)
        self.delegation=DelegationStore(registry.path)
    def evaluate(self, context, tool, parameters, resource, *, now):
        _,_,action,_,risk,_=self.tools[tool]
        base=dict(risk_level=risk,action=action,resource_type=resource['type'],resource_id=resource['id'])
        def deny(reason): return PolicyDecision(decision='DENY',reason_code=reason,**base)
        if resource['tenant_id']!=context.tenant_id: return deny('RESOURCE_TENANT_MISMATCH')
        key=resource['type']+':'+resource['id']
        agent=self.registry.get_agent(context.agent_id)
        if action not in agent.allowed_capabilities: return deny('AGENT_ACTION_CEILING')
        if key not in agent.allowed_resource_scope: return deny('AGENT_RESOURCE_CEILING')
        if action not in context.task_actions: return deny('TASK_ACTION_MISMATCH')
        if key not in context.task_scope: return deny('TASK_RESOURCE_MISMATCH')
        if action=='refund.issue':
            if context.task_refund_currency!=parameters['currency']: return deny('TASK_CURRENCY_MISMATCH')
            if context.task_refund_limit_minor is None or parameters['amount_minor']>context.task_refund_limit_minor: return deny('TASK_AMOUNT_OVER_LIMIT')
        matched=[]
        for kind,subject in [('user',context.user_id),('agent',context.agent_id)]:
            request=CapabilityRequest(subject_type=kind,subject_id=subject,tenant_id=context.tenant_id,action=action,resource_type=resource['type'],resource_id=resource['id'],amount_minor=parameters.get('amount_minor') if action=='refund.issue' else None,currency=parameters.get('currency') if action=='refund.issue' else None)
            result=self.store.match(request,now=now)
            if not result['matched']: return deny(kind.upper()+'_CAPABILITY_MISSING')
            matched.extend(result['matched_grant_ids'])
        try:
            chain=self.delegation.authorize(context,action,resource,parameters,now)
        except DelegationError as exc: return deny(str(exc))
        base['delegation_chain']=chain
        # HIGH needs the separate approval gate; base policy never approves it alone.
        if risk in {'HIGH','CRITICAL'}:
            return PolicyDecision(decision='REQUIRE_APPROVAL',reason_code='HIGH_RISK_APPROVAL_REQUIRED',matched_grant_ids=tuple(matched),**base)
        return PolicyDecision(decision='ALLOW',reason_code='ALL_REQUIRED_SCOPES_MATCH',matched_grant_ids=tuple(matched),**base)
