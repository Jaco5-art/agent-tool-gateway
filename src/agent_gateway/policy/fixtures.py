"""Trusted local demo provisioning only. Not exposed as an Agent tool."""
from .capabilities import CapabilityStore, CapabilityGrant, ResourceScope, Conditions

ACTIONS=('customer.read','ticket.read','ticket.close','refund.issue','database.query')
RESOURCES=('customer:customer_001','ticket:ticket_101','order:order_438','dataset:orders_customer_001')

def provision_grants(registry, context):
    store=CapabilityStore(registry.path)
    scopes={'customer.read':('customer','customer_001'),'ticket.read':('ticket','ticket_101'),'ticket.close':('ticket','ticket_101'),'refund.issue':('order','order_438'),'database.query':('dataset','orders_customer_001')}
    for kind,subject in [('user',context.user_id),('agent',context.agent_id)]:
        for action,(rtype,rid) in scopes.items():
            store.add(CapabilityGrant(grant_id=subject+'_'+action.replace('.','_'),subject_type=kind,subject_id=subject,tenant_id=context.tenant_id,action=action,resource_scope=ResourceScope(resource_type=rtype,resource_ids=(rid,)),conditions=Conditions(currency='GBP',max_amount_minor=50000) if action=='refund.issue' else Conditions(),not_before=context.authenticated_at,expires_at=context.expires_at,issued_by='demo-admin'))
