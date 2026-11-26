"""DEV10 direct HTTP regression tests, all database writes on temporary files."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import runpy

from fastapi.testclient import TestClient
from store_inventory.scheduling.policy import authorize_state, SchedulingError


def test_dev10_composite_and_sensitive_fields():
    support = runpy.run_path(str(Path(__file__).with_name('test_v160_dev9_catalog_inventory_permissions.py')))
    with TemporaryDirectory() as td:
        project, old_cwd, main = support['_load_project'](Path(td))
        try:
            admin = TestClient(main.app, base_url='https://testserver')
            assert admin.post('/api/auth/bootstrap',json={'username':'manager','display_name':'店长','password':'ManagerPass1234'}).status_code == 200
            today = date.today().isoformat()
            future = (date.today()+timedelta(days=1)).isoformat()
            payload = {'staff':[{'id':'s1','name':'一'},{'id':'s2','name':'二'}], 'services':[], 'appointments':[
                {'id':'a1','staffId':'s1','date':today,'start':'09:00','duration':30,'status':'pending','phoneTail':'123456','note':'隐私'},
                {'id':'a2','staffId':'s2','date':today,'start':'10:00','duration':30,'status':'pending'}], 'bookingGroups':[], 'daysOff':{}, 'daySchedules':{}}
            assert admin.post('/api/schedule/migrate',json={'payload':payload}).status_code == 200
            creator, uid, group = support['_create_user'](admin,main,'scoped',{'ext.scheduling.read':True,'ext.appointments.read':True,'ext.appointments.create_self_today':True,'ext.appointments.status_change_self_today':True})
            assert admin.put('/api/schedule/staff-links',json={'user_id':uid,'staff_id':'s1'}).status_code == 200
            response=creator.get('/api/schedule/state')
            assert 'no-store' in response.headers['cache-control']
            r=response.json()
            assert r['staff_id']=='s1' and r['payload']['appointments'][0]['phoneTail']=='' and r['payload']['appointments'][0]['note']==''
            incoming=deepcopy(r['payload']); incoming['appointments'].append({'id':'a3','staffId':'s1','date':today,'start':'11:00','duration':30,'status':'pending'})
            for field, value in [('daysOff',{today:['s1']}),('businessHours',{'start':'08:00','end':'20:00'})]:
                forged=deepcopy(incoming); forged[field]=value
                assert creator.put('/api/schedule/state',json={'payload':forged,'revision':1}).status_code==403
                assert admin.get('/api/schedule/state').json()['revision']==1
            for sid, day in [('s2',today),('s1',future)]:
                forged=deepcopy(incoming); forged['appointments'][-1].update(staffId=sid,date=day)
                assert creator.put('/api/schedule/state',json={'payload':forged,'revision':1}).status_code==403
            assert creator.put('/api/schedule/state',json={'payload':incoming,'revision':1}).status_code==200
            saved=admin.get('/api/schedule/state').json()
            assert saved['payload']['appointments'][0]['phoneTail']=='123456' and saved['payload']['appointments'][0]['note']=='隐私'
            assert creator.patch('/api/schedule/appointments/a2/status',json={'status':'done','expected_revision':2}).status_code==403
            assert creator.patch('/api/schedule/appointments/a1/status',json={'status':'done','expected_revision':2}).status_code==200
            assert creator.patch('/api/schedule/appointments/a1/status',json={'status':'pending','expected_revision':2}).status_code==409
            assert creator.post('/api/schedule/appointments/transfer',json={'appointment_id':'a1','target_staff_id':'s2','segment_start':'09:00','segment_end':'09:15','expected_revision':3}).status_code==403
            assert admin.delete(f'/api/permission-groups/{group}/members/{uid}').status_code==200
            assert creator.patch('/api/schedule/appointments/a1/status',json={'status':'pending','expected_revision':3}).status_code==403

            assert admin.post('/api/customers',json={'id':'private','name':'档案','phone_tail':'998877','notes':'仅授权可见'}).status_code==200
            reader, _, _=support['_create_user'](admin,main,'reader',{'ext.customers.read':True,'ext.customers.update':True})
            row=reader.get('/api/customers?q=档案').json()[0]
            assert row['phone_tail']=='' and row['notes']==''
            assert reader.get('/api/customers?q=998877').json()==[]
            assert reader.post('/api/customers',json={'id':'private','name':'新名称'}).status_code==200
            assert reader.post('/api/customers',json={'id':'private','name':'新名称','notes':'越权'}).status_code==403
            assert reader.post('/api/customers',json={'name':'越权新增'}).status_code==403
            assert reader.delete('/api/customers/private').status_code==403
            row=admin.get('/api/customers?q=新名称').json()[0]
            assert row['phone_tail']=='998877' and row['notes']=='仅授权可见'

            wait=admin.post('/api/schedule/waitlist',json={'customer_name':'候补','phone_tail':'654321','notes':'私密'})
            wid=wait.json()['id']
            limited,_,_=support['_create_user'](admin,main,'waitreader',{'ext.waitlist.read':True})
            item=next(x for x in limited.get('/api/schedule/waitlist').json() if x['id']==wid)
            assert item['phone_tail']=='' and item['notes']==''
            assert limited.patch(f'/api/schedule/waitlist/{wid}?status=booked').status_code==403
        finally:
            support['_close_project'](project,old_cwd)


def test_dev10_group_diff_requires_scope_for_every_member():
    day=date.today().isoformat()
    old={'appointments':[{'id':'a','staffId':'s1','date':day},{'id':'b','staffId':'s2','date':day}], 'bookingGroups':[{'id':'g','appointmentIds':['a','b'],'color':'old'}]}
    new=deepcopy(old);new['bookingGroups'][0]['color']='new'
    user={'role':'staff','permissions':{'ext.appointments.update_self_today':True}}
    try:
        authorize_state(old,new,user,'s1',day)
    except SchedulingError as exc:
        assert exc.status_code==403
    else:
        raise AssertionError('mixed owner group modification was allowed')


if __name__=='__main__':
    test_dev10_group_diff_requires_scope_for_every_member()
    print('test_dev10_group_diff_requires_scope_for_every_member PASS')
    test_dev10_composite_and_sensitive_fields()
    print('test_dev10_composite_and_sensitive_fields PASS')
