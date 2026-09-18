import hashlib
import json
import secrets
import time
from uuid import uuid4
from agent_gateway.identity import IdentityRegistry

class ApprovalError(ValueError): pass

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def fingerprint(tool, parameters): return hashlib.sha256(canonical({'tool':tool,'parameters':parameters}).encode()).hexdigest()

class ApprovalStore:
    def __init__(self,path):
        self.registry=IdentityRegistry(path)
        with self.registry.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS reviewers(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, tenant TEXT NOT NULL, actions TEXT NOT NULL, resources TEXT NOT NULL, expires REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, reviewer_id TEXT, decided_at REAL, result TEXT);
            CREATE TABLE IF NOT EXISTS approval_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL, event TEXT NOT NULL, actor TEXT, timestamp REAL NOT NULL);
            ''')
    def register_reviewer(self,reviewer_id,tenant,actions,resources,expires_at):
        # Trusted provisioning operation; never exposed through MCP.
        if expires_at<=time.time(): raise ApprovalError('REVIEWER_EXPIRED')
        token=secrets.token_urlsafe(32)
        with self.registry.connect() as db:
            db.execute('INSERT INTO reviewers(id,token_hash,tenant,actions,resources,expires) VALUES (?,?,?,?,?,?)',(reviewer_id,self.registry.digest(token),tenant,canonical(actions),canonical(resources),expires_at))
        return token
    @staticmethod
    def event(db,approval_id,event,actor):
        db.execute('INSERT INTO approval_events(approval_id,event,actor,timestamp) VALUES (?,?,?,?)',(approval_id,event,actor,time.time()))
    def create(self,context,tool,parameters,decision,request_id):
        if decision.decision!='REQUIRE_APPROVAL': raise ApprovalError('NOT_APPROVAL_ELIGIBLE')
        aid='approval_'+uuid4().hex
        payload={'approval_id':aid,'request_id':request_id,'context':context.model_dump(mode='json'),'tool':tool,'parameters':parameters,'parameters_hash':fingerprint(tool,parameters),'policy_version':decision.policy_version,'delegation_digest':self.delegation_digest(context),'action':decision.action,'resource':decision.resource_type+':'+decision.resource_id,'risk_level':decision.risk_level,'created_at':time.time(),'expires_at':min(time.time()+300,context.expires_at)}
        with self.registry.connect() as db:
            db.execute('INSERT INTO approvals(id,payload,status) VALUES (?,?,?)',(aid,canonical(payload),'pending'))
            self.event(db,aid,'requested',context.user_id)
        return aid
    def delegation_digest(self,context):
        from agent_gateway.policy.delegation import DelegationStore
        if context.delegation_leaf_id is None: return None
        reader=DelegationStore.__new__(DelegationStore)
        reader.registry=self.registry
        return reader.digest(context)
    @staticmethod
    def row(db,aid):
        row=db.execute('SELECT * FROM approvals WHERE id=?',(aid,)).fetchone()
        if row is None: raise ApprovalError('UNKNOWN_APPROVAL')
        return row
    def get(self,aid):
        with self.registry.connect() as db: row=self.row(db,aid)
        payload=json.loads(row['payload'])
        status=row['status']
        if status in {'pending','approved'} and payload['expires_at']<=time.time(): status='expired'
        return payload|{'status':status,'reviewer_id':row['reviewer_id'],'decided_at':row['decided_at'],'result':json.loads(row['result']) if row['result'] else None}
    @staticmethod
    def reviewer_check(reviewer,payload):
        if reviewer is None or not reviewer['active'] or reviewer['expires']<=time.time(): raise ApprovalError('REVIEWER_NOT_AUTHORIZED')
        c=payload['context']
        if reviewer['id'] in {c['user_id'],c['agent_id']}: raise ApprovalError('SELF_APPROVAL_FORBIDDEN')
        if reviewer['tenant']!=c['tenant_id'] or payload['action'] not in json.loads(reviewer['actions']) or payload['resource'] not in json.loads(reviewer['resources']): raise ApprovalError('REVIEWER_SCOPE_MISMATCH')
    def decide(self,aid,reviewer_token,decision):
        if decision not in {'approve','reject'}: raise ApprovalError('INVALID_REVIEW_DECISION')
        with self.registry.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self.row(db,aid);payload=json.loads(row['payload'])
            reviewer=db.execute('SELECT * FROM reviewers WHERE token_hash=?',(self.registry.digest(reviewer_token),)).fetchone()
            self.reviewer_check(reviewer,payload)
            if payload['expires_at']<=time.time(): raise ApprovalError('APPROVAL_EXPIRED')
            if row['status']!='pending': raise ApprovalError('APPROVAL_ALREADY_DECIDED')
            status='approved' if decision=='approve' else 'rejected'
            db.execute('UPDATE approvals SET status=?,reviewer_id=?,decided_at=? WHERE id=?',(status,reviewer['id'],time.time(),aid))
            self.event(db,aid,status,reviewer['id'])
        return self.get(aid)
    def validate_approved(self,db,aid,context,tool,parameters,policy_version):
        row=self.row(db,aid);payload=json.loads(row['payload'])
        if row['status']!='approved': raise ApprovalError('APPROVAL_NOT_EXECUTABLE')
        if payload['expires_at']<=time.time(): raise ApprovalError('APPROVAL_EXPIRED')
        for key in ['user_id','agent_id','tenant_id','task_id','session_id','delegation_leaf_id']:
            if payload['context'][key]!=getattr(context,key): raise ApprovalError('APPROVAL_CONTEXT_MISMATCH')
        if payload['tool']!=tool or payload['parameters_hash']!=fingerprint(tool,parameters): raise ApprovalError('APPROVAL_PARAMETERS_MISMATCH')
        if payload['policy_version']!=policy_version: raise ApprovalError('APPROVAL_POLICY_CHANGED')
        if payload.get('delegation_digest')!=self.delegation_digest(context): raise ApprovalError('APPROVAL_DELEGATION_CHANGED')
        reviewer=db.execute('SELECT * FROM reviewers WHERE id=?',(row['reviewer_id'],)).fetchone()
        self.reviewer_check(reviewer,payload)
        return payload

    def claim(self,aid,context,tool,parameters,policy_version):
        # Legacy trusted helper; the Phase 7 gateway exclusively uses JITStore.consume.
        with self.registry.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.validate_approved(db,aid,context,tool,parameters,policy_version)
            db.execute("UPDATE approvals SET status='executing' WHERE id=?",(aid,))
            self.event(db,aid,'executing',context.agent_id)
    def finish(self,aid,result=None,failed=False):
        with self.registry.connect() as db:
            status='execution_uncertain' if failed else 'executed'
            if db.execute("UPDATE approvals SET status=?,result=? WHERE id=? AND status='executing'",(status,canonical(result) if result is not None else None,aid)).rowcount!=1: raise ApprovalError('INVALID_APPROVAL_TRANSITION')
            self.event(db,aid,status,None)
