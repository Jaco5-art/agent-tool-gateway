"""Local, disposable SQLite business simulation; not an authorization layer."""
import sqlite3
from contextlib import contextmanager
from uuid import uuid4

class BusinessError(ValueError):
    pass

class Backend:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS resource_owners(resource_type TEXT, resource_id TEXT, tenant_id TEXT NOT NULL, PRIMARY KEY(resource_type,resource_id));
            INSERT OR IGNORE INTO resource_owners VALUES ('customer','customer_001','demo-tenant'),('ticket','ticket_101','demo-tenant'),('order','order_438','demo-tenant'),('dataset','orders_customer_001','demo-tenant');
            CREATE TABLE IF NOT EXISTS customers(customer_id TEXT PRIMARY KEY, name TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS tickets(ticket_id TEXT PRIMARY KEY, customer_id TEXT, status TEXT, summary TEXT, resolution TEXT, version INTEGER);
            CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, customer_id TEXT, amount_minor INTEGER, refunded_minor INTEGER, currency TEXT);
            CREATE TABLE IF NOT EXISTS refunds(refund_id TEXT PRIMARY KEY, order_id TEXT, amount_minor INTEGER, currency TEXT, status TEXT);
            INSERT OR IGNORE INTO customers VALUES ('customer_001','Demo Customer','active');
            INSERT OR IGNORE INTO tickets VALUES ('ticket_101','customer_001','open','Delivery question','',1);
            INSERT OR IGNORE INTO orders VALUES ('order_438','customer_001',100000,0,'GBP');
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def require(row):
        if row is None:
            raise BusinessError("RESOURCE_NOT_FOUND")
        return dict(row)

    def execute(self, tool, p):
        with self.connect() as db:
            if tool == 'crm.get_customer':
                return self.require(db.execute('SELECT * FROM customers WHERE customer_id=?',(p['customer_id'],)).fetchone())
            if tool == 'ticket.read':
                return self.require(db.execute('SELECT * FROM tickets WHERE ticket_id=?',(p['ticket_id'],)).fetchone())
            if tool == 'ticket.close':
                db.execute('BEGIN IMMEDIATE')
                row = self.require(db.execute('SELECT * FROM tickets WHERE ticket_id=?',(p['ticket_id'],)).fetchone())
                if row['version'] != p['expected_version']:
                    raise BusinessError('VERSION_CONFLICT')
                if row['status'] != 'open':
                    raise BusinessError('TICKET_NOT_OPEN')
                db.execute("UPDATE tickets SET status='closed', resolution=?, version=version+1 WHERE ticket_id=?",(p['resolution'],p['ticket_id']))
                return dict(db.execute('SELECT * FROM tickets WHERE ticket_id=?',(p['ticket_id'],)).fetchone())
            if tool == 'refund.issue':
                db.execute('BEGIN IMMEDIATE')
                order = self.require(db.execute('SELECT * FROM orders WHERE order_id=?',(p['order_id'],)).fetchone())
                if order['currency'] != p['currency']:
                    raise BusinessError('CURRENCY_MISMATCH')
                if p['amount_minor'] > order['amount_minor'] - order['refunded_minor']:
                    raise BusinessError('REFUND_EXCEEDS_BALANCE')
                result = dict(refund_id='refund_'+uuid4().hex, order_id=p['order_id'],amount_minor=p['amount_minor'],currency=p['currency'],status='simulated')
                db.execute('INSERT INTO refunds VALUES (?,?,?,?,?)',tuple(result.values()))
                db.execute('UPDATE orders SET refunded_minor=refunded_minor+? WHERE order_id=?',(p['amount_minor'],p['order_id']))
                return result
            if tool == 'database.query':
                rows = [dict(r) for r in db.execute('SELECT * FROM orders WHERE customer_id=? ORDER BY order_id LIMIT ?', (p['customer_id'],p['limit']))]
                return dict(columns=['order_id','customer_id','amount_minor','refunded_minor','currency'],rows=rows,row_count=len(rows))
            raise BusinessError('UNKNOWN_TOOL')

    def resolve_resource(self, tool, parameters):
        mapping={'crm.get_customer':('customer','customer_id','customers'),'ticket.read':('ticket','ticket_id','tickets'),'ticket.close':('ticket','ticket_id','tickets'),'refund.issue':('order','order_id','orders'),'database.query':('dataset','customer_id','customers')}
        rtype,field,table=mapping[tool]
        supplied=parameters[field]
        rid='orders_'+supplied if rtype=='dataset' else supplied
        with self.connect() as db:
            # SQL identifiers come exclusively from the static server mapping.
            self.require(db.execute(f'SELECT * FROM {table} WHERE {field}=?',(supplied,)).fetchone())
            row=self.require(db.execute('SELECT tenant_id FROM resource_owners WHERE resource_type=? AND resource_id=?',(rtype,rid)).fetchone())
        return {'type':rtype,'id':rid,'tenant_id':row['tenant_id']}

    def validate_business(self, tool, p):
        # Preflight prevents nonsensical approval requests; execute rechecks writes.
        with self.connect() as db:
            if tool=='ticket.close':
                row=self.require(db.execute('SELECT * FROM tickets WHERE ticket_id=?',(p['ticket_id'],)).fetchone())
                if row['version']!=p['expected_version']: raise BusinessError('VERSION_CONFLICT')
                if row['status']!='open': raise BusinessError('TICKET_NOT_OPEN')
            if tool=='refund.issue':
                row=self.require(db.execute('SELECT * FROM orders WHERE order_id=?',(p['order_id'],)).fetchone())
                if row['currency']!=p['currency']: raise BusinessError('CURRENCY_MISMATCH')
                if p['amount_minor']>row['amount_minor']-row['refunded_minor']: raise BusinessError('REFUND_EXCEEDS_BALANCE')
