from datetime import date, timedelta
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from store_inventory.scheduling.availability import shop_today
from test_v160_dev9_catalog_inventory_permissions import _close_project, _create_user, _load_project


def test_overview_includes_boundary_and_resumes_at_rest_end():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    assets = Path(__file__).resolve().parents[1] / 'static'
    script = """
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
for (const path of process.argv.slice(1)) vm.runInThisContext(fs.readFileSync(path,'utf8'));
const {bookingWindows, candidateStarts} = ScheduleAvailability;
const shifts = [{start:540,end:600},{start:600,end:720},{start:780,end:1020}];
const busy = [{start:540,end:670}];
const starts = (lead, now, allowRestOverrun = true) => {
  const windows = bookingWindows(shifts,540,1020,lead,0,allowRestOverrun);
  return candidateStarts(ScheduleHandoff.freeWindows(windows,busy),90,540,1020,now);
};
assert.deepEqual(starts(50,null),[670,780,870]);
assert.deepEqual(starts(51,null),[780,870]);
assert.deepEqual(starts(50,671),[780,870]);
assert.deepEqual(starts(50,null,false),[780,870]);
const shortBreak = bookingWindows([{start:540,end:720},{start:740,end:1020}],540,1020,50);
assert.equal(shortBreak[0].end,740);
assert.deepEqual(candidateStarts(shortBreak,90,540,1020,670),[740,830,920]);
const occupiedBreak = ScheduleHandoff.freeWindows(bookingWindows(shifts,540,1020,50),
  [...busy,{start:750,end:775}]);
assert.deepEqual(candidateStarts(occupiedBreak,90,540,1020),[780,870]);
"""
    subprocess.run([node, '-e', script, str(assets / 'schedule_availability.js'),
                    str(assets / 'schedule_handoff.js')], check=True, timeout=10)


def test_rest_lead_boundary_setting_permissions_and_existing_bookings(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url='https://testserver')
        assert admin.post('/api/auth/bootstrap', json={
            'username': 'manager', 'display_name': '店长', 'password': 'ManagerPass1234',
        }).is_success
        employee, _, _ = _create_user(admin, main, 'booking', {
            'ext.appointments.read': True, 'ext.appointments.create': True,
        })
        day = (date.fromisoformat(shop_today()) + timedelta(days=1)).isoformat()
        person = {'id': 's1', 'shifts': [{'start': '09:00', 'end': '12:00'},
                                         {'start': '13:00', 'end': '17:00'}]}
        base = {'staff': [person], 'services': [], 'appointments': []}
        assert admin.post('/api/schedule/migrate', json={'payload': base}).is_success

        def put(client, revision, payload):
            return client.put('/api/schedule/state', json={'payload': payload, 'revision': revision})

        assert put(admin, 1, {**base, 'restBookingLeadMinutes': True}).status_code == 400
        assert put(employee, 1, {**base, 'restBookingLeadMinutes': 20}).status_code == 403
        booking = {'id': 'a', 'staffId': 's1', 'date': day, 'start': '11:10',
                   'duration': 70, 'buffer': 20, 'status': 'pending'}
        for start in ('11:11', '12:59'):
            result = put(admin, 1, {**base, 'appointments': [{**booking, 'start': start}]})
            assert result.status_code == 422 and '预约门槛' in result.text
        booked = {**base, 'appointments': [booking]}
        assert put(employee, 1, booked).is_success

        # Raising the lead keeps existing bookings and older clients may omit it.
        assert put(admin, 2, {**booked, 'restBookingLeadMinutes': 60}).is_success
        afternoon = {**booking, 'id': 'b', 'start': '13:00'}
        assert put(employee, 3, {**base, 'appointments': [booking, afternoon]}).is_success
        stored = admin.get('/api/schedule/state').json()['payload']
        assert stored['restBookingLeadMinutes'] == 60
        assert [a['start'] for a in stored['appointments']] == ['11:10', '13:00']
        assert put(admin, 4, {**stored, 'appointments': [{**booking, 'start': '11:05'}, afternoon]}).status_code == 422
    finally:
        _close_project(project, old_cwd)
