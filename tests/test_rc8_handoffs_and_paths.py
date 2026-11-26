import json
from pathlib import Path
from zipfile import ZipFile
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from test_v160_dev9_catalog_inventory_permissions import _close_project, _create_user, _load_project

ROOT = Path(__file__).resolve().parents[1]


def test_handoff_candidates_require_two_continuous_free_slots_and_count_wait_once():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the scheduling client')
    subprocess.run([node, str(ROOT / 'tests/rc8_handoff_case.cjs'),
                    str(ROOT / 'static/schedule_handoff.js'), str(ROOT / 'static/index_V2_5_5.html')], check=True, timeout=10)


def test_handoff_is_saved_with_history_and_stale_or_unauthorized_plan_is_rejected(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        assert admin.post('/api/auth/bootstrap', json={
            'username': 'manager', 'display_name': '店长', 'password': 'ManagerPass1234',
        }).is_success
        creator, _, _ = _create_user(admin, main, 'booking', {
            'ext.appointments.create': True, 'ext.appointments.read': True,
        })
        day = '2026-11-17'
        base = {'staff': [
            {'id': 's1', 'name': 'A', 'shifts': [{'start': '09:00', 'end': '09:50'}]},
            {'id': 's2', 'name': 'B', 'shifts': [{'start': '09:30', 'end': '11:00'}]},
        ], 'services': [{'id': 'p1', 'name': '护理'}], 'appointments': [], 'bookingGroups': [],
            'businessHours': {'start': '09:00', 'end': '21:00'}}
        assert admin.post('/api/schedule/migrate', json={'payload': base}).is_success
        first = {'id': 'a1', 'staffId': 's1', 'serviceId': 'p1', 'groupId': 'g1',
                 'date': day, 'start': '09:00', 'duration': 30, 'buffer': 0, 'extend': 0,
                 'status': 'pending', 'customer': '预约测试', 'segmentOf': 'a1'}
        second = {**first, 'id': 'a2', 'staffId': 's2', 'start': '09:30', 'buffer': 10}
        group = {'id': 'g1', 'date': day, 'appointmentIds': ['a1','a2'], 'automaticSplit': True}
        payload = {**base, 'appointments': [first, second], 'bookingGroups': [group]}
        command = {'revision': 1, 'payload': payload}
        assert creator.put('/api/schedule/state', json=command).status_code == 403
        bad_payload = {**payload, 'appointments': [first, {**second, 'start': '09:35'}]}
        assert admin.put('/api/schedule/state', json={**command, 'payload': bad_payload}).status_code == 400
        result = admin.put('/api/schedule/state', json=command)
        assert result.status_code == 200, result.text
        history = admin.get('/api/schedule/transfers').json()
        assert len(history) == 1 and history[0]['segment_end'] == '10:10'
        assert admin.put('/api/schedule/state', json=command).status_code == 409
    finally:
        _close_project(project, old_cwd)


def test_settings_preserve_paths_on_partial_save_and_restart(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        assert admin.post('/api/auth/bootstrap', json={
            'username': 'manager', 'display_name': '店长', 'password': 'ManagerPass1234',
        }).is_success
        paths = {'backup_dir': 'custom/backups', 'purchase_dir': 'custom/purchase'}
        response = admin.put('/api/settings', json={**paths, 'https': False})
        assert response.is_success, response.text
        response = admin.put('/api/settings', json={'backup_time': '19:00'})
        assert response.is_success and not response.json()['restart_required']
        main.CONFIG = {**main.CONFIG, 'backup_dir': 'data/backups', 'purchase_dir': 'data/purchase'}
        main.update_runtime_config({'auto_start': True})
        for key, value in paths.items():
            assert main.load_config()[key] == value
            assert main.configured_dir(key, main.DATA_DIR) == project / value
        main.CONFIG_PATH.write_text('{broken', encoding='utf-8')
        assert all(main.load_config()[key] == value for key, value in paths.items())
        assert main.CONFIG_PATH.read_text() == '{broken'
        result = main.create_backup()
        bundle = main.configured_dir('backup_dir', main.BACKUP_DIR) / result['bundle_archive']
        with ZipFile(bundle) as archive:
            saved = json.loads(archive.read('data/config.json'))
            assert all(saved[key] == value for key, value in paths.items())
    finally:
        _close_project(project, old_cwd)
