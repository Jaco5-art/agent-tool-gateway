import sqlite3
import tempfile
import unittest
from pathlib import Path
from pydantic import ValidationError
from agent_gateway.policy.capabilities import CapabilityGrant, CapabilityRequest, ResourceScope, Conditions, CapabilityStore, match_grant

def grant(**changes):
    args=dict(grant_id='grant_a',subject_type='agent',subject_id='agent_a',tenant_id='tenant_a',action='refund.issue',resource_scope=ResourceScope(resource_type='order',resource_ids=('order_438',)),conditions=Conditions(currency='GBP',max_amount_minor=50000),not_before=100.0,expires_at=400.0,issued_by='admin_a')
    return CapabilityGrant(**(args|changes))
def request(**changes):
    args=dict(subject_type='agent',subject_id='agent_a',tenant_id='tenant_a',action='refund.issue',resource_type='order',resource_id='order_438',amount_minor=1200,currency='GBP')
    return CapabilityRequest(**(args|changes))

class CapabilityTests(unittest.TestCase):
    def test_valid_refund(self): self.assertTrue(match_grant(grant(),request(),now=200).matched)
    def test_inclusive_amount_limit(self): self.assertTrue(match_grant(grant(),request(amount_minor=50000),now=200).matched)
    def test_over_limit(self): self.assertEqual(match_grant(grant(),request(amount_minor=50001),now=200).reason_code,'AMOUNT_OVER_LIMIT')
    def test_wrong_resource(self): self.assertEqual(match_grant(grant(),request(resource_id='order_999'),now=200).reason_code,'RESOURCE_MISMATCH')
    def test_wrong_currency(self): self.assertEqual(match_grant(grant(),request(currency='USD'),now=200).reason_code,'CURRENCY_MISMATCH')
    def test_not_before_boundary(self):
        self.assertFalse(match_grant(grant(),request(),now=99.9).matched)
        self.assertTrue(match_grant(grant(),request(),now=100).matched)
    def test_expiry_boundary(self):
        self.assertTrue(match_grant(grant(),request(),now=399.99).matched)
        self.assertEqual(match_grant(grant(),request(),now=400).reason_code,'GRANT_EXPIRED')
    def test_wrong_subject_id(self): self.assertFalse(match_grant(grant(),request(subject_id='agent_b'),now=200).matched)
    def test_wrong_subject_type(self): self.assertFalse(match_grant(grant(),request(subject_type='user'),now=200).matched)
    def test_wrong_tenant(self): self.assertEqual(match_grant(grant(),request(tenant_id='tenant_b'),now=200).reason_code,'TENANT_MISMATCH')
    def test_wrong_action(self):
        r=request(action='refund.read',amount_minor=None,currency=None)
        self.assertEqual(match_grant(grant(),r,now=200).reason_code,'ACTION_MISMATCH')
    def test_revoked(self): self.assertFalse(match_grant(grant(status='revoked'),request(),now=200).matched)
    def test_strict_money(self):
        for amount in [True,1.5,'1200',0,-1]:
            with self.subTest(amount=amount),self.assertRaises(ValidationError): request(amount_minor=amount)
    def test_missing_refund_fields(self):
        for change in [dict(amount_minor=None),dict(currency=None)]:
            with self.assertRaises(ValidationError): request(**change)
    def test_amount_ceiling_requires_currency(self):
        with self.assertRaises(ValidationError): Conditions(max_amount_minor=50000)
    def test_invalid_time(self):
        for t in [float('nan'),float('inf'),True,-1]:
            with self.assertRaises(ValueError): match_grant(grant(),request(),now=t)
    def test_invalid_lifetime(self):
        with self.assertRaises(ValidationError): grant(expires_at=100.0)
        with self.assertRaises(ValidationError): grant(expires_at=float('inf'))
    def test_no_wildcards_or_unknown_conditions(self):
        with self.assertRaises(ValidationError): ResourceScope(resource_type='order',resource_ids=('*',))
        with self.assertRaises(ValidationError): Conditions(expression='amount < 500')
    def test_action_resource_mismatch(self):
        with self.assertRaises(ValidationError): grant(resource_scope=ResourceScope(resource_type='ticket',resource_ids=('ticket_101',)))
    def test_multiple_resources(self):
        g=grant(resource_scope=ResourceScope(resource_type='order',resource_ids=('order_438','order_999')))
        self.assertTrue(match_grant(g,request(resource_id='order_999'),now=200).matched)
    def test_read_capability(self):
        g=grant(action='ticket.read',resource_scope=ResourceScope(resource_type='ticket',resource_ids=('ticket_101',)),conditions=Conditions())
        r=request(action='ticket.read',resource_type='ticket',resource_id='ticket_101',amount_minor=None,currency=None)
        self.assertTrue(match_grant(g,r,now=200).matched)
    def test_untrusted_model_copy_revalidated(self):
        with self.assertRaises(ValueError): match_grant(grant(),request().model_copy(update={'amount_minor':True}),now=200)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'grants.sqlite';self.store=CapabilityStore(self.path)
    def tearDown(self): self.tmp.cleanup()
    def test_empty_store_no_match(self): self.assertFalse(self.store.match(request(),now=200)['matched'])
    def test_persistence(self):
        self.store.add(grant())
        self.assertEqual(CapabilityStore(self.path).match(request(),now=200)['matched_grant_ids'],['grant_a'])
    def test_revoke(self):
        self.store.add(grant());self.store.revoke('grant_a')
        self.assertFalse(self.store.match(request(),now=200)['matched'])
    def test_duplicate_cannot_overwrite(self):
        self.store.add(grant())
        with self.assertRaises(sqlite3.IntegrityError): self.store.add(grant(conditions=Conditions()))
    def test_grants_cannot_combine_to_expand(self):
        self.store.add(grant(conditions=Conditions(currency='GBP',max_amount_minor=100)))
        self.store.add(grant(grant_id='grant_b',resource_scope=ResourceScope(resource_type='order',resource_ids=('order_999',))))
        self.assertFalse(self.store.match(request(),now=200)['matched'])
    def test_matching_alternative(self):
        self.store.add(grant(status='revoked'));self.store.add(grant(grant_id='grant_b'))
        self.assertEqual(self.store.match(request(),now=200)['matched_grant_ids'],['grant_b'])
