import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from store_inventory.platform.operations import PlatformOperations


def make_service(tmp_path):
    path = tmp_path / 'platform.db'
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript('''
    CREATE TABLE app_notifications(id TEXT PRIMARY KEY, recipient_user_id TEXT, kind TEXT, title TEXT, body TEXT, target_url TEXT, source_key TEXT, created_at TEXT, read_at TEXT);
    CREATE TABLE device_presence(device_id TEXT PRIMARY KEY,user_id TEXT,device_name TEXT,platform TEXT,ip_address TEXT,first_seen TEXT,last_seen TEXT);
    CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT,display_name TEXT);
    CREATE TABLE audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT,action TEXT,detail TEXT);
    INSERT INTO users VALUES('u1','alice','Alice');
    INSERT INTO app_notifications VALUES('n1',NULL,'system','broadcast','','','','2026-11-15T10:00:00',NULL);
    INSERT INTO app_notifications VALUES('n2','u2','system','private','','','','2026-11-15T10:01:00',NULL);
    INSERT INTO app_notifications VALUES('n3','u1','system','mine','','','','2026-11-15T10:02:00',NULL);
    INSERT INTO audit_logs(time,action,detail) VALUES('2026-11-15T10:00:00','login','ok');
    ''')
    con.commit(); con.close()
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    return PlatformOperations(connect)


def test_notification_visibility_and_read_scope(tmp_path):
    service = make_service(tmp_path)
    rows = service.list_notifications('u1')
    assert {r['id'] for r in rows} == {'n1', 'n3'}
    assert service.mark_notification_read('n2', 'u1') is False
    assert service.mark_notification_read('n3', 'u1') is True
    service.mark_all_notifications_read('u1')
    rows = service.list_notifications('u1')
    assert all(r['read_at'] for r in rows)


def test_device_heartbeat_upsert_and_online_window(tmp_path):
    service = make_service(tmp_path)
    now = datetime(2026, 11, 15, 12, 0, 0)
    result = service.heartbeat(device_id='d1', user_id='u1', device_name='Phone',
                               platform='iOS', ip_address='127.0.0.1', now=now.isoformat())
    assert result['ok'] is True
    devices = service.list_online_devices(now=now + timedelta(minutes=2))
    assert len(devices) == 1 and devices[0]['username'] == 'alice'
    assert service.list_online_devices(now=now + timedelta(minutes=5)) == []


def test_audit_list_limit_is_bounded(tmp_path):
    service = make_service(tmp_path)
    assert len(service.list_audit_logs(0)) == 1
    assert service.list_audit_logs(5000)[0]['action'] == 'login'
