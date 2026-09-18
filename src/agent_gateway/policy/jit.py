"""Opaque execution credentials: one token per approval, atomic token+approval claim."""
import json
import secrets
import time
from uuid import uuid4
from agent_gateway.approval.store import ApprovalStore, ApprovalError, canonical, fingerprint

class JITError(ApprovalError): pass

class JITStore(ApprovalStore):
    def __init__(self,path):
        super().__init__(path)
        with self.registry.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS execution_grants(token_id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, approval_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, consumed_at REAL)')
    def issue(self,aid,context,tool,parameters,policy_version,ttl=300):
        if type(ttl) is not int or not 1<=ttl<=300: raise JITError('INVALID_JIT_TTL')
        with self.registry.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            approval=self.validate_approved(db,aid,context,tool,parameters,policy_version)
            if db.execute('SELECT 1 FROM execution_grants WHERE approval_id=?',(aid,)).fetchone(): raise JITError('JIT_ALREADY_ISSUED')
            now=time.time();token=secrets.token_urlsafe(32);token_id='jit_'+uuid4().hex
            payload={'token_id':token_id,'approval_id':aid,'user_id':context.user_id,'agent_id':context.agent_id,'tenant_id':context.tenant_id,'task_id':context.task_id,'session_id':context.session_id,'delegation_leaf_id':context.delegation_leaf_id,'delegation_digest':approval.get('delegation_digest'),'tool':tool,'action':approval['action'],'resource':approval['resource'],'parameters_hash':fingerprint(tool,parameters),'parameter_constraints':parameters,'policy_version':policy_version,'issued_at':now,'expires_at':min(now+ttl,approval['expires_at'],context.expires_at),'max_uses':1}
            db.execute('INSERT INTO execution_grants VALUES (?,?,?,?,?,NULL)',(token_id,self.registry.digest(token),aid,canonical(payload),'active'))
            self.event(db,aid,'jit_issued',context.agent_id)
        return token,payload
    def inspect(self,token):
        with self.registry.connect() as db:
            row=db.execute('SELECT * FROM execution_grants WHERE token_hash=?',(self.registry.digest(token),)).fetchone()
        if row is None: raise JITError('UNKNOWN_JIT_TOKEN')
        payload=json.loads(row['payload']);status=row['status']
        if status=='active' and payload['expires_at']<=time.time(): status='expired'
        return payload|{'status':status,'consumed_at':row['consumed_at']}
    def revoke(self,token_id):
        with self.registry.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT approval_id,status FROM execution_grants WHERE token_id=?',(token_id,)).fetchone()
            if row is None: raise JITError('UNKNOWN_JIT_TOKEN')
            if row['status']!='active': raise JITError('JIT_NOT_ACTIVE')
            db.execute("UPDATE execution_grants SET status='revoked' WHERE token_id=? AND status='active'",(token_id,))
            self.event(db,row['approval_id'],'jit_revoked','trusted-admin')
    def consume(self,token,aid,context,tool,parameters,policy_version):
        with self.registry.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM execution_grants WHERE token_hash=?',(self.registry.digest(token),)).fetchone()
            if row is None: raise JITError('UNKNOWN_JIT_TOKEN')
            p=json.loads(row['payload'])
            if row['status']!='active': raise JITError('JIT_NOT_ACTIVE')
            if p['expires_at']<=time.time(): raise JITError('JIT_EXPIRED')
            if p['approval_id']!=aid: raise JITError('JIT_APPROVAL_MISMATCH')
            for key in ['user_id','agent_id','tenant_id','task_id','session_id','delegation_leaf_id']:
                if p[key]!=getattr(context,key): raise JITError('JIT_CONTEXT_MISMATCH')
            if p['tool']!=tool or p['parameters_hash']!=fingerprint(tool,parameters): raise JITError('JIT_PARAMETERS_MISMATCH')
            if p['policy_version']!=policy_version: raise JITError('JIT_POLICY_CHANGED')
            if p.get('delegation_digest')!=self.delegation_digest(context): raise JITError('JIT_DELEGATION_CHANGED')
            self.validate_approved(db,aid,context,tool,parameters,policy_version)
            now=time.time()
            db.execute("UPDATE execution_grants SET status='consumed',consumed_at=? WHERE token_id=?",(now,p['token_id']))
            db.execute("UPDATE approvals SET status='executing' WHERE id=?",(aid,))
            self.event(db,aid,'jit_consumed',context.agent_id)
            self.event(db,aid,'executing',context.agent_id)
        return p['token_id']
