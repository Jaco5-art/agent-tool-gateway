"""Persistent, attenuation-only delegation. Records are provisioned by the trusted control plane."""
import hashlib
import json
import time
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator
from agent_gateway.identity import IdentityRegistry
from .capabilities import Action, CapabilityRequest, CapabilityStore

class DelegationError(ValueError): pass

class DelegationLink(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,strict=True,revalidate_instances='always')
    delegation_id: str
    parent_id: str | None = None
    issuer_id: str
    subject_id: str
    user_id: str
    tenant_id: str
    task_id: str
    session_id: str
    actions: tuple[Action,...]
    resources: tuple[str,...]
    refund_limit_minor: Annotated[int,Field(ge=0)] | None = None
    refund_currency: str | None = None
    not_before: Annotated[float,Field(ge=0,allow_inf_nan=False)]
    expires_at: Annotated[float,Field(ge=0,allow_inf_nan=False)]
    remaining_depth: Annotated[int,Field(ge=0,le=8)] = 0
    @model_validator(mode='after')
    def valid(self):
        if not self.actions or not self.resources or self.expires_at<=self.not_before: raise ValueError('INVALID_DELEGATION_SCOPE')
        if self.issuer_id==self.subject_id: raise ValueError('SELF_DELEGATION')
        if 'refund.issue' in self.actions and (self.refund_limit_minor is None or self.refund_currency not in {'GBP','USD','CNY'}): raise ValueError('REFUND_SCOPE_REQUIRED')
        if any(':' not in r or '*' in r for r in self.resources): raise ValueError('EXACT_RESOURCE_REQUIRED')
        return self

class DelegationStore:
    def __init__(self,path):
        self.registry=IdentityRegistry(path)
        self.capabilities=CapabilityStore(path)
        with self.registry.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS delegations(id TEXT PRIMARY KEY,payload TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0)')
    def get(self,ident):
        with self.registry.connect() as db: row=db.execute('SELECT * FROM delegations WHERE id=?',(ident,)).fetchone()
        if row is None: raise DelegationError('DELEGATION_PARENT_MISSING')
        return DelegationLink.model_validate_json(row['payload']),bool(row['revoked'])
    @staticmethod
    def attenuation(parent,child):
        if child.issuer_id!=parent.subject_id: raise DelegationError('DELEGATION_ISSUER_MISMATCH')
        for key in ['user_id','tenant_id','task_id','session_id']:
            if getattr(parent,key)!=getattr(child,key): raise DelegationError('DELEGATION_CONTEXT_MISMATCH')
        if not set(child.actions)<=set(parent.actions) or not set(child.resources)<=set(parent.resources): raise DelegationError('DELEGATION_SCOPE_EXPANSION')
        if child.not_before<parent.not_before or child.expires_at>parent.expires_at: raise DelegationError('DELEGATION_TIME_EXPANSION')
        if parent.remaining_depth<=0 or child.remaining_depth>=parent.remaining_depth: raise DelegationError('DELEGATION_DEPTH_EXCEEDED')
        if 'refund.issue' in child.actions and (child.refund_currency!=parent.refund_currency or child.refund_limit_minor>parent.refund_limit_minor): raise DelegationError('DELEGATION_AMOUNT_EXPANSION')
    def chain(self,leaf_id,now=None):
        now=time.time() if now is None else now
        chain=[];seen=set();current=leaf_id
        while current is not None:
            if current in seen: raise DelegationError('DELEGATION_CYCLE')
            if len(chain)>=9: raise DelegationError('DELEGATION_TOO_LONG')
            seen.add(current);link,revoked=self.get(current)
            if revoked: raise DelegationError('DELEGATION_REVOKED')
            if not link.not_before<=now<link.expires_at: raise DelegationError('DELEGATION_EXPIRED_OR_NOT_YET_VALID')
            chain.append(link);current=link.parent_id
        chain.reverse()
        if not chain or chain[0].issuer_id!=chain[0].user_id: raise DelegationError('DELEGATION_ROOT_INVALID')
        actors=[chain[0].issuer_id]+[x.subject_id for x in chain]
        if len(set(actors))!=len(actors): raise DelegationError('DELEGATION_PRINCIPAL_CYCLE')
        for p,c in zip(chain,chain[1:]): self.attenuation(p,c)
        for idx,actor in enumerate(actors):
            with self.registry.connect() as db: row=db.execute('SELECT * FROM principals WHERE id=?',(actor,)).fetchone()
            if row is None or row['kind']!=('user' if idx==0 else 'agent'): raise DelegationError('DELEGATION_PRINCIPAL_INVALID')
            if row['tenant']!=chain[0].tenant_id: raise DelegationError('DELEGATION_TENANT_MISMATCH')
            if row['status']!='active' or row['expires']<=now: raise DelegationError('DELEGATION_PRINCIPAL_INACTIVE')
        return chain
    def add(self,link):
        link=DelegationLink.model_validate(link)
        # Validate structural attenuation before persisting; request-specific grants
        # (including delegable flags) are independently checked at execution time.
        if link.parent_id:
            parents=self.chain(link.parent_id)
            self.attenuation(parents[-1],link)
            if link.subject_id in [parents[0].issuer_id]+[x.subject_id for x in parents]: raise DelegationError('DELEGATION_PRINCIPAL_CYCLE')
        elif link.issuer_id!=link.user_id: raise DelegationError('DELEGATION_ROOT_INVALID')
        with self.registry.connect() as db: db.execute('INSERT INTO delegations(id,payload) VALUES (?,?)',(link.delegation_id,link.model_dump_json()))
    def revoke(self,ident):
        with self.registry.connect() as db:
            if db.execute('UPDATE delegations SET revoked=1 WHERE id=?',(ident,)).rowcount!=1: raise DelegationError('UNKNOWN_DELEGATION')
    def validate_context(self,context,now):
        links=self.chain(context.delegation_leaf_id,now)
        leaf=links[-1]
        for key in ['user_id','tenant_id','task_id','session_id']:
            if getattr(leaf,key)!=getattr(context,key): raise DelegationError('DELEGATION_CONTEXT_MISMATCH')
        if leaf.subject_id!=context.agent_id: raise DelegationError('DELEGATION_LEAF_MISMATCH')
        return links
    def digest(self,context):
        if context.delegation_leaf_id is None: return None
        links=self.validate_context(context,time.time())
        return hashlib.sha256(json.dumps([x.model_dump(mode='json') for x in links],sort_keys=True).encode()).hexdigest()
    def authorize(self,context,action,resource,parameters,now):
        if context.delegation_leaf_id is None: return ()
        links=self.validate_context(context,now);key=resource['type']+':'+resource['id']
        for link in links:
            if action not in link.actions or key not in link.resources: raise DelegationError('DELEGATION_REQUEST_OUT_OF_SCOPE')
            if action=='refund.issue' and (parameters['currency']!=link.refund_currency or parameters['amount_minor']>link.refund_limit_minor): raise DelegationError('DELEGATION_AMOUNT_OUT_OF_SCOPE')
        actors=[links[0].issuer_id]+[x.subject_id for x in links]
        for idx,actor in enumerate(actors):
            if idx:
                agent=self.registry.get_agent(actor)
                if action not in agent.allowed_capabilities or key not in agent.allowed_resource_scope: raise DelegationError('DELEGATION_AGENT_CEILING')
            req=CapabilityRequest(subject_type='user' if idx==0 else 'agent',subject_id=actor,tenant_id=context.tenant_id,action=action,resource_type=resource['type'],resource_id=resource['id'],amount_minor=parameters.get('amount_minor') if action=='refund.issue' else None,currency=parameters.get('currency') if action=='refund.issue' else None)
            matches=self.capabilities.match(req,now=now)['matched_grant_ids']
            if not matches: raise DelegationError('DELEGATION_CAPABILITY_MISSING')
            if idx<len(actors)-1 and not any(self.capabilities.get(g).delegable for g in matches): raise DelegationError('DELEGATION_NOT_DELEGABLE')
        return tuple(x.delegation_id for x in links)
