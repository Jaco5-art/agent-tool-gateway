"""Typed, deterministic capability grants. No expression evaluation or implicit wildcards."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from agent_gateway.identity import IdentityRegistry

Action = Literal['customer.read','customer.update','ticket.read','ticket.close','refund.read','refund.issue','database.query','database.export','code.execute','analysis.run']
ResourceType = Literal['customer','ticket','order','dataset','workspace']
SubjectType = Literal['user','agent']
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')]
Timestamp = Annotated[float, Field(allow_inf_nan=False, ge=0)]
ACTION_RESOURCE = {
    'analysis.run':'dataset',
    'customer.read':'customer','customer.update':'customer','ticket.read':'ticket','ticket.close':'ticket',
    'refund.read':'order','refund.issue':'order','database.query':'dataset','database.export':'dataset','code.execute':'workspace',
}

class FrozenModel(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,strict=True,revalidate_instances='always')

class ResourceScope(FrozenModel):
    resource_type: ResourceType
    resource_ids: Annotated[tuple[Identifier,...],Field(min_length=1)]

class Conditions(FrozenModel):
    currency: Literal['GBP','USD','CNY'] | None = None
    max_amount_minor: Annotated[int,Field(ge=0)] | None = None
    @model_validator(mode='after')
    def currency_for_money(self):
        if self.max_amount_minor is not None and self.currency is None:
            raise ValueError('An amount ceiling requires an explicit currency')
        return self

class CapabilityGrant(FrozenModel):
    grant_id: Identifier
    subject_type: SubjectType
    subject_id: Identifier
    tenant_id: Identifier
    action: Action
    resource_scope: ResourceScope
    conditions: Conditions = Conditions()
    not_before: Timestamp
    expires_at: Timestamp
    delegable: bool = False
    status: Literal['active','revoked'] = 'active'
    issued_by: Identifier
    @model_validator(mode='after')
    def validate_semantics(self):
        if self.expires_at <= self.not_before: raise ValueError('Invalid grant lifetime')
        if ACTION_RESOURCE[self.action]!=self.resource_scope.resource_type: raise ValueError('Action/resource type mismatch')
        if self.action!='refund.issue' and (self.conditions.currency is not None or self.conditions.max_amount_minor is not None):
            raise ValueError('Money constraints are only supported on refund.issue')
        return self

class CapabilityRequest(FrozenModel):
    subject_type: SubjectType
    subject_id: Identifier
    tenant_id: Identifier
    action: Action
    resource_type: ResourceType
    resource_id: Identifier
    amount_minor: Annotated[int,Field(gt=0)] | None = None
    currency: Literal['GBP','USD','CNY'] | None = None
    @model_validator(mode='after')
    def validate_semantics(self):
        if ACTION_RESOURCE[self.action]!=self.resource_type: raise ValueError('Action/resource type mismatch')
        if self.action=='refund.issue' and (self.amount_minor is None or self.currency is None): raise ValueError('Refund requires amount and currency')
        if self.action!='refund.issue' and (self.amount_minor is not None or self.currency is not None): raise ValueError('Unexpected money fields')
        return self

class MatchResult(FrozenModel):
    matched: bool
    reason_code: str
    grant_id: str

def match_grant(grant: CapabilityGrant, request: CapabilityRequest, *, now: float) -> MatchResult:
    # Revalidate to fail closed even if a caller used model_construct/model_copy.
    grant=CapabilityGrant.model_validate(grant)
    request=CapabilityRequest.model_validate(request)
    from math import isfinite
    if isinstance(now,bool) or not isinstance(now,(float,int)) or not isfinite(now) or now<0:
        raise ValueError('Invalid evaluation time')
    checks=[
        (grant.status=='active','GRANT_REVOKED'),
        (grant.subject_type==request.subject_type and grant.subject_id==request.subject_id,'SUBJECT_MISMATCH'),
        (grant.tenant_id==request.tenant_id,'TENANT_MISMATCH'),
        (now>=grant.not_before,'GRANT_NOT_YET_VALID'),
        (now<grant.expires_at,'GRANT_EXPIRED'),
        (grant.action==request.action,'ACTION_MISMATCH'),
        (grant.resource_scope.resource_type==request.resource_type and request.resource_id in grant.resource_scope.resource_ids,'RESOURCE_MISMATCH'),
        (grant.conditions.currency is None or grant.conditions.currency==request.currency,'CURRENCY_MISMATCH'),
        (grant.conditions.max_amount_minor is None or (request.amount_minor is not None and request.amount_minor<=grant.conditions.max_amount_minor),'AMOUNT_OVER_LIMIT'),
    ]
    for passed,reason in checks:
        if not passed: return MatchResult(matched=False,reason_code=reason,grant_id=grant.grant_id)
    return MatchResult(matched=True,reason_code='MATCH',grant_id=grant.grant_id)

class CapabilityStore:
    """Trusted admin API; immutable grants with explicit revocation. No grant issuance by LLM."""
    def __init__(self, path):
        self.registry=IdentityRegistry(path)
        with self.registry.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS capability_grants(grant_id TEXT PRIMARY KEY, payload TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)')
    def add(self, grant):
        grant=CapabilityGrant.model_validate(grant)
        with self.registry.connect() as db:
            db.execute('INSERT INTO capability_grants VALUES (?,?,?)',(grant.grant_id,grant.model_dump_json(),int(grant.status=='revoked')))
    def revoke(self, grant_id):
        with self.registry.connect() as db:
            if db.execute('UPDATE capability_grants SET revoked=1 WHERE grant_id=?',(grant_id,)).rowcount!=1: raise KeyError(grant_id)
    def get(self, grant_id):
        with self.registry.connect() as db:
            row=db.execute('SELECT payload,revoked FROM capability_grants WHERE grant_id=?',(grant_id,)).fetchone()
        if row is None: raise KeyError(grant_id)
        grant=CapabilityGrant.model_validate_json(row['payload'])
        return grant.model_copy(update={'status':'revoked'}) if row['revoked'] else grant
    def match(self, request, *, now):
        with self.registry.connect() as db:
            ids=[r[0] for r in db.execute('SELECT grant_id FROM capability_grants ORDER BY grant_id')]
        # Each grant must independently satisfy every constraint; never mix conditions across grants.
        results=[match_grant(self.get(i),request,now=now) for i in ids]
        return {'matched':any(r.matched for r in results),
                'matched_grant_ids':[r.grant_id for r in results if r.matched],
                'checks':[r.model_dump() for r in results]}
