import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from pydantic import ValidationError
from .contracts import TOOLS
from .identity import IdentityError
from .backend import BusinessError
from .approval.store import ApprovalStore, ApprovalError
from .policy.jit import JITStore, JITError
from .policy.engine import PolicyEngine, PolicyBlocked, POLICY_VERSION

class Service:
    def __init__(self, backend, audit_path, session_id, *, registry=None, token=None):
        self.backend=backend
        self.tools={**TOOLS,**getattr(backend,"extra_tools",{})}
        self.audit_path=Path(audit_path); self.audit_path.parent.mkdir(parents=True,exist_ok=True)
        self.session_id=session_id
        if registry is None or token is None: raise IdentityError('MISSING_TRUSTED_CONTEXT')
        self.registry,self.token=registry,token
        self.policy=PolicyEngine(registry,tools=self.tools)
        self.approvals=ApprovalStore(registry.path)
        self.jit=JITStore(registry.path)

    def call(self, tool, parameters, *, approval_id=None, jit_token=None):
        started=time.perf_counter()
        event=dict(request_id=uuid4().hex,session_id=self.session_id,tool=tool,timestamp=datetime.now(timezone.utc).isoformat(),policy_decision='DENY',policy_version=POLICY_VERSION,reason_code='PRECHECK_NOT_PASSED',parameters_hash=hashlib.sha256(json.dumps(parameters,sort_keys=True).encode()).hexdigest())
        event.update(audit_schema_version=2,trace_id=event['request_id'],parent_request_id=None)
        execution_started=False
        approval_claimed=False
        try:
            context,_=self.registry.lookup(self.token)
            event.update(context.model_dump())
            context=self.registry.resolve(self.token)
            if context.session_id!=self.session_id: raise IdentityError('SESSION_MISMATCH')
            event['identity_status']='validated'
            if tool not in self.tools: raise ValueError('UNKNOWN_TOOL')
            input_model,output_model,*_=self.tools[tool]
            p=input_model.model_validate(parameters).model_dump()
            resource=self.backend.resolve_resource(tool,p)
            decision=self.policy.evaluate(context,tool,p,resource,now=datetime.now(timezone.utc).timestamp())
            # Capability denial takes precedence over approval or business-state details.
            if decision.decision!='DENY':
                self.backend.validate_business(tool,p)
            event.update(decision.model_dump(exclude={'decision'}))
            event['policy_decision']=decision.decision
            if jit_token is not None and approval_id is None: raise JITError('JIT_APPROVAL_REQUIRED')
            if approval_id is not None:
                event['approval_id']=approval_id
                if decision.decision=='DENY': raise PolicyBlocked(decision)
                if jit_token is None: raise JITError('JIT_TOKEN_REQUIRED')
                event['jit_token_id']=self.jit.consume(jit_token,approval_id,context,tool,p,decision.policy_version)
                approval_claimed=True
                origin=self.approvals.get(approval_id)
                event.update(trace_id=origin['request_id'],parent_request_id=origin['request_id'],reviewer_id=origin['reviewer_id'],approved_at=origin['decided_at'])
                event.update(policy_decision='ALLOW',reason_code='JIT_VERIFIED_AND_CONSUMED')
            elif decision.decision=='REQUIRE_APPROVAL':
                aid=self.approvals.create(context,tool,p,decision,event['request_id'])
                event['approval_id']=aid
                raise PolicyBlocked(decision.model_copy(update={'approval_id':aid}))
            elif decision.decision=='DENY': raise PolicyBlocked(decision)
            execution_started=True
            if hasattr(self.backend,'execution_metadata'): event.update(self.backend.execution_metadata(tool))
            result=output_model.model_validate(self.backend.execute(tool,p))
            if approval_claimed: self.approvals.finish(approval_id,result.model_dump())
            if tool=='analysis.run':
                event.update({k:v for k,v in result.model_dump().items() if k!='result'})
            event['result_sha256']=hashlib.sha256(json.dumps(result.model_dump(),sort_keys=True,separators=(',',':')).encode()).hexdigest()
            event['execution_status']='succeeded'
            return result.model_dump()
        except Exception as exc:
            if approval_claimed:
                self.approvals.finish(approval_id,failed=True)
            if isinstance(exc,ApprovalError): event['policy_decision']='DENY'
            event['execution_status']='failed' if execution_started else 'blocked'
            event['error_type']=type(exc).__name__
            if isinstance(exc,IdentityError):
                event.update(identity_status='rejected',identity_error=str(exc),reason_code=str(exc))
            elif isinstance(exc,ValidationError) and not execution_started: event['reason_code']='INVALID_ARGUMENTS'
            elif isinstance(exc,(BusinessError,ValueError)) and not isinstance(exc,PolicyBlocked): event['reason_code']=str(exc)
            raise
        finally:
            event['latency_ms']=round((time.perf_counter()-started)*1000,3)
            with self.audit_path.open('a',encoding='utf-8') as stream: stream.write(json.dumps(event)+'\n')

    def issue_jit(self,approval_id,ttl=300):
        record=self.approvals.get(approval_id)
        context=self.registry.resolve(self.token)
        if context.session_id!=self.session_id: raise IdentityError('SESSION_MISMATCH')
        tool=record['tool'];p=self.tools[tool][0].model_validate(record['parameters']).model_dump()
        resource=self.backend.resolve_resource(tool,p)
        decision=self.policy.evaluate(context,tool,p,resource,now=datetime.now(timezone.utc).timestamp())
        if decision.decision=='DENY': raise PolicyBlocked(decision)
        self.backend.validate_business(tool,p)
        return self.jit.issue(approval_id,context,tool,p,decision.policy_version,ttl)

    def resume(self,approval_id,*,jit_token=None):
        # Backwards-compatible trusted orchestrator: mint then immediately consume.
        # Once minted, a lost/revoked/expired token cannot be silently replaced.
        if jit_token is None: jit_token,_=self.issue_jit(approval_id)
        record=self.approvals.get(approval_id)
        return self.call(record['tool'],record['parameters'],approval_id=approval_id,jit_token=jit_token)
