"""Transaction boundary shared by scheduling, customer and waitlist adapters."""
from contextlib import closing
from datetime import datetime
from functools import wraps
from typing import Protocol, Mapping, Any
import uuid


def _transaction(write):
    def decorate(method):
        @wraps(method)
        def call(self, *args):
            with closing(self.connect()) as con:
                if write:
                    con.execute('BEGIN IMMEDIATE')
                with con:
                    return method(self, con, *args)
        return call
    return decorate


reading = _transaction(False)
writing = _transaction(True)


class SQLiteRepository:
    def __init__(self, connect, audit_writer):
        self.connect = connect
        self.audit = audit_writer.write

    @staticmethod
    def notify(con, title, body='', kind='system', recipient_user_id=None, target_url='', source_key=None):
        nid = uuid.uuid4().hex
        con.execute('''INSERT OR IGNORE INTO app_notifications
            (id,recipient_user_id,kind,title,body,target_url,source_key,created_at)
            VALUES(?,?,?,?,?,?,?,?)''', (nid,recipient_user_id,kind,title,body,target_url,source_key,
                                        datetime.now().isoformat(timespec='seconds')))
        return nid


class SchedulingRepository(Protocol):
    def get_schedule_state(self, user, actor): ...
    def migrate_schedule(self, user, actor, data: Mapping): ...
    def put_schedule_state(self, user, actor, data: Mapping): ...
    def list_schedule_staff_links(self, user, actor): ...
    def put_schedule_staff_link(self, user, actor, data: Mapping): ...
    def delete_schedule_staff_link(self, user, actor, user_id): ...
    def get_schedule_transfers(self, user, actor, date_from, date_to): ...
    def transfer_schedule_segment(self, user, actor, data: Mapping): ...
    def change_appointment_status(self, user, actor, appointment_id, data: Mapping): ...
    def schedule_workload(self, user, actor, date_from, date_to): ...
    def list_schedule_requests(self, user, actor, status): ...
    def create_schedule_request(self, user, actor, data: Mapping): ...
    def review_schedule_request(self, user, actor, request_id, data: Mapping): ...


class CustomerRepository(Protocol):
    def list_customers(self, user, actor, q, limit): ...
    def save_customer(self, user, actor, data: Mapping): ...
    def delete_customer(self, user, actor, customer_id): ...


class WaitlistRepository(Protocol):
    def list_schedule_waitlist(self, user, actor, status): ...
    def add_schedule_waitlist(self, user, actor, data: Mapping): ...
    def update_schedule_waitlist(self, user, actor, item_id, status): ...
