from contextlib import closing

from fastapi.testclient import TestClient

from test_v160_dev9_catalog_inventory_permissions import _close_project, _load_project


def test_blocked_cookie_does_not_consume_first_login_code(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        with closing(main.db()) as con:
            assert con.execute('SELECT count(*) FROM users').fetchone()[0] == 0
            assert con.execute('SELECT count(*) FROM products').fetchone()[0] == 0
        admin = TestClient(main.app, base_url='https://testserver')
        assert admin.post('/api/auth/bootstrap', json={
            'username':'manager','display_name':'店长','password':'ManagerPass1234',
        }).is_success
        staff = admin.post('/api/users',json={
            'username':'assistant1','display_name':'员工','activation_code':'01234567',
        })
        assert staff.is_success, staff.text
        for url, username, password, setup in [
            ('https://testserver','assistant1','01234567',True),
            ('http://192.168.1.8','manager','ManagerPass1234',False),
        ]:
            browser = TestClient(main.app, base_url=url)
            probe = browser.post('/api/auth/cookie-probe')
            assert probe.is_success and probe.headers['cache-control'] == 'no-store'
            assert ('Secure' in probe.headers['set-cookie']) == url.startswith('https:')
            payload = {'username':username,'password':password,'cookie_probe':probe.json()['probe']}
            if setup:
                blocked = TestClient(main.app, base_url=url)
                failed = blocked.post('/api/auth/login',json=payload)
                assert failed.status_code == 400 and '未验证' in failed.json()['detail']
            login = browser.post('/api/auth/login',json=payload)
            assert login.is_success, login.text
            assert login.json()['must_set_password'] == setup
            state = browser.get('/api/auth/status')
            assert state.json()['session_cookie_received']
            assert state.json()['authenticated'] and state.json()['user']['username'] == username
            assert 'set-cookie' not in state.headers
            if setup:
                assert browser.post('/api/auth/set-first-password',json={'password':'EmployeePass123'}).is_success
                assert not browser.get('/api/auth/status').json()['user']['must_set_password']
    finally:
        _close_project(project, old_cwd)
