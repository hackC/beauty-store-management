from datetime import date, timedelta
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from store_inventory.scheduling.availability import rest_reason, shop_today
from store_inventory.scheduling.rules import _effective_staff_shifts
from test_v160_dev9_catalog_inventory_permissions import _close_project, _create_user, _load_project


def test_browser_candidates_follow_window_and_store_clock():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    asset = Path(__file__).resolve().parents[1] / 'static/schedule_availability.js'
    script = """
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = require('node:fs').readFileSync(process.argv[1], 'utf8');
vm.runInThisContext(code);
const {candidateStarts, shopClock, restReason} = ScheduleAvailability;
assert.deepEqual(candidateStarts([{start:790,end:990}],90,540,1380), [790,880]);
assert.deepEqual(candidateStarts([{start:790,end:990}],90,540,1380,792), [795,885]);
assert.deepEqual(candidateStarts([{start:790,end:879}],90,540,1380), []);
assert.deepEqual(candidateStarts([{start:620,end:740},{start:790,end:970}],90,540,1380),[620,790,880]);
assert.equal(shopClock(new Date('2026-11-23T16:02:00Z')).day,'2026-11-24');
const roster = {weeklyRest:{a:[{from:'2026-11-24',weekdays:[2]}]},extraAttendance:{a:{'2026-12-01':[{start:'13:10',end:'16:30'}]}}};
assert.equal(restReason(roster,'a','2026-11-24'),'固定休息');
assert.equal(restReason(roster,'a','2026-12-01'),'');
assert.equal(restReason(roster,'a','2026-12-08'),'固定休息');
"""
    subprocess.run([node, '-e', script, str(asset)], check=True, timeout=10)


def test_recurring_rest_blocks_conflicts_and_keeps_history(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url='https://testserver')
        assert admin.post('/api/auth/bootstrap', json={
            'username': 'manager', 'display_name': '店长', 'password': 'ManagerPass1234',
        }).is_success
        staff_user, _, _ = _create_user(admin, main, 'booking', {
            'ext.appointments.create': True, 'ext.appointments.read': True,
        })
        day = date.fromisoformat(shop_today()) + timedelta(days=1)
        day_string = day.isoformat()
        next_week = (day + timedelta(days=7)).isoformat()
        past = (day - timedelta(days=7)).isoformat()
        shifts = [{'start': '09:00','end': '21:00'}]
        record = {'id': 'a1', 'staffId': 's1', 'serviceId': 'p1', 'date': day_string,
                  'start': '13:10', 'duration': 70, 'buffer': 20, 'status': 'pending'}
        base = {'staff': [{'id':'s1','name':'美容师','shifts':shifts}], 'services': [],
                'appointments': [record]}
        assert admin.post('/api/schedule/migrate',json={'payload':base}).is_success
        weekly = {**base, 'weeklyRest': {'s1': [{'from':day_string,'weekdays':[day.isoweekday()]}]}}
        def put(client, payload, revision):
            return client.put('/api/schedule/state',json={'payload':payload,'revision':revision})
        assert put(staff_user,weekly,1).status_code == 403
        blocked = put(admin,weekly,1)
        assert blocked.status_code == 422 and day_string in blocked.text
        exception = [{'start':'13:10','end':'16:30'}]
        allowed = {**weekly, 'extraAttendance': {'s1': {day_string: exception}}}
        assert put(admin,allowed,1).is_success
        saved = admin.get('/api/schedule/state').json()['payload']
        assert _effective_staff_shifts(saved,'s1',day_string) == exception
        assert _effective_staff_shifts(saved,'s1',past) == shifts
        assert _effective_staff_shifts(saved,'s1',next_week) == []
        assert rest_reason(saved,'s1',next_week) == '固定休息'
        invalid = {**allowed,'appointments':[record,{**record,'id':'a2','date':next_week}]}
        assert put(admin,invalid,2).status_code == 422
        outside = {**allowed,'appointments':[{**record,'start':'15:55'}]}
        assert put(admin,outside,2).status_code == 422
        assert put(admin,{**allowed,'extraAttendance':{}},2).status_code == 422
    finally:
        _close_project(project,old_cwd)
