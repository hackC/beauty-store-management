"""SQLite adapter. Every command executes in one protected transaction."""
from datetime import datetime, date
import json
import hashlib
import sqlite3
import uuid
from store_inventory.scheduling.policy import SchedulingError, granted, require, authorize_state, redact_state, redact_customer
from store_inventory.scheduling.rules import _validate_time, _validate_schedule_payload, _schedule_conflict_keys, _effective_staff_shifts
from store_inventory.scheduling.repository import SQLiteRepository, reading, writing


class SQLiteWaitlistRepository(SQLiteRepository):
    @reading
    def list_schedule_waitlist(self, con, user, actor, status):
        require(user, 'ext.waitlist.read')
        if status == 'all':
            rows = con.execute('SELECT * FROM schedule_waitlist ORDER BY created_at DESC LIMIT 500').fetchall()
        else:
            rows = con.execute('SELECT * FROM schedule_waitlist WHERE status=? ORDER BY preferred_date,preferred_time,created_at', (status,)).fetchall()
        return [redact_customer(row, user, 'waitlist') for row in rows]

    @writing
    def add_schedule_waitlist(self, con, user, actor, data):
        require(user, 'ext.waitlist.create')
        if not data['customer_name'].strip():
            raise SchedulingError(400, '客户姓名不能为空')
        if data['phone_tail']:
            require(user, 'ext.waitlist.phone_edit')
            if not (data['phone_tail'].isascii() and data['phone_tail'].isdigit()):
                raise SchedulingError(400, '手机尾号只能包含数字')
        if data['notes']:
            require(user, 'ext.waitlist.notes_edit')
        now = datetime.now().isoformat(timespec='seconds')
        item_id = uuid.uuid4().hex
        con.execute('\n        INSERT INTO schedule_waitlist(id,customer_name,phone_tail,service_id,preferred_date,preferred_time,notes,created_by,created_at,updated_at)\n        VALUES(?,?,?,?,?,?,?,?,?,?)\n    ', (item_id, data['customer_name'].strip(), data['phone_tail'], data['service_id'], data['preferred_date'], data['preferred_time'], data['notes'], user['id'], now, now))
        self.notify(con, '新增候补预约', f"客户：{data['customer_name']}；日期：{data['preferred_date'] or '未指定'}", 'waitlist', source_key=f'waitlist:{item_id}')
        self.audit(con, '新增候补预约', data['customer_name'], actor, 'waitlist', item_id, None, dict(data))
        return {'ok': True, 'id': item_id}

    @writing
    def update_schedule_waitlist(self, con, user, actor, item_id, status):
        require(user, 'ext.waitlist.status_change')
        if status not in ('waiting', 'booked', 'cancelled'):
            raise SchedulingError(400, '候补状态无效')
        row = con.execute('SELECT * FROM schedule_waitlist WHERE id=?', (item_id,)).fetchone()
        if not row:
            raise SchedulingError(404, '候补记录不存在')
        con.execute('UPDATE schedule_waitlist SET status=?,updated_at=? WHERE id=?', (status, datetime.now().isoformat(timespec='seconds'), item_id))
        self.audit(con, '更新候补预约', f"{row['customer_name']} -> {status}", actor, 'waitlist', item_id, dict(row), {'status': status})
        return {'ok': True}

