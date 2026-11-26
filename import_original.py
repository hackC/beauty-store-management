# -*- coding: utf-8 -*-
"""只读迁移原始 XLSM 数据；不会运行宏或修改源文件。"""
import sys, json, sqlite3, hashlib
from pathlib import Path
from datetime import datetime, date, time
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'inventory.db'
SRC=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'source'/'门店库存数字管理系统.xlsm'
if not SRC.exists(): raise SystemExit(f'找不到原始工作簿：{SRC}')
wb=load_workbook(SRC,data_only=True,read_only=True,keep_vba=False)
required=['商品档案','库存流水','历史归档','基础设置']
missing=[s for s in required if s not in wb.sheetnames]
if missing: raise SystemExit('缺少工作表：'+','.join(missing))
products=[]
for row in wb['商品档案'].iter_rows(min_row=4,values_only=True):
    pid=row[1] if len(row)>1 else None
    if not pid: continue
    products.append({'id':str(pid).strip(),'brand':str(row[2] or '').strip(),'name':str(row[3] or '').strip(),'spec':str(row[4] or '').strip(),'category':str(row[5] or '').strip(),'location':str(row[6] or '').strip(),'unit':str(row[7] or '').strip(),'opening':float(row[8] or 0),'current_excel':float(row[9] or 0),'threshold':None if row[10] in (None,'') else float(row[10])})
ids=[p['id'] for p in products]
if len(ids)!=len(set(ids)): raise SystemExit('商品ID存在重复，迁移已停止。')
if any(not p['name'] for p in products): raise SystemExit('存在商品名称为空的商品，迁移已停止。')
flows=[]
for sn in ['库存流水','历史归档']:
    for row in wb[sn].iter_rows(min_row=4,values_only=True):
        if len(row)>1 and row[1]:
            flows.append({'id':str(row[1]),'time':row[2].isoformat(sep=' ') if hasattr(row[2],'isoformat') else str(row[2] or datetime.now().isoformat(sep=' ')),'type':str(row[3] or ''),'doc_no':str(row[4] or ''),'product_id':str(row[5] or ''),'product_name':str(row[6] or ''),'qty_in':float(row[7] or 0),'qty_out':float(row[8] or 0),'qty_adjust':float(row[9] or 0),'operator':str(row[10] or ''),'note':str(row[11] or ''),'source_sheet':sn})
known=set(ids)
unknown=[t for t in flows if t['product_id'] not in known]
if unknown: raise SystemExit('存在引用不存在商品ID的流水，迁移已停止：'+str([x['id'] for x in unknown]))
agg={}
for t in flows: agg[t['product_id']]=agg.get(t['product_id'],0)+t['qty_in']-t['qty_out']+t['qty_adjust']
mis=[]
for p in products:
    calc=p['opening']+agg.get(p['id'],0)
    if abs(calc-p['current_excel'])>1e-6: mis.append({'id':p['id'],'excel_current':p['current_excel'],'recalculated':calc,'difference':calc-p['current_excel']})
report={'source':SRC.name,'source_sha256':hashlib.sha256(SRC.read_bytes()).hexdigest(),'products':len(products),'transactions':len(flows),'active_flow_count':sum(x['source_sheet']=='库存流水' for x in flows),'archive_flow_count':sum(x['source_sheet']=='历史归档' for x in flows),'stock_mismatches':mis,'imported_at':datetime.now().isoformat(timespec='seconds')}
if mis:
    (ROOT/'migration_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    raise SystemExit('库存核对存在差异，已暂停导入，请查看 migration_report.json')
con=sqlite3.connect(DB); con.execute('PRAGMA foreign_keys=ON')
con.executescript('''CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,brand TEXT DEFAULT '',name TEXT NOT NULL,spec TEXT DEFAULT '',category TEXT DEFAULT '',location TEXT DEFAULT '',unit TEXT DEFAULT '',opening REAL NOT NULL DEFAULT 0,threshold REAL,status TEXT NOT NULL DEFAULT '正常',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,type TEXT NOT NULL,external_no TEXT DEFAULT '',operator TEXT DEFAULT '',note TEXT DEFAULT '',created_at TEXT NOT NULL,request_id TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY,doc_id TEXT NOT NULL,time TEXT NOT NULL,type TEXT NOT NULL,doc_no TEXT NOT NULL,product_id TEXT NOT NULL,product_name TEXT NOT NULL,qty_in REAL NOT NULL DEFAULT 0,qty_out REAL NOT NULL DEFAULT 0,qty_adjust REAL NOT NULL DEFAULT 0,operator TEXT DEFAULT '',note TEXT DEFAULT '',actual_count REAL,FOREIGN KEY(doc_id) REFERENCES documents(id),FOREIGN KEY(product_id) REFERENCES products(id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT NOT NULL,action TEXT NOT NULL,detail TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
if con.execute('SELECT COUNT(*) FROM products').fetchone()[0]:
    raise SystemExit('目标数据库已有商品数据，禁止重复导入。')
now=datetime.now().isoformat(timespec='seconds')
for p in products:
    con.execute('INSERT INTO products(id,brand,name,spec,category,location,unit,opening,threshold,status,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(p['id'],p['brand'],p['name'],p['spec'],p['category'],p['location'],p['unit'],p['opening'],p['threshold'],'正常',1,now,now))
# Preserve the source document grouping; legacy rows may share "无单号", so each legacy line receives a distinct migration document.
for t in flows:
    doc_id='MIG-'+t['id']
    con.execute('INSERT INTO documents(id,type,external_no,operator,note,created_at) VALUES(?,?,?,?,?,?)',(doc_id,t['type'] or '迁移记录',t['doc_no'],t['operator'],t['note'],t['time']))
    con.execute('INSERT INTO transactions(id,doc_id,time,type,doc_no,product_id,product_name,qty_in,qty_out,qty_adjust,operator,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(t['id'],doc_id,t['time'],t['type'] or '迁移记录',t['doc_no'],t['product_id'],t['product_name'],t['qty_in'],t['qty_out'],t['qty_adjust'],t['operator'],t['note']))
# Settings B4:C10
for row in wb['基础设置'].iter_rows(min_row=4,max_row=10,min_col=2,max_col=3,values_only=True):
    if row[0] is not None:
        k=str(row[0]); v=row[1]
        if isinstance(v,(datetime,date,time)): v=v.isoformat()
        con.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(k,json.dumps(v,ensure_ascii=False)))
con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('revision','1')")
con.execute('INSERT INTO audit_logs(time,action,detail) VALUES(?,?,?)',(now,'首次迁移',json.dumps(report,ensure_ascii=False)))
con.commit(); con.close(); wb.close()
(ROOT/'migration_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
