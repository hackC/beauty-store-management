# -*- coding: utf-8 -*-
"""Exercise the fresh-install API on a disposable copy."""
import importlib
from pathlib import Path
import shutil
import sys

from fastapi.testclient import TestClient


def test_blank_inventory_smoke(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parent
    target = tmp_path / "project"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(
        "data", "release", "logs", "dist", "build", "__pycache__", ".pytest_cache"))
    monkeypatch.syspath_prepend(str(target))
    monkeypatch.chdir(target)
    monkeypatch.delitem(sys.modules, "main", raising=False)
    main = importlib.import_module("main")
    try:
        _exercise_api(main)
    finally:
        sys.modules.pop("main", None)


def _exercise_api(main):
    main.init_db()
    client=TestClient(main.app)
    assert client.get('/api/health').status_code==200
    assert client.get('/').status_code==200
    # 当前正式系统启用了账号认证，测试先在临时数据库中初始化管理员。
    boot=client.post('/api/auth/bootstrap',json={'username':'smokeadmin','display_name':'烟雾测试管理员','password':'SmokePass123'})
    assert boot.status_code==200,boot.text
    ps=client.get('/api/products').json(); assert ps==[], ps
    # product create
    r=client.post('/api/products',json={'id':'TEST-001','brand':'测试品牌','name':'测试商品','spec':'1个','category':'测试','location':'测试区','unit':'个','opening':10,'threshold':2})
    assert r.status_code==200,r.text
    # inbound 5
    r=client.post('/api/submit',json={'type':'入库','external_no':'TEST-IN','operator':'测试','request_id':'smoke-in','items':[{'product_id':'TEST-001','quantity':5,'note':''}]})
    assert r.status_code==200,r.text
    # outbound 3
    r=client.post('/api/submit',json={'type':'出库','external_no':'TEST-OUT','operator':'测试','request_id':'smoke-out','items':[{'product_id':'TEST-001','quantity':3,'note':''}]})
    assert r.status_code==200,r.text
    # stocktake to zero (valid and should create -12 adjustment from current 12)
    r=client.post('/api/submit',json={'type':'盘点调整','external_no':'TEST-STOCKTAKE','operator':'测试','request_id':'smoke-stock','items':[{'product_id':'TEST-001','quantity':0,'note':'实盘为零'}]})
    assert r.status_code==200,r.text
    p=[x for x in client.get('/api/products').json() if x['id']=='TEST-001'][0]
    assert p['stock']==0,p
    # insufficient stock blocked
    r=client.post('/api/submit',json={'type':'出库','operator':'测试','request_id':'smoke-out-fail','items':[{'product_id':'TEST-001','quantity':1}]})
    assert r.status_code==400,r.text
    # idempotency
    r1=client.post('/api/submit',json={'type':'入库','request_id':'same-key','items':[{'product_id':'TEST-001','quantity':2}]})
    r2=client.post('/api/submit',json={'type':'入库','request_id':'same-key','items':[{'product_id':'TEST-001','quantity':2}]})
    assert r1.status_code==200 and r2.status_code==200 and r2.json().get('duplicate') is True,(r1.text,r2.text)
    # xlsx exports and backup
    for endpoint in ['/api/export/products','/api/export/transactions','/api/export/purchase']:
        ex=client.get(endpoint); assert ex.status_code==200 and ex.content[:2]==b'PK', (endpoint,ex.status_code,ex.content[:10])
    assert client.post('/api/backup').status_code==200
    assert len(client.get('/api/backup').json())>=1
    st=client.get('/api/settings'); assert st.status_code==200 and st.json()['port']==8000
    print('PASS: health, empty database startup, product create, inbound, outbound, stocktake zero, insufficient-stock guard, idempotency, XLSX exports, SQLite backup')
