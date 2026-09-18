from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")]

class CustomerInput(Contract):
    customer_id: Identifier

class TicketInput(Contract):
    ticket_id: Identifier

class CloseInput(TicketInput):
    resolution: Annotated[str, Field(min_length=1, max_length=1000)]
    expected_version: Annotated[int, Field(ge=1)]

class RefundInput(Contract):
    order_id: Identifier
    amount_minor: Annotated[int, Field(gt=0)]
    currency: Literal["GBP", "USD", "CNY"]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]

class QueryInput(Contract):
    query_id: Literal["orders_by_customer"]
    customer_id: Identifier
    limit: Annotated[int, Field(ge=1, le=100)]

class CustomerOutput(Contract):
    customer_id: str
    name: str
    status: str

class TicketOutput(Contract):
    ticket_id: str
    customer_id: str
    status: str
    summary: str
    resolution: str
    version: int

class RefundOutput(Contract):
    refund_id: str
    order_id: str
    amount_minor: int
    currency: str
    status: Literal["simulated"]

class OrderRow(Contract):
    order_id: str
    customer_id: str
    amount_minor: int
    refunded_minor: int
    currency: str

class QueryOutput(Contract):
    columns: list[str]
    rows: list[OrderRow]
    row_count: int

# Canonical names stay dotted. Agent runtime maps them to API-safe aliases.
TOOLS = {
    "crm.get_customer": (CustomerInput, CustomerOutput, "customer.read", "customer", "LOW", "Read a simulated customer by customer_id."),
    "ticket.read": (TicketInput, TicketOutput, "ticket.read", "ticket", "LOW", "Read a simulated ticket and its current version."),
    "ticket.close": (CloseInput, TicketOutput, "ticket.close", "ticket", "MEDIUM", "Close a simulated open ticket. Read its version first; requires resolution and expected_version."),
    "refund.issue": (RefundInput, RefundOutput, "refund.issue", "order", "HIGH", "Issue a SIMULATED refund, never a real payment. amount_minor is integer pence/cents/fen. Requires order_id, currency and reason."),
    "database.query": (QueryInput, QueryOutput, "database.query", "dataset", "LOW", "Run orders_by_customer only. Requires customer_id and limit 1 to 100. No SQL input."),
}
