# -*- coding: utf-8 -*-
"""V1.3.1 新增后台能力的隔离数据库集成测试。"""
import importlib
import os
import pathlib
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
import json

from fastapi.testclient import TestClient

from application_test_support import unload_test_application


def test_notifications_devices_customers_waitlist_and_schedule_approvals():
    source = pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v131_features_") as td:
        project = pathlib.Path(td) / "project"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", "build", "dist", "release", "logs"
        ))
        old = os.getcwd()
        os.chdir(project)
        sys.path.insert(0, str(project))
        try:
            sys.modules.pop("main", None)
            main = importlib.import_module("main")
            main.DB_PATH = project / "data" / "test.db"
            main.init_db()
            client = TestClient(main.app)
            boot = client.post("/api/auth/bootstrap", json={
                "username": "admin1", "display_name": "系统管理员", "password": "AdminPass123"
            })
            assert boot.status_code == 200, boot.text

            # 预约冲突：服务端拒绝新增的同美容师重叠时段。
            today = "2026-11-13"
            payload = {
                "version": 10,
                "staff": [
                    {"id": "staff-demo", "name": "测试美容师", "shifts": [{"start": "09:00", "end": "18:00"}]},
                    {"id": "staff-other", "name": "其他美容师", "shifts": [{"start": "10:00", "end": "18:00"}]}
                ],
                "services": [{"id": "svc1", "name": "护理", "duration": 60, "buffer": 0, "enabled": True}],
                "appointments": [{"id": "appt-1", "date": today, "staffId": "staff-demo", "serviceId": "svc1", "start": "10:00", "duration": 60, "buffer": 0, "extend": 0, "status": "pending", "customer": "客户一"}],
                "bookingGroups": [], "daysOff": {}, "daySchedules": {}, "permanentRestStaffIds": [],
                "scheduleRules": [], "rotation": {"enabled": False, "mode": "daily", "anchorDate": today, "manualOffset": 0},
                "businessHours": {"start": "09:00", "end": "18:00"}
            }
            migrated = client.post("/api/schedule/migrate", json={"payload": payload})
            assert migrated.status_code == 200, migrated.text
            seeded_customers = client.get("/api/customers?q=客户一")
            assert seeded_customers.status_code == 200
            assert any(x["name"] == "客户一" for x in seeded_customers.json())
            changed = dict(payload)
            changed["appointments"] = list(payload["appointments"]) + [{
                "id": "appt-2", "date": today, "staffId": "staff-demo", "serviceId": "svc1", "start": "10:30", "duration": 30, "buffer": 0, "extend": 0, "status": "pending", "customer": "客户二"
            }]
            conflict = client.put("/api/schedule/state", json={"payload": changed, "revision": migrated.json()["revision"]})
            assert conflict.status_code == 409 and "预约时间冲突" in conflict.json()["detail"]
            workload = client.get(f"/api/schedule/workload?date_from={today}&date_to={today}")
            assert workload.status_code == 200
            assert workload.json()["items"][0]["appointment_count"] == 1
            assert workload.json()["items"][0]["service_minutes"] == 60

            # 员工账号的工作负荷接口仅返回本人绑定的美容师，不泄露其他员工数据。
            group = client.post("/api/permission-groups", json={
                "name": "美容师个人排班", "permissions": {"schedule_view": True, "schedule_self_manage": True}
            })
            assert group.status_code == 201, group.text
            staff_create = client.post("/api/users", json={
                "username": "staff_scope", "display_name": "本人美容师", "password": "StaffPass123",
                "group_ids": [group.json()["id"]]
            })
            assert staff_create.status_code == 200, staff_create.text
            staff_user_id = staff_create.json()["id"]
            assert client.put("/api/schedule/staff-links", json={"user_id": staff_user_id, "staff_id": "staff-demo"}).status_code == 200
            client.post("/api/auth/logout")
            staff_client = TestClient(main.app)
            assert staff_client.post("/api/auth/login", json={"username": "staff_scope", "password": "StaffPass123"}).status_code == 200
            scoped = staff_client.get(f"/api/schedule/workload?date_from={today}&date_to={today}")
            assert scoped.status_code == 200, scoped.text
            assert [x["staff_id"] for x in scoped.json()["items"]] == ["staff-demo"]
            assert staff_client.get("/api/audit").status_code == 403
            client = TestClient(main.app)
            assert client.post("/api/auth/login", json={"username": "admin1", "password": "AdminPass123"}).status_code == 200

            # 预约前 24h / 2h 内部提醒生成且去重。
            future_date = (date.today() + timedelta(days=3)).isoformat()
            con = main.db()
            current_state = con.execute("SELECT payload FROM schedule_state WHERE id=1").fetchone()
            reminder_payload = json.loads(current_state["payload"])
            reminder_payload["appointments"].append({
                "id": "reminder-appt", "date": future_date, "staffId": "staff-demo", "serviceId": "svc1",
                "start": "12:00", "duration": 60, "buffer": 0, "extend": 0, "status": "pending",
                "customer": "提醒客户", "phoneTail": "1234"
            })
            con.execute("UPDATE schedule_state SET payload=? WHERE id=1", (json.dumps(reminder_payload, ensure_ascii=False),))
            con.commit(); con.close()
            appointment_at = datetime.fromisoformat(f"{future_date}T12:00:00")
            assert main.run_appointment_reminders(appointment_at - timedelta(hours=23)) == 1
            assert main.run_appointment_reminders(appointment_at - timedelta(hours=22)) == 0
            assert main.run_appointment_reminders(appointment_at - timedelta(hours=1)) == 1

            # 消息中心：生成、查询、标记已读。
            con = main.db()
            main.create_notification(con, "测试通知", "消息正文", "system", source_key="test-notice-1")
            con.commit(); con.close()
            messages = client.get("/api/notifications")
            assert messages.status_code == 200 and messages.json()
            message_id = messages.json()[0]["id"]
            assert client.post(f"/api/notifications/{message_id}/read").status_code == 200
            assert client.post("/api/notifications/read-all").status_code == 200

            # 在线设备心跳与管理员设备列表。
            heartbeat = client.post("/api/devices/heartbeat", json={
                "device_id": "device-test-0001", "device_name": "测试安卓手机", "platform": "Android"
            })
            assert heartbeat.status_code == 200, heartbeat.text
            devices = client.get("/api/devices")
            assert devices.status_code == 200
            assert any(row["device_id"] == "device-test-0001" for row in devices.json())

            # 客户档案新增、查询、删除。
            customer = client.post("/api/customers", json={
                "name": "测试客户", "phone_tail": "12345678901", "notes": "偏好下午"
            })
            assert customer.status_code == 200, customer.text
            customer_id = customer.json()["id"]
            assert any(x["id"] == customer_id for x in client.get("/api/customers?q=测试客户").json())
            assert client.delete(f"/api/customers/{customer_id}").status_code == 200

            # 候补预约新增、状态变更。
            wait = client.post("/api/schedule/waitlist", json={
                "customer_name": "候补客户", "phone_tail": "12345678901",
                "service_id": "svc1", "preferred_date": "2026-11-14", "preferred_time": "14:00"
            })
            assert wait.status_code == 200, wait.text
            wait_id = wait.json()["id"]
            assert client.patch(f"/api/schedule/waitlist/{wait_id}?status=booked").status_code == 200

            # 员工休息/换班申请提交与审批。
            request_result = client.post("/api/schedule/requests", json={
                "request_type": "休息申请", "staff_id": "staff-demo", "request_date": "2026-11-15",
                "start_time": "12:00", "end_time": "13:00", "details": "个人安排"
            })
            assert request_result.status_code == 200, request_result.text
            request_id = request_result.json()["id"]
            approved = client.patch(f"/api/schedule/requests/{request_id}", json={
                "status": "approved", "review_note": "已批准"
            })
            assert approved.status_code == 200, approved.text
            assert approved.json()["schedule_revision"] == 2
            approved_state = client.get("/api/schedule/state").json()["payload"]
            assert approved_state["daySchedules"]["2026-11-15"]["staff-demo"] == [
                {"start": "09:00", "end": "12:00"}, {"start": "13:00", "end": "18:00"}
            ]
            overlap_request = client.post("/api/schedule/requests", json={
                "request_type": "休息申请", "staff_id": "staff-demo", "request_date": today,
                "start_time": "10:30", "end_time": "10:45", "details": "测试与预约冲突"
            })
            assert overlap_request.status_code == 200
            overlap_approval = client.patch(f"/api/schedule/requests/{overlap_request.json()['id']}", json={"status": "approved"})
            assert overlap_approval.status_code == 409 and "预约" in overlap_approval.json()["detail"]

            # 换班审批会交换两位美容师当天的工作时段并更新 revision。
            swap_request = client.post("/api/schedule/requests", json={
                "request_type": "换班申请", "staff_id": "staff-demo", "target_staff_id": "staff-other",
                "request_date": "2026-11-15", "details": "双方确认互换班次"
            })
            assert swap_request.status_code == 200, swap_request.text
            swap_approved = client.patch(f"/api/schedule/requests/{swap_request.json()['id']}", json={"status": "approved"})
            assert swap_approved.status_code == 200, swap_approved.text
            assert swap_approved.json()["schedule_revision"] == 3
            swapped = client.get("/api/schedule/state").json()["payload"]["daySchedules"]["2026-11-15"]
            assert swapped["staff-demo"] == [{"start": "10:00", "end": "18:00"}]
            assert swapped["staff-other"] == [{"start": "09:00", "end": "12:00"}, {"start": "13:00", "end": "18:00"}]

            # 审计记录有操作者和实体信息。
            audit = client.get("/api/audit")
            assert audit.status_code == 200
            assert any(row["entity_type"] == "customer" for row in audit.json())
            assert any(row["entity_type"] == "schedule_request" for row in audit.json())

            # 库存预警 API 可用，且返回结构稳定。
            alerts = client.get("/api/inventory/alerts")
            assert alerts.status_code == 200
            assert "count" in alerts.json() and "items" in alerts.json()

            # 经营报表：汇总库存流水、预约、客户和美容师服务量。
            report = client.get(f"/api/reports/operations?date_from={today}&date_to={today}")
            assert report.status_code == 200, report.text
            assert report.json()["appointments"]["count"] >= 1
            assert "transactions" in report.json()["inventory"]
            assert "new_in_period" in report.json()["customers"]
        finally:
            os.chdir(old)
            sys.path.remove(str(project))
            unload_test_application()


def test_schedule_request_target_staff_column_migrates_idempotently():
    source = pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v131_request_migration_") as td:
        project = pathlib.Path(td) / "project"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", "build", "dist", "release", "logs"
        ))
        old = os.getcwd(); os.chdir(project); sys.path.insert(0, str(project))
        try:
            sys.modules.pop("main", None)
            main = importlib.import_module("main")
            main.DB_PATH = project / "data" / "legacy.db"
            main.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            con = __import__("sqlite3").connect(main.DB_PATH)
            con.execute("""CREATE TABLE schedule_requests(
                id TEXT PRIMARY KEY, request_type TEXT NOT NULL, staff_id TEXT NOT NULL,
                user_id TEXT NOT NULL, request_date TEXT NOT NULL,
                start_time TEXT NOT NULL DEFAULT '', end_time TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
                reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at TEXT,
                review_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            con.commit(); con.close()
            main.init_db(); main.init_db()
            con = main.db()
            cols = {x[1] for x in con.execute("PRAGMA table_info(schedule_requests)").fetchall()}
            assert "target_staff_id" in cols
            con.close()
        finally:
            os.chdir(old); sys.path.remove(str(project)); unload_test_application()
