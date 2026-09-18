"""Trusted demo provisioning. Never called from model tool arguments."""
from datetime import datetime,timezone
from uuid import uuid4
from agent_gateway.identity import IdentityRegistry,AgentIdentity,ExecutionContext
from agent_gateway.policy.capabilities import CapabilityGrant,CapabilityStore,ResourceScope


def analysis_session(path,session_id):
    r=IdentityRegistry(path);now=datetime.now(timezone.utc).timestamp();suffix=uuid4().hex
    user='analyst_'+suffix;agent='analysis_agent_'+suffix
    scope=('dataset:orders_customer_001',)
    r.register_user(user,'demo-tenant',now+3600)
    r.register_agent(AgentIdentity(agent_id=agent,agent_name='Order Analysis Agent',owner_id=user,
        tenant_id='demo-tenant',purpose='Analyze authorized synthetic orders',allowed_capabilities=('analysis.run',),
        allowed_resource_scope=scope,created_at=now,expires_at=now+3600))
    context=ExecutionContext(user_id=user,agent_id=agent,tenant_id='demo-tenant',task_id='analysis_'+suffix,
        task_scope=scope,task_actions=('analysis.run',),session_id=session_id,authenticated_at=now,expires_at=now+1800)
    store=CapabilityStore(path)
    for kind,ident in [('user',user),('agent',agent)]:
        store.add(CapabilityGrant(grant_id=ident+'_analysis',subject_type=kind,subject_id=ident,tenant_id='demo-tenant',
            action='analysis.run',resource_scope=ResourceScope(resource_type='dataset',resource_ids=('orders_customer_001',)),
            not_before=now,expires_at=now+1800,issued_by='demo-admin'))
    return r,r.issue_session(context)


def seed_analysis(backend):
    with backend.connect() as db:
        db.executemany('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?)',[
            ('order_439','customer_001',25000,5000,'GBP'),
            ('order_440','customer_001',10000,0,'USD'),
            ('order_441','customer_001',20000,3000,'USD'),
            ('order_private','customer_002',999999,0,'CNY')])
        db.execute("INSERT OR IGNORE INTO customers VALUES ('customer_002','Private fixture','active')")
        db.execute("INSERT OR IGNORE INTO resource_owners VALUES ('dataset','orders_customer_002','other-tenant')")

REFERENCE_CODE='''import json
from collections import defaultdict
rows=json.load(open('/tmp/gateway/data.json'))
groups={}
for r in rows:
 c=r['currency']
 g=groups.setdefault(c,{'order_count':0,'amount_minor':0,'refunded_minor':0,'net_minor':0})
 g['order_count']+=1
 g['amount_minor']+=r['amount_minor']
 g['refunded_minor']+=r['refunded_minor']
 g['net_minor']+=r['amount_minor']-r['refunded_minor']
print(json.dumps({'by_currency':groups,'row_count':len(rows)}))
'''
EXPECTED={'by_currency':{'GBP':{'order_count':2,'amount_minor':125000,'refunded_minor':5000,'net_minor':120000},
                         'USD':{'order_count':2,'amount_minor':30000,'refunded_minor':3000,'net_minor':27000}},'row_count':4}
