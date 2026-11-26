from datetime import date, timedelta
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from store_inventory.scheduling.availability import rest_reason, shop_today
from test_v160_dev9_catalog_inventory_permissions import _close_project, _load_project


def test_dated_override_closing_cutoff_and_rest_block(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        client = TestClient(main.app, base_url='https://testserver')
        assert client.post('/api/auth/bootstrap', json={
            'username': 'manager', 'display_name': '店长', 'password': 'ManagerPass1234',
        }).is_success
        today = date.fromisoformat(shop_today())
        day, tomorrow = today.isoformat(), (today + timedelta(days=1)).isoformat()
        shifts = [{'start':'09:00', 'end':'23:00'}]
        base = {'staff':[{'id':'s1','shifts':shifts}], 'services':[{'id':'pause','duration':0}],
                'appointments':[], 'restBookingLeadMinutes':60, 'businessHours':shifts[0]}
        assert client.post('/api/schedule/migrate', json={'payload':base}).is_success

        def put(payload, revision):
            return client.put('/api/schedule/state', json={'payload':payload,'revision':revision})

        override = {**base, 'permanentRestStaffIds':['s1'], 'daysOff':{day:['s1']},
                    'extraAttendance':{'s1':{day:shifts}}}
        assert put(override, 1).is_success
        assert not rest_reason(override,'s1',day)
        assert rest_reason(override,'s1',tomorrow) == '持续休息'
        late = {'id':'client','staffId':'s1','date':day,'start':'22:20',
                'duration':30,'buffer':0,'extend':0,'status':'pending'}
        assert put({**override,'appointments':[late]},2).status_code == 422
        assert put({**override,'appointments':[{**late,'date':tomorrow,'start':'12:00'}]},2).status_code == 422

        # Use the regular roster for the rest block and terminal-boundary checks.
        pause = {'id':'r1','staffId':'s1','date':day,'start':'16:00',
                 'serviceId':'pause','duration':0,'buffer':30,'extend':0,
                 'status':'pending','restBlock':True}
        plan = {**base,'appointments':[pause]}
        assert put(plan,2).is_success
        for start in ('15:05','16:00','22:20'):
            assert put({**plan,'appointments':[pause,{**late,'start':start}]},3).status_code in (409,422)
        assert put({**plan,'appointments':[pause,{**late,'start':'15:00','serviceId':'pause'}]},3).status_code == 400
        assert put({**plan,'appointments':[pause,{**late,'start':'22:00'}]},3).is_success
        state = client.get('/api/schedule/state').json()
        assert state['payload']['appointments'][0] == pause
    finally:
        _close_project(project,old_cwd)


def test_browser_windows_apply_same_terminal_and_rest_block_cutoffs():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    asset = Path(__file__).resolve().parents[1] / 'static/schedule_availability.js'
    script = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const {bookingWindows,closingStarts,restReason}=ScheduleAvailability;
const roster={permanentRestStaffIds:['s1'],daysOff:{'2026-11-23':['s1']},extraAttendance:{
  s1:{'2026-11-23':[{start:'09:00',end:'23:00'}]}
}};
assert.equal(restReason(roster,'s1','2026-11-23'),'');
assert.equal(restReason(roster,'s1','2026-11-24'),'持续休息');
const windows=bookingWindows([{start:540,end:1380}],540,1380,60,60,true,[{start:960,end:990}]);
assert.deepEqual(windows,[{start:540,end:960,latestStart:900},{start:990,end:1440,latestStart:1320}]);
assert.deepEqual(closingStarts(windows,[],90,540,1380,1340),[]);
assert.deepEqual(closingStarts(windows,[],90,540,1380,1320),[1320]);
"""
    subprocess.run([node, '-e', script, str(asset)], check=True, timeout=12)
