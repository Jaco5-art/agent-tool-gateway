from .contracts import ANALYSIS_SPEC

class AnalysisBackend:
    extra_tools={'analysis.run':ANALYSIS_SPEC}
    def __init__(self,backend,runner): self.backend=backend;self.runner=runner
    def __getattr__(self,name): return getattr(self.backend,name)
    def resolve_resource(self,tool,p):
        return self.backend.resolve_resource('database.query' if tool=='analysis.run' else tool,p)
    def validate_business(self,tool,p):
        if tool!='analysis.run': self.backend.validate_business(tool,p)
    def execution_metadata(self,tool):
        return {'execution_environment':'e2b' if tool=='analysis.run' else 'host'}
    def execute(self,tool,p):
        if tool!='analysis.run': return self.backend.execute(tool,p)
        # Service has already checked tenant, task, both grants, and delegation chain.
        data=self.backend.execute('database.query',{'query_id':'orders_by_customer','customer_id':p['customer_id'],'limit':p['limit']})
        return self.runner.run(p['code'],data['rows'])
