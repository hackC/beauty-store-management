from datetime import date, timedelta
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from store_inventory.scheduling.availability import shop_today
from test_v160_dev9_catalog_inventory_permissions import _close_project, _create_user, _load_project


def test_member_and_overtime_rules_follow_appointment_through_api(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url='https://testserver')
        assert admin.post('/api/auth/bootstrap', json={
            'username': 'manager', 'display_name': '店长', 'password': 'ManagerPass1234',
        }).is_success
        reader, _, _ = _create_user(admin, main, 'observer', {'ext.appointments.read': True})
        day = (date.fromisoformat(shop_today()) + timedelta(days=1)).isoformat()
        shifts = [{'start': '09:00', 'end': '12:00'}, {'start': '14:00', 'end': '18:00'}]
        base = {'businessHours': {'start': '09:00', 'end': '18:00'},
                'staff': [{'id': 'a', 'shifts': shifts}, {'id': 'b', 'shifts': shifts}],
                'appointments': [], 'services': []}
        assert admin.post('/api/schedule/migrate', json={'payload':base}).is_success
        booking = {'id':'one', 'staffId':'a', 'date':day, 'start':'12:10',
                   'duration':70, 'buffer':20, 'status':'pending'}

        def put(appointments, revision=1, client=admin, **changes):
            return client.put('/api/schedule/state', json={
                'payload':{**base, 'appointments':appointments, **changes}, 'revision':revision,
            })

        assert put([booking]).status_code == 422
        flagged = {**booking, 'isMember':True, 'temporaryOvertime':True}
        assert put([{**flagged, 'isMember':'true'}]).status_code == 400
        assert put([flagged], client=reader).status_code == 403
        assert put([flagged], daysOff={day:['a']}).status_code == 422
        assert put([flagged], weeklyRest={'a':[{'from':day,'weekdays':[date.fromisoformat(day).isoweekday()]}]}).status_code == 422
        assert put([{**flagged,'start':'16:00'}]).status_code == 422
        assert put([{**flagged,'duration':360}]).status_code == 422
        assert put([flagged]).is_success
        stored = admin.get('/api/schedule/state').json()['payload']
        assert stored['appointments'] == [flagged]
        assert stored['staff'] == base['staff']
        assert put([{**flagged,'temporaryOvertime':False}], revision=2).status_code == 422

        # The permission applies to this booking only, and cannot skip collisions.
        overlapping = {**flagged,'id':'two','start':'13:00'}
        assert put([flagged,{**overlapping,'temporaryOvertime':False}], revision=2).status_code == 422
        assert put([flagged,overlapping], revision=2).status_code == 409
        ordinary = {**booking,'id':'after','start':'14:00'}
        assert put([flagged,ordinary], revision=2).is_success

        # Editing only membership preserves the appointment's time and overtime flag.
        regular = {**flagged, 'isMember':False}
        assert put([regular,ordinary], revision=3).is_success
        assert admin.get('/api/schedule/state').json()['payload']['appointments'][0] == regular
    finally:
        _close_project(project,old_cwd)


def test_browser_rest_overtime_uses_the_same_gap_boundaries():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    asset = Path(__file__).resolve().parents[1] / 'static/schedule_availability.js'
    script = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
vm.runInThisContext(require('node:fs').readFileSync(process.argv[1],'utf8'));
const shifts = [{start:540,end:720},{start:840,end:1080}];
const check = (start,end) => ScheduleAvailability.fitsRestOvertime(shifts,540,1080,start,end);
assert.equal(check(730,820),true);
assert.equal(check(690,760),true);
assert.equal(check(690,720),false);
assert.equal(check(830,920),true);
assert.equal(check(840,900),false);
assert.equal(check(730,1081),false);
assert.equal(check(539,730),false);
assert.equal(ScheduleAvailability.fitsRestOvertime([],540,1080,730,820),false);
assert.equal(ScheduleAvailability.fitsRestOvertime([{start:540,end:720},{start:720,end:900}],540,1080,730,820),false);
"""
    subprocess.run([node, '-e', script, str(asset)], check=True, timeout=10)
