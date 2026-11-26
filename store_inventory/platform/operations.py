"""Application services for platform operations backed by injected SQLite connections.

HTTP, authentication and permission decisions remain at the composition boundary.
Every method owns and closes only the connection it opens.
"""
from __future__ import annotations
from datetime import datetime, timedelta


class PlatformOperations:
    def __init__(self, connection_factory):
        self._connect = connection_factory

    def list_notifications(self, user_id: str, limit: int = 100):
        limit = max(1, min(int(limit), 300))
        con = self._connect()
        try:
            rows = con.execute("""SELECT * FROM app_notifications
                WHERE recipient_user_id IS NULL OR recipient_user_id=?
                ORDER BY created_at DESC LIMIT ?""", (user_id, limit)).fetchall()
            return [dict(row) for row in rows]
        finally:
            con.close()

    def mark_notification_read(self, notification_id: str, user_id: str, now: str | None = None) -> bool:
        con = self._connect()
        try:
            cur = con.execute("""UPDATE app_notifications SET read_at=?
                WHERE id=? AND (recipient_user_id IS NULL OR recipient_user_id=?)""",
                (now or datetime.now().isoformat(timespec='seconds'), notification_id, user_id))
            con.commit()
            return bool(cur.rowcount)
        finally:
            con.close()

    def mark_all_notifications_read(self, user_id: str, now: str | None = None) -> None:
        con = self._connect()
        try:
            con.execute("""UPDATE app_notifications SET read_at=?
                WHERE read_at IS NULL AND (recipient_user_id IS NULL OR recipient_user_id=?)""",
                (now or datetime.now().isoformat(timespec='seconds'), user_id))
            con.commit()
        finally:
            con.close()

    def heartbeat(self, *, device_id: str, user_id: str, device_name: str,
                  platform: str, ip_address: str = '', now: str | None = None):
        now = now or datetime.now().isoformat(timespec='seconds')
        con = self._connect()
        try:
            con.execute("""INSERT INTO device_presence(
                device_id,user_id,device_name,platform,ip_address,first_seen,last_seen)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET
                user_id=excluded.user_id,device_name=excluded.device_name,
                platform=excluded.platform,ip_address=excluded.ip_address,last_seen=excluded.last_seen""",
                (device_id, user_id, device_name, platform, ip_address, now, now))
            con.commit()
            return {'ok': True, 'last_seen': now}
        finally:
            con.close()

    def list_online_devices(self, now: datetime | None = None):
        cutoff = ((now or datetime.now()) - timedelta(minutes=3)).isoformat(timespec='seconds')
        con = self._connect()
        try:
            rows = con.execute("""SELECT d.device_id,d.device_name,d.platform,d.ip_address,
                d.first_seen,d.last_seen,u.username,u.display_name
                FROM device_presence d LEFT JOIN users u ON u.id=d.user_id
                WHERE d.last_seen>=? ORDER BY d.last_seen DESC""", (cutoff,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            con.close()

    def list_audit_logs(self, limit: int = 200):
        con = self._connect()
        try:
            rows = con.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",
                                (max(1, min(int(limit), 1000)),)).fetchall()
            return [dict(row) for row in rows]
        finally:
            con.close()
