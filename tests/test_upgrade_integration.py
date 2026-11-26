# -*- coding: utf-8 -*-
"""统一升级集成测试：所有写入都发生在临时项目副本。"""
import os, pathlib, shutil, sys, tempfile
from fastapi.testclient import TestClient

from application_test_support import unload_test_application


def test_barcode_schedule_auth_and_transfer():
    source = pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="store_upgrade_test_") as td:
        project = pathlib.Path(td) / "project"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "build", "dist", "release", "logs"
        ))
        sys.path.insert(0, str(project))
        old_cwd = os.getcwd()
        os.chdir(project)
        try:
            import importlib
            sys.modules.pop("main", None)
            main = importlib.import_module("main")
            main.init_db()
            admin = TestClient(main.app)
            assert admin.get("/api/health").status_code == 200
            assert admin.get("/schedule").status_code == 200
            assert admin.post("/api/auth/bootstrap", json={
                "username": "admin1", "display_name": "管理员", "password": "AdminPass123"
            }).status_code == 200

            # HTTPS 证书路径按项目目录解析，不依赖启动时工作目录。
            cert_dir = project / "data" / "certs"
            cert_dir.mkdir(parents=True, exist_ok=True)
            (cert_dir / "server.crt").write_text("test certificate", encoding="utf-8")
            (cert_dir / "server.key").write_text("test key", encoding="utf-8")
            settings = admin.put("/api/settings", json={
                "port":8000,"domain":"ku.home.arpa","access_ip":"","https":True,
                "ssl_certfile":"data/certs/server.crt","ssl_keyfile":"data/certs/server.key",
                "auto_backup":True,"backup_time":"18:00","backup_keep_days":30,
                "backup_dir":"data/backups","purchase_dir":"data/purchase","default_threshold":5
            })
            assert settings.status_code == 200, settings.text

            # 条码保留原始字符；非空重复拦截，空值可重复。
            base = {"brand":"测试", "barcode":" 000123 ", "name":"条码测试A", "spec":"", "category":"测试", "location":"", "unit":"个", "opening":1}
            assert admin.post("/api/products", json={**base, "id":"BC-A"}).status_code == 200
            product_audit = admin.get("/api/audit")
            assert any(x["entity_type"] == "product" and x["entity_id"] == "BC-A" for x in product_audit.json())
            rows = admin.get("/api/products?q=000123").json()
            assert any(x["id"] == "BC-A" and x["barcode"] == " 000123 " for x in rows)
            dup = admin.post("/api/products", json={**base, "id":"BC-B", "name":"条码测试B"})
            assert dup.status_code == 409
            assert admin.post("/api/products", json={**base, "id":"BC-C", "barcode":"", "name":"空条码C"}).status_code == 200
            assert admin.post("/api/products", json={**base, "id":"BC-D", "barcode":"", "name":"空条码D"}).status_code == 200
            long_code = "X" * 5000
            assert admin.post("/api/products", json={**base, "id":"BC-LONG", "barcode":long_code, "name":"长条码商品"}).status_code == 200
            special_code = "AB%_C\\D"
            assert admin.post("/api/products", json={**base, "id":"BC-SPECIAL", "barcode":special_code, "name":"特殊字符条码"}).status_code == 200
            special_search = admin.get("/api/products?q=" + __import__('urllib.parse').parse.quote(special_code)).json()
            assert any(x["id"] == "BC-SPECIAL" for x in special_search)

            today = __import__('datetime').date.today().isoformat()
            future = (__import__('datetime').date.today() + __import__('datetime').timedelta(days=1)).isoformat()
            payload = {
                "version":10,
                "businessHours":{"start":"09:00","end":"18:00"},
                "staff":[
                    {"id":"s1","name":"美容师甲","shifts":[{"start":"09:00","end":"18:00"}]},
                    {"id":"s2","name":"美容师乙","shifts":[{"start":"09:00","end":"18:00"}]}
                ],
                "services":[{"id":"svc1","name":"护理","duration":60,"buffer":0,"enabled":True}],
                "appointments":[
                    {"id":"a1","date":today,"staffId":"s1","serviceId":"svc1","start":"10:00","duration":60,"buffer":0,"extend":0,"status":"pending","customer":"甲"},
                    {"id":"a2","date":today,"staffId":"s2","serviceId":"svc1","start":"13:00","duration":60,"buffer":0,"extend":0,"status":"pending","customer":"乙"},
                    {"id":"a3","date":future,"staffId":"s1","serviceId":"svc1","start":"14:00","duration":60,"buffer":0,"extend":0,"status":"pending","customer":"未来预约"}
                ],
                "bookingGroups":[],"daysOff":{},"daySchedules":{},"permanentRestStaffIds":[],"scheduleRules":[],"rotation":{"enabled":False,"mode":"daily","anchorDate":today,"manualOffset":0}
            }
            migrated = admin.post("/api/schedule/migrate", json={"payload":payload})
            assert migrated.status_code == 200, migrated.text
            revision = migrated.json()["revision"]
            transfer = admin.post("/api/schedule/appointments/transfer", json={
                "appointment_id":"a1","target_staff_id":"s2","segment_start":"10:20","segment_end":"10:40","expected_revision":revision
            })
            assert transfer.status_code == 200, transfer.text
            assert transfer.json()["revision"] == revision + 1
            segments = transfer.json()["payload"]["appointments"]
            assert len(segments) == 5
            assert [(x["staffId"],x["start"],x["duration"]) for x in segments[:3]] == [
                ("s1","10:00",20),("s2","10:20",20),("s1","10:40",20)
            ]
            assert len(admin.get("/api/schedule/transfers").json()) == 1
            stale = admin.put("/api/schedule/state", json={"payload":payload,"revision":revision})
            assert stale.status_code == 409

            # 在线备份与恢复使用临时数据库副本；恢复前会自动再备份当前库。
            assert admin.post("/api/backup").status_code == 200
            backup_audit = admin.get("/api/audit").json()
            assert any(x["action"] == "创建数据库备份" for x in backup_audit)
            backup_list = admin.get("/api/backup")
            assert backup_list.status_code == 200 and backup_list.json()
            backup_name = backup_list.json()[0]["name"] if isinstance(backup_list.json()[0], dict) else backup_list.json()[0]
            backup_download = admin.get(f"/api/backup/download/{backup_name}")
            assert backup_download.status_code == 200 and backup_download.content[:16] == b"SQLite format 3\x00"
            restored = admin.post("/api/restore", files={"file": ("test.db", backup_download.content, "application/octet-stream")})
            assert restored.status_code == 200, restored.text
            assert admin.get("/api/auth/me").status_code == 401
            assert admin.post("/api/auth/login", json={
                "username": "admin1", "password": "AdminPass123"
            }).status_code == 200
            invalid_restore = admin.post("/api/restore", files={"file": ("broken.db", b"not a sqlite database", "application/octet-stream")})
            assert invalid_restore.status_code == 400
            restore_audit = admin.get("/api/audit").json()
            assert any(x["action"] == "恢复数据库" for x in restore_audit)

            # 员工账号仅能编辑本人当天预约；不能改他人或全局资料。
            group = admin.post("/api/permission-groups", json={
                "name":"美容师排班", "permissions":{"schedule_view":True,"schedule_self_manage":True}
            })
            assert group.status_code == 201, group.text
            created = admin.post("/api/users", json={
                "username":"staff1","display_name":"美容师甲","password":"StaffPass123",
                "group_ids":[group.json()["id"]]
            })
            assert created.status_code == 200, created.text
            user_id = created.json()["id"]
            link = admin.put("/api/schedule/staff-links", json={"user_id":user_id,"staff_id":"s1"})
            assert link.status_code == 200, link.text
            admin.post("/api/auth/logout")
            staff = TestClient(main.app, base_url="https://testserver")
            login = staff.post("/api/auth/login", json={"username":"staff1","password":"StaffPass123"})
            assert login.status_code == 200, login.text
            assert staff.get("/api/reports/operations").status_code == 403
            state_response = staff.get("/api/schedule/state")
            assert state_response.status_code == 200, state_response.text
            state = state_response.json()
            changed = dict(state["payload"])
            changed["appointments"] = [dict(x) for x in changed["appointments"]]
            own = next(x for x in changed["appointments"] if x["staffId"] == "s1")
            own["note"] = "本人更新"
            ok = staff.put("/api/schedule/state", json={"payload":changed,"revision":state["revision"]})
            assert ok.status_code == 200, ok.text
            state2 = staff.get("/api/schedule/state").json()
            forbidden = dict(state2["payload"])
            forbidden["staff"] = [dict(x) for x in forbidden["staff"]]
            forbidden["staff"][1]["name"] = "擅自修改他人档案"
            denied = staff.put("/api/schedule/state", json={"payload":forbidden,"revision":state2["revision"]})
            assert denied.status_code == 403, denied.text
            state3 = staff.get("/api/schedule/state").json()
            future_change = dict(state3["payload"])
            future_change["appointments"] = [dict(x) for x in future_change["appointments"]]
            next_day = next(x for x in future_change["appointments"] if x["date"] == future)
            next_day["note"] = "不允许修改未来日期"
            denied_future = staff.put("/api/schedule/state", json={"payload":future_change,"revision":state3["revision"]})
            assert denied_future.status_code == 403, denied_future.text
        finally:
            os.chdir(old_cwd)
            sys.path.remove(str(project))
            unload_test_application()
