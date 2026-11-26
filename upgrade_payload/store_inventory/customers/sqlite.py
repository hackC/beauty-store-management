"""SQLite adapter. Every command executes in one protected transaction."""
from datetime import datetime, date
import json
import hashlib
import sqlite3
import uuid
from store_inventory.scheduling.policy import SchedulingError, granted, require, authorize_state, redact_state, redact_customer
from store_inventory.scheduling.rules import _validate_time, _validate_schedule_payload, _schedule_conflict_keys, _effective_staff_shifts
from store_inventory.scheduling.repository import SQLiteRepository, reading, writing


def sync_from_appointments(con, payload, now):
    """Customer-owned projection, called in the scheduling write transaction."""
    for item in payload.get('appointments', []):
        if item.get('restBlock') is True:
            continue
        name = str(item.get('customer') or '').strip()
        if not name:
            continue
        phone = str(item.get('phoneTail') or '').strip()[:11]
        existing = con.execute('SELECT id FROM customer_profiles WHERE name=? AND phone_tail=? ORDER BY updated_at DESC LIMIT 1', (name, phone)).fetchone()
        if existing:
            con.execute('UPDATE customer_profiles SET updated_at=? WHERE id=?', (now, existing['id']))
        else:
            cid=hashlib.sha256(f'{name}\x00{phone}'.encode('utf-8')).hexdigest()[:32]
            con.execute('INSERT OR IGNORE INTO customer_profiles(id,name,phone_tail,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)', (cid,name,phone,'',now,now))


class SQLiteCustomersRepository(SQLiteRepository):
    @reading
    def list_customers(self, con, user, actor, q, limit):
        require(user, 'ext.customers.read')
        sql = 'SELECT * FROM customer_profiles'
        args = []
        if q.strip():
            sql += ' WHERE name LIKE ?'
            args = [f'%{q.strip()}%']
            if granted(user, 'ext.customers.phone_read'):
                sql += ' OR phone_tail LIKE ?'
                args.append(f'%{q.strip()}%')
        sql += ' ORDER BY updated_at DESC LIMIT ?'
        args.append(max(1, min(limit, 500)))
        rows = con.execute(sql, args).fetchall()
        return [redact_customer(row, user) for row in rows]

    @writing
    def save_customer(self, con, user, actor, data):
        now = datetime.now().isoformat(timespec='seconds')
        customer_id = data['id'] or uuid.uuid4().hex
        before_row = con.execute('SELECT * FROM customer_profiles WHERE id=?', (customer_id,)).fetchone()
        before = dict(before_row) if before_row else None
        require(user, 'ext.customers.update' if before else 'ext.customers.create')
        if not data['name'].strip():
            raise SchedulingError(400, '客户姓名不能为空')
        data = dict(data)
        for field, perm in [('phone_tail', 'phone'), ('notes', 'notes')]:
            old = (before or {}).get(field, '')
            if field not in data or (before and not granted(user, 'ext.customers.' + perm + '_read') and not data[field]):
                data[field] = old
            if data[field] != old:
                require(user, 'ext.customers.' + perm + '_edit')
        if data['phone_tail'] and not (data['phone_tail'].isascii() and data['phone_tail'].isdigit()):
            raise SchedulingError(400, '手机尾号只能包含数字')
        con.execute('\n        INSERT INTO customer_profiles(id,name,phone_tail,notes,created_at,updated_at)\n        VALUES(?,?,?,?,?,?)\n        ON CONFLICT(id) DO UPDATE SET name=excluded.name,phone_tail=excluded.phone_tail,\n        notes=excluded.notes,updated_at=excluded.updated_at\n    ', (customer_id, data['name'].strip(), data['phone_tail'], data['notes'], before['created_at'] if before else now, now))
        self.audit(con, '保存客户档案', data['name'], actor, 'customer', customer_id, before, {'id': customer_id, 'name': data['name'], 'phone_tail': data['phone_tail'], 'notes': data['notes']})
        return {'ok': True, 'id': customer_id}

    @writing
    def delete_customer(self, con, user, actor, customer_id):
        require(user, 'ext.customers.delete')
        row = con.execute('SELECT * FROM customer_profiles WHERE id=?', (customer_id,)).fetchone()
        if not row:
            raise SchedulingError(404, '客户档案不存在')
        self.audit(con, '删除客户档案', row['name'], actor, 'customer', customer_id, dict(row), None)
        con.execute('DELETE FROM customer_profiles WHERE id=?', (customer_id,))
        return {'ok': True}

