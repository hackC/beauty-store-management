# -*- coding: utf-8 -*-
"""静态前端检查；有 Node.js 时额外执行 JavaScript 语法检查。"""
import pathlib, shutil, subprocess, tempfile


def _scripts(html):
    parts=[]; pos=0
    while True:
        a=html.find('<script',pos)
        if a<0: break
        a=html.find('>',a)+1
        b=html.find('</script>',a)
        if b<0: break
        parts.append(html[a:b]);pos=b+9
    return parts


def test_inventory_barcode_and_schedule_navigation():
    root=pathlib.Path(__file__).resolve().parents[1]
    html=(root/'static'/'index.html').read_text(encoding='utf-8')
    for marker in ('V1.5.0 mobile-first refinement','grid-template-columns: repeat(2, minmax(0, 1fr))','safe-area-inset-bottom','min-height: 46px','op-table-head','overflow: visible','max-height: 42dvh'):
        assert marker in html, marker
    for marker in ('id="pBarcode"','startBarcodeScan(\'pBarcode\')','startBarcodeScan(\'prodSearch\')','startBarcodeScan(\'${searchId}\')','/schedule','product.barcode','schedule_approve','notifications_view','audit_view','devices_view','customers_manage','waitlist_manage','openNotifications','/api/devices/heartbeat','page-controlcenter','运营监控中心','loadControlTab','/api/customers','/api/audit','手机扫码','startMobileScan','经营报表','reports_view'):
        assert marker in html, marker
    scripts=_scripts(html)
    assert scripts
    node=shutil.which('node')
    if node:
        with tempfile.NamedTemporaryFile('w',suffix='.js',encoding='utf-8',delete=False) as f:
            f.write('\n'.join(scripts)); path=f.name
        try: subprocess.run([node,'--check',path],check=True,capture_output=True,text=True)
        finally: pathlib.Path(path).unlink(missing_ok=True)


def test_schedule_uses_server_revision_and_segment_transfer():
    root=pathlib.Path(__file__).resolve().parents[1]
    html=(root/'static'/'index_V2_5_5.html').read_text(encoding='utf-8')
    for marker in ('viewport-fit=cover','V1.5.0 mobile-first refinement for a 5–10 person shop','safe-area-inset-top','/static/schedule_display.css','grid-template-columns: minmax(0, 1fr)'):
        assert marker in html, marker
    for marker in ('/api/schedule/state','/api/schedule/migrate','scheduleRevision','expected_revision','transferPanel','/api/schedule/appointments/transfer','initializeScheduleServer','class="store-shell app-frame"','data-store-page="schedule"','/api/system/identity','/api/devices/heartbeat','scheduleNotificationsBtn','协作管理','scheduleCollabModal','/api/schedule/requests','/api/schedule/waitlist','/api/schedule/workload','提交申请','加入候补名单','最多 11 位数字','scheduleCollabBtn'):
        assert marker in html, marker
    scripts=_scripts(html)
    assert scripts
    node=shutil.which('node')
    if node:
        with tempfile.NamedTemporaryFile('w',suffix='.js',encoding='utf-8',delete=False) as f:
            f.write('\n'.join(scripts)); path=f.name
        try: subprocess.run([node,'--check',path],check=True,capture_output=True,text=True)
        finally: pathlib.Path(path).unlink(missing_ok=True)
