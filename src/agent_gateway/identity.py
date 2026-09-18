"""Local simulated authentication. The launcher/registry files are trusted, not the LLM."""
import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class IdentityError(ValueError): pass

class AgentIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    agent_id: str
    agent_name: str
    owner_id: str
    tenant_id: str
    purpose: str
    status: Literal['active','disabled'] = 'active'
    allowed_capabilities: tuple[str, ...] = ()
    allowed_resource_scope: tuple[str, ...] = ()
    created_at: float
    expires_at: float

class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    user_id: str
    agent_id: str
    tenant_id: str
    task_id: str
    task_scope: tuple[str, ...]
    session_id: str
    delegation_leaf_id: str | None = None
    task_actions: tuple[str, ...] = ()
    task_refund_limit_minor: int | None = Field(default=None, ge=0, strict=True)
    task_refund_currency: str | None = None
    authenticated_at: float
    expires_at: float
    authentication_method: Literal['local_simulated'] = 'local_simulated'

class IdentityRegistry:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS principals(id TEXT PRIMARY KEY, kind TEXT NOT NULL, tenant TEXT NOT NULL, status TEXT NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL, context TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            ''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def register_user(self, user_id, tenant_id, expires_at):
        with self.connect() as db:
            db.execute('INSERT INTO principals VALUES (?,?,?,?,?,?)',(user_id,'user',tenant_id,'active',expires_at,'{}'))
    def register_agent(self, agent: AgentIdentity):
        if agent.expires_at <= agent.created_at: raise IdentityError('INVALID_AGENT_LIFETIME')
        with self.connect() as db:
            db.execute('INSERT INTO principals VALUES (?,?,?,?,?,?)',(agent.agent_id,'agent',agent.tenant_id,agent.status,agent.expires_at,agent.model_dump_json()))
    def get_agent(self, agent_id):
        with self.connect() as db:
            row=db.execute("SELECT payload,status FROM principals WHERE id=? AND kind='agent'",(agent_id,)).fetchone()
        if row is None: raise IdentityError('UNKNOWN_AGENT')
        return AgentIdentity.model_validate_json(row['payload']).model_copy(update={'status':row['status']})
    def validate(self, context, now):
        with self.connect() as db:
            for ident,kind in [(context.user_id,'user'),(context.agent_id,'agent')]:
                row=db.execute('SELECT * FROM principals WHERE id=?',(ident,)).fetchone()
                if row is None or row['kind']!=kind: raise IdentityError('UNKNOWN_'+kind.upper())
                if row['status']!='active': raise IdentityError(kind.upper()+'_DISABLED')
                if row['expires']<=now: raise IdentityError(kind.upper()+'_EXPIRED')
                if row['tenant']!=context.tenant_id: raise IdentityError('TENANT_MISMATCH')
        if context.expires_at<=now: raise IdentityError('SESSION_EXPIRED')
        if context.authenticated_at>now: raise IdentityError('SESSION_NOT_YET_VALID')
    def issue_session(self, context, now=None):
        now=datetime.now(timezone.utc).timestamp() if now is None else now
        self.validate(context,now)
        token=secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('INSERT INTO sessions(token_hash,session_id,context) VALUES (?,?,?)',(self.digest(token),context.session_id,context.model_dump_json()))
        return token
    @staticmethod
    def digest(token): return hashlib.sha256(token.encode()).hexdigest()
    def lookup(self, token):
        with self.connect() as db:
            row=db.execute('SELECT context,revoked FROM sessions WHERE token_hash=?',(self.digest(token),)).fetchone()
        if row is None: raise IdentityError('UNKNOWN_SESSION')
        return ExecutionContext.model_validate_json(row['context']), bool(row['revoked'])
    def resolve(self, token, now=None):
        context,revoked=self.lookup(token)
        if revoked: raise IdentityError('SESSION_REVOKED')
        self.validate(context,datetime.now(timezone.utc).timestamp() if now is None else now)
        return context
    def set_status(self, principal_id, status):
        if status not in {'active','disabled'}: raise ValueError('INVALID_STATUS')
        with self.connect() as db: db.execute('UPDATE principals SET status=? WHERE id=?',(status,principal_id))
    def revoke(self, session_id):
        with self.connect() as db: db.execute('UPDATE sessions SET revoked=1 WHERE session_id=?',(session_id,))

def local_session(path, session_id):
    """Explicit fixture provisioning, called by the trusted local launcher only."""
    from uuid import uuid4
    from .policy.fixtures import ACTIONS, RESOURCES, provision_grants
    registry=IdentityRegistry(path)
    now=datetime.now(timezone.utc).timestamp()
    # Unique fixture principals avoid overwriting identities, expiry, or revocations.
    suffix=uuid4().hex
    user='user_'+suffix; agent='support-agent_'+suffix
    registry.register_user(user,'demo-tenant',now+86400)
    registry.register_agent(AgentIdentity(agent_id=agent,agent_name='Support Agent',owner_id='demo-owner',tenant_id='demo-tenant',purpose='Simulated support operations',allowed_capabilities=('customer.read','ticket.read','ticket.close','refund.issue','database.query'),allowed_resource_scope=RESOURCES,created_at=now,expires_at=now+86400))
    context=ExecutionContext(user_id=user,agent_id=agent,tenant_id='demo-tenant',task_id='task_'+suffix,task_scope=RESOURCES,task_actions=ACTIONS,task_refund_limit_minor=50000,task_refund_currency='GBP',session_id=session_id,authenticated_at=now,expires_at=now+3600)
    provision_grants(registry,context)
    return registry,registry.issue_session(context)
