"""SQLite adapter. Every command executes in one protected transaction."""
from datetime import datetime, date
import json
import hashlib
import sqlite3
import uuid
from store_inventory.scheduling.policy import SchedulingError, granted, require, scoped, authorize_state, redact_state
from store_inventory.scheduling.permissions import STATE_WRITE_KEYS
from store_inventory.scheduling.availability import FIELDS, affected_appointments, blocked_by_rest, extra_shifts, rest_reason, shop_today, work_periods
from store_inventory.scheduling.rules import _validate_time, _validate_schedule_payload, _schedule_conflict_keys, _effective_staff_shifts, validate_new_handoffs, validate_rest_projects
from store_inventory.scheduling.repository import SQLiteRepository, reading, writing

def _schedule_row(con):
    return con.execute('SELECT payload,revision,updated_at,updated_by FROM schedule_state WHERE id=1').fetchone()

def _schedule_decode(row):
    if not row:
        return None
    return {'payload': json.loads(row['payload']), 'revision': int(row['revision']), 'updated_at': row['updated_at'], 'updated_by': row['updated_by']}

def _schedule_user_staff(con, user_id):
    row = con.execute('SELECT staff_id FROM schedule_staff_users WHERE user_id=?', (user_id,)).fetchone()
    return row['staff_id'] if row else None

class SQLiteSchedulingRepository(SQLiteRepository):
    def __init__(self, connect, audit_writer, sync_customers):
        super().__init__(connect, audit_writer)
        self.sync_customers = sync_customers

    @reading
    def get_schedule_state(self, con, user, actor):
        if not any(granted(user, key) for key in (
            'ext.scheduling.read', 'ext.appointments.read', 'ext.appointments.read_self',
        )):
            raise SchedulingError(403, '没有排班查看权限')
        result = _schedule_decode(_schedule_row(con))
        if result is None:
            return {'initialized': False, 'payload': None, 'revision': 0}
        result['staff_id'] = _schedule_user_staff(con, user['id'])
        result['payload'] = redact_state(result['payload'], user, result['staff_id'])
        return {'initialized': True, **result}

    @writing
    def migrate_schedule(self, con, user, actor, data):
        require(user, 'ext.scheduling.initialize')
        _validate_schedule_payload(data['payload'])
        now = datetime.now().isoformat(timespec='seconds')
        encoded = json.dumps(data['payload'], ensure_ascii=False, separators=(',', ':'))
        row = _schedule_row(con)
        if row:
            result = _schedule_decode(row)
            result['payload'] = redact_state(result['payload'], user)
            return {'ok': True, 'created': False, **result}
        authorize_state({'staff': [], 'services': [], 'appointments': []}, data['payload'], user, _schedule_user_staff(con, user['id']), date.today().isoformat())
        con.execute('INSERT INTO schedule_state(id,payload,revision,updated_at,updated_by) VALUES(1,?,1,?,?)', (encoded, now, user['id']))
        self.audit(con, '初始化排班数据', '首次排班迁移', actor, 'schedule_state', '1', None, {'revision': 1})
        self.sync_customers(con, data['payload'], now)
        return {'ok': True, 'created': True, 'payload': redact_state(data['payload'], user), 'revision': 1, 'updated_at': now, 'updated_by': user['id']}

    @writing
    def put_schedule_state(self, con, user, actor, data):
        if not any(granted(user, key) for key in STATE_WRITE_KEYS):
            raise SchedulingError(403, '没有排班修改权限')
        _validate_schedule_payload(data['payload'])
        now = datetime.now().isoformat(timespec='seconds')
        row = _schedule_row(con)
        if not row:
            raise SchedulingError(409, '服务端尚无排班基线，请先执行首次数据迁移')
        current_revision = int(row['revision'])
        if data['revision'] != current_revision:
            raise SchedulingError(409, f'排班数据版本冲突：当前版本 {current_revision}，请刷新后重试')
        old_payload = json.loads(row['payload'])
        incoming = dict(data['payload'])
        if not granted(user, 'ext.appointments.read') and not granted(user, 'ext.scheduling.rules_update'):
            staff_id = _schedule_user_staff(con, user['id'])
            for field in FIELDS:
                stored = old_payload.get(field, {})
                visible = {staff_id: stored[staff_id]} if staff_id in stored and granted(user, 'ext.appointments.read_self') else {}
                if field in incoming and incoming[field] != visible:
                    raise SchedulingError(403, '没有修改固定休息或临时出勤的权限')
                if field in old_payload:
                    incoming[field] = stored
                else:
                    incoming.pop(field, None)
        if not granted(user, 'ext.appointments.read'):
            if incoming.get('appointments') or incoming.get('bookingGroups'):
                raise SchedulingError(403, '未授权查看预约，不可通过全量接口修改预约')
            for key in ('appointments', 'bookingGroups'):
                incoming[key] = old_payload.get(key, [])
        for field in FIELDS:
            if field in old_payload and field not in incoming:
                incoming[field] = old_payload[field]
        if 'restBookingLeadMinutes' in old_payload and 'restBookingLeadMinutes' not in incoming:
            incoming['restBookingLeadMinutes'] = old_payload['restBookingLeadMinutes']
        new_payload = authorize_state(old_payload, incoming, user, _schedule_user_staff(con, user['id']), date.today().isoformat())
        validate_rest_projects(old_payload, new_payload)
        impacted = affected_appointments(old_payload, new_payload, shop_today())
        if impacted:
            dates = '、'.join(sorted({str(a.get('date') or '日期未填') for a in impacted})[:5])
            raise SchedulingError(422, f'休息前预约门槛、临时加班或休息安排与 {len(impacted)} 条预约冲突（{dates}），请调整预约或班次后再保存')
        handoffs = validate_new_handoffs(old_payload, new_payload, user)
        new_conflicts = _schedule_conflict_keys(new_payload) - _schedule_conflict_keys(old_payload)
        if new_conflicts:
            first = next(iter(new_conflicts))
            raise SchedulingError(409, f'预约时间冲突：美容师 {first[1]} 在 {first[0]} 的预约时间重叠，请调整后再保存')
        encoded = json.dumps(new_payload, ensure_ascii=False, separators=(',', ':'))
        next_revision = current_revision + 1
        con.execute('UPDATE schedule_state SET payload=?,revision=?,updated_at=?,updated_by=? WHERE id=1', (encoded, next_revision, now, user['id']))
        for group, first, second in handoffs:
            hh, mm = map(int, second['start'].split(':'))
            end_at = hh * 60 + mm + second['duration'] + second.get('buffer', 0) + second.get('extend', 0)
            con.execute('''INSERT INTO schedule_transfer_history
                (id,appointment_id,group_id,appointment_date,source_staff_id,target_staff_id,
                 segment_start,segment_end,action_by,created_at,detail_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (uuid.uuid4().hex, first['id'], group['id'], first['date'], first['staffId'],
                 second['staffId'], second['start'], f'{end_at//60:02}:{end_at%60:02}',
                 user['id'], now, json.dumps({'automatic': True, 'created_segment_id': second['id']})))
        self.audit(con, '更新每日排班', f'revision {current_revision} -> {next_revision}', actor, 'schedule_state', '1', {'revision': current_revision}, {'revision': next_revision})
        self.sync_customers(con, new_payload, now)
        return {'ok': True, 'revision': next_revision, 'updated_at': now}

    @reading
    def list_schedule_staff_links(self, con, user, actor):
        if user.get('role') != 'admin':
            raise SchedulingError(403, '只有管理员可以查看员工绑定')
        rows = con.execute('\n        SELECT l.user_id,l.staff_id,u.username,u.display_name\n        FROM schedule_staff_users l JOIN users u ON u.id=l.user_id\n        ORDER BY u.username\n    ').fetchall()
        return [dict(x) for x in rows]

    @writing
    def change_appointment_status(self, con, user, actor, appointment_id, data):
        row = _schedule_row(con)
        if not row or int(row['revision']) != data['expected_revision']:
            raise SchedulingError(409, '预约版本已变更，请刷新后重试')
        payload = json.loads(row['payload'])
        item = next((x for x in payload['appointments'] if str(x['id']) == appointment_id), None)
        if item is None:
            raise SchedulingError(404, '预约不存在')
        scoped(user, 'ext.appointments.status_change', _schedule_user_staff(con, user['id']), date.today().isoformat(), item)
        if item.get('restBlock') is True and data['status'] not in ('pending','cancelled'):
            raise SchedulingError(400, '休息时间块没有服务状态')
        before = item.get('status')
        old_conflicts = _schedule_conflict_keys(payload)
        if before in ('cancelled', 'done') and data['status'] in ('pending', 'serving') and blocked_by_rest(payload, item):
            raise SchedulingError(422, '该预约与休息、休息前预约门槛或临时加班规则冲突')
        item['status'] = data['status']
        if _schedule_conflict_keys(payload) - old_conflicts:
            raise SchedulingError(409, '恢复预约将产生时间冲突')
        revision = int(row['revision']) + 1
        now = datetime.now().isoformat(timespec='seconds')
        con.execute('UPDATE schedule_state SET payload=?,revision=?,updated_at=?,updated_by=? WHERE id=1', (json.dumps(payload,ensure_ascii=False,separators=(',',':')),revision,now,user['id']))
        self.audit(con, '变更预约状态', appointment_id, actor, 'schedule_appointment', appointment_id, {'status':before}, {'status':data['status']})
        return {'ok':True,'revision':revision}

    @writing
    def put_schedule_staff_link(self, con, user, actor, data):
        if user.get('role') != 'admin':
            raise SchedulingError(403, '只有管理员可以绑定员工')
        user_exists = con.execute('SELECT 1 FROM users WHERE id=? AND enabled=1', (data['user_id'],)).fetchone()
        if not user_exists:
            raise SchedulingError(404, '有效员工账号不存在')
        row = _schedule_row(con)
        if not row:
            raise SchedulingError(409, '请先初始化排班数据')
        payload = json.loads(row['payload'])
        if not any((str(x.get('id')) == data['staff_id'] for x in payload.get('staff', []) if isinstance(x, dict))):
            raise SchedulingError(404, '美容师档案不存在')
        try:
            con.execute('INSERT INTO schedule_staff_users(user_id,staff_id,created_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET staff_id=excluded.staff_id,created_at=excluded.created_at', (data['user_id'], data['staff_id'], datetime.now().isoformat(timespec='seconds')))
        except sqlite3.IntegrityError:
            raise SchedulingError(409, '该美容师档案已绑定其他账号')
        return {'ok': True}

    @writing
    def delete_schedule_staff_link(self, con, user, actor, user_id):
        if user.get('role') != 'admin':
            raise SchedulingError(403, '只有管理员可以解除绑定')
        con.execute('DELETE FROM schedule_staff_users WHERE user_id=?', (user_id,))
        return {'ok': True}

    @reading
    def get_schedule_transfers(self, con, user, actor, date_from, date_to):
        require(user, 'ext.appointments.read')
        sql = 'SELECT * FROM schedule_transfer_history WHERE 1=1'
        args = []
        if date_from:
            sql += ' AND appointment_date>=?'
            args.append(date_from)
        if date_to:
            sql += ' AND appointment_date<=?'
            args.append(date_to)
        sql += ' ORDER BY created_at DESC LIMIT 500'
        rows = con.execute(sql, args).fetchall()
        result = [dict(x) for x in rows]
        if not granted(user, 'ext.appointments.contact_read'):
            for item in result:
                item['detail_json'] = ''
        return result

    @writing
    def transfer_schedule_segment(self, con, user, actor, data):
        """将一条预约中的指定时间段拆分并转给另一位美容师，原预约剩余时段保留。"""
        require(user, 'ext.appointments.transfer')
        try:
            row = _schedule_row(con)
            if not row:
                raise SchedulingError(409, '排班数据尚未初始化')
            revision = int(row['revision'])
            if revision != data['expected_revision']:
                raise SchedulingError(409, f'排班数据版本冲突：当前版本 {revision}')
            payload = json.loads(row['payload'])
            appointments = payload.get('appointments', [])
            appt_index = next((i for i, x in enumerate(appointments) if str(x.get('id')) == data['appointment_id']), None)
            if appt_index is None:
                raise SchedulingError(404, '预约不存在')
            original = appointments[appt_index]
            if original.get('restBlock') is True:
                raise SchedulingError(400, '休息时间块不能分单')
            if not _validate_time(original.get('start')):
                raise SchedulingError(400, '原预约开始时间格式不正确')
            if original.get('status') == 'cancelled':
                raise SchedulingError(400, '已取消的预约不能转单')
            if not _validate_time(data['segment_start']) or not _validate_time(data['segment_end']):
                raise SchedulingError(400, '转单时间格式必须为 HH:MM')
            start_min = int(data['segment_start'][:2]) * 60 + int(data['segment_start'][3:])
            end_min = int(data['segment_end'][:2]) * 60 + int(data['segment_end'][3:])
            original_start = int(str(original.get('start', '00:00'))[:2]) * 60 + int(str(original.get('start', '00:00'))[3:])
            original_total = max(0, int(original.get('duration', 0) or 0) + int(original.get('buffer', 0) or 0) + int(original.get('extend', 0) or 0))
            original_end = original_start + original_total
            if end_min <= start_min or start_min % 5 or end_min % 5:
                raise SchedulingError(400, '转单分段必须按 5 分钟为单位设置')
            if start_min < original_start or end_min > original_end:
                raise SchedulingError(400, f"转单分段必须完全位于原预约时段 {original.get('start')}–{original_end // 60:02d}:{original_end % 60:02d} 内")
            source_staff = str(original.get('staffId') or '')
            target_staff = data['target_staff_id']
            if not target_staff or target_staff == source_staff:
                raise SchedulingError(400, '请选择不同于原美容师的目标人员')
            staff = next((x for x in payload.get('staff', []) if str(x.get('id')) == target_staff), None)
            if not staff:
                raise SchedulingError(404, '目标美容师不存在')
            appt_date = str(original.get('date') or '')
            if rest_reason(payload, target_staff, appt_date):
                raise SchedulingError(409, '目标美容师当天休息，不能接收转单')
            shifts = _effective_staff_shifts(payload, target_staff, appt_date)
            tolerance = 0 if extra_shifts(payload, target_staff, appt_date) else 60
            if shifts is not None and shifts:
                covered = any(a <= start_min < b and end_min <= b + tolerance
                              for a,b in work_periods(shifts))
                if not covered:
                    raise SchedulingError(409, '转单时段不在目标美容师的工作时间内')
                if blocked_by_rest(payload, {
                    'staffId': target_staff, 'date': appt_date, 'start': data['segment_start'],
                    'duration': end_min - start_min,
                }):
                    raise SchedulingError(422, '转单时段不符合工作时间或休息前预约门槛')
            elif shifts == []:
                raise SchedulingError(409, '目标美容师当天没有可用班次')
            for other in appointments:
                if other is original or other.get('date') != appt_date or other.get('staffId') != target_staff or (other.get('status') == 'cancelled'):
                    continue
                other_start = int(str(other.get('start', '00:00'))[:2]) * 60 + int(str(other.get('start', '00:00'))[3:])
                other_end = other_start + int(other.get('duration', 0) or 0) + int(other.get('buffer', 0) or 0) + int(other.get('extend', 0) or 0)
                if start_min < other_end and other_start < end_min:
                    raise SchedulingError(409, f"目标美容师该时段与现有预约 {other.get('start')} 重叠")

            def make_segment(segment_id, seg_start, seg_end, staff_id, segment_type):
                item = dict(original)
                service_end = original_start + int(original.get('duration') or 0)
                wait_end = service_end + int(original.get('buffer') or 0)
                def intersection(left, right):
                    return max(0, min(seg_end, right) - max(seg_start, left))
                item.update({'id': segment_id, 'staffId': staff_id,
                             'start': f'{seg_start // 60:02d}:{seg_start % 60:02d}',
                             'duration': intersection(original_start, service_end),
                             'buffer': intersection(service_end, wait_end),
                             'extend': intersection(wait_end, original_end),
                             'segmentOf': str(original.get('segmentOf') or original.get('id')),
                             'segmentType': segment_type})
                if item.get('temporaryOvertime') is True:
                    if staff_id != source_staff or blocked_by_rest(payload, item):
                        item.pop('temporaryOvertime', None)
                        if staff_id == source_staff and blocked_by_rest(payload, item):
                            raise SchedulingError(422, '分单后原美容师的保留时段不符合休息规则，请调整分单范围')
                return item
            segments = []
            if start_min > original_start:
                segments.append(make_segment(str(original['id']), original_start, start_min, source_staff, '转出后保留'))
            transferred_id = uuid.uuid4().hex
            segments.append(make_segment(transferred_id, start_min, end_min, target_staff, '转入分段'))
            if end_min < original_end:
                post_id = uuid.uuid4().hex if start_min > original_start else str(original['id'])
                segments.append(make_segment(post_id, end_min, original_end, source_staff, '转出后保留'))
            appointments[appt_index:appt_index + 1] = segments
            group_id = str(original.get('groupId') or '')
            if group_id:
                for group in payload.get('bookingGroups', []):
                    if str(group.get('id')) == group_id:
                        ids = list(group.get('appointmentIds') or [])
                        try:
                            pos = ids.index(str(original['id']))
                            ids[pos:pos + 1] = [x['id'] for x in segments]
                        except ValueError:
                            ids.extend((x['id'] for x in segments))
                        group['appointmentIds'] = ids
                        break
            now = datetime.now().isoformat(timespec='seconds')
            next_revision = revision + 1
            transfer_id = uuid.uuid4().hex
            con.execute('\n            INSERT INTO schedule_transfer_history(\n                id,appointment_id,group_id,appointment_date,source_staff_id,\n                target_staff_id,segment_start,segment_end,action_by,created_at,detail_json\n            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)\n        ', (transfer_id, data['appointment_id'], group_id, appt_date, source_staff, target_staff, data['segment_start'], data['segment_end'], user['id'], now, json.dumps({'source_appointment': original, 'created_segment_id': transferred_id}, ensure_ascii=False)))
            con.execute('UPDATE schedule_state SET payload=?,revision=?,updated_at=?,updated_by=? WHERE id=1', (json.dumps(payload, ensure_ascii=False, separators=(',', ':')), next_revision, now, user['id']))
            self.audit(con, '预约分段转单', f"{data['segment_start']}-{data['segment_end']} 转给美容师 {target_staff}", actor, 'schedule_appointment', data['appointment_id'], original, {'target_staff_id': target_staff, 'segments': segments, 'revision': next_revision})
            return {'ok': True, 'transfer_id': transfer_id, 'revision': next_revision, 'payload': redact_state(payload, user), 'message': '预约分段转单成功，原美容师剩余时段已保留'}
        except Exception:
            raise

    @reading
    def schedule_workload(self, con, user, actor, date_from, date_to):
        """按日期区间汇总美容师预约量与服务/占用时长。"""
        if not granted(user, 'ext.scheduling.workload_read_all'):
            require(user, 'ext.scheduling.workload_read_self')
        row = _schedule_row(con)
        if not row:
            return {'date_from': date_from, 'date_to': date_to, 'items': []}
        payload = json.loads(row['payload'])
        staff_map = {str(item.get('id')): item for item in payload.get('staff', []) if isinstance(item, dict)}
        if not granted(user, 'ext.scheduling.workload_read_all'):
            link = con.execute('SELECT staff_id FROM schedule_staff_users WHERE user_id=?', (user['id'],)).fetchone()
            if not link:
                raise SchedulingError(403, '当前账号尚未绑定美容师，不能查看工作负荷')
            staff_map = {sid: staff for sid, staff in staff_map.items() if sid == str(link['staff_id'])}
        result = {sid: {'staff_id': sid, 'staff_name': staff.get('name', sid), 'appointment_count': 0, 'service_minutes': 0, 'occupied_minutes': 0, 'active_days': set(), 'rest_days': 0} for sid, staff in staff_map.items()}
        for appt in payload.get('appointments', []):
            if appt.get('restBlock') is True:
                continue
            if not isinstance(appt, dict) or appt.get('status') == 'cancelled':
                continue
            appt_date = str(appt.get('date') or '')
            if date_from and appt_date < date_from:
                continue
            if date_to and appt_date > date_to:
                continue
            sid = str(appt.get('staffId') or '')
            if sid not in result:
                continue
            try:
                service_minutes = max(0, int(appt.get('duration', 0) or 0))
                occupied_minutes = service_minutes + max(0, int(appt.get('buffer', 0) or 0)) + max(0, int(appt.get('extend', 0) or 0))
            except (TypeError, ValueError):
                continue
            result[sid]['appointment_count'] += 1
            result[sid]['service_minutes'] += service_minutes
            result[sid]['occupied_minutes'] += occupied_minutes
            result[sid]['active_days'].add(appt_date)
        for item in result.values():
            item['active_days'] = len(item['active_days'])
        return {'date_from': date_from, 'date_to': date_to, 'items': sorted(result.values(), key=lambda x: (-x['occupied_minutes'], x['staff_name']))}

    @reading
    def list_schedule_requests(self, con, user, actor, status):
        if not granted(user, 'ext.schedule_requests.read_all'):
            require(user, 'ext.schedule_requests.read_self')
        if granted(user, 'ext.schedule_requests.read_all'):
            if status == 'all':
                rows = con.execute('SELECT * FROM schedule_requests ORDER BY created_at DESC LIMIT 500').fetchall()
            else:
                rows = con.execute('SELECT * FROM schedule_requests WHERE status=? ORDER BY created_at DESC LIMIT 500', (status,)).fetchall()
        else:
            if status == 'all':
                rows = con.execute('SELECT * FROM schedule_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 200', (user['id'],)).fetchall()
            else:
                rows = con.execute('SELECT * FROM schedule_requests WHERE user_id=? AND status=? ORDER BY created_at DESC LIMIT 200', (user['id'], status)).fetchall()
        return [dict(row) for row in rows]

    @writing
    def create_schedule_request(self, con, user, actor, data):
        require(user, 'ext.schedule_requests.create_self')
        try:
            date.fromisoformat(data['request_date'])
        except ValueError as exc:
            raise SchedulingError(400, '申请日期必须为有效 YYYY-MM-DD') from exc
        link = con.execute('SELECT staff_id FROM schedule_staff_users WHERE user_id=?', (user['id'],)).fetchone()
        if user['role'] != 'admin' and (not link or link['staff_id'] != data['staff_id']):
            raise SchedulingError(403, '只能为本人绑定的美容师档案提交申请')
        target_staff_id = data['target_staff_id'].strip()
        if data['request_type'] == '换班申请':
            if not target_staff_id or target_staff_id == data['staff_id']:
                raise SchedulingError(400, '换班申请必须选择另一位美容师')
            state_row = _schedule_row(con)
            if not state_row:
                raise SchedulingError(409, '排班尚未初始化')
            schedule_payload = json.loads(state_row['payload'])
            staff_ids = {str(x.get('id')) for x in schedule_payload.get('staff', []) if isinstance(x, dict)}
            if data['staff_id'] not in staff_ids or target_staff_id not in staff_ids:
                raise SchedulingError(404, '换班美容师档案不存在')
        now = datetime.now().isoformat(timespec='seconds')
        request_id = uuid.uuid4().hex
        con.execute('\n        INSERT INTO schedule_requests(id,request_type,staff_id,target_staff_id,user_id,request_date,start_time,end_time,details,created_at,updated_at)\n        VALUES(?,?,?,?,?,?,?,?,?,?,?)\n    ', (request_id, data['request_type'], data['staff_id'], target_staff_id, user['id'], data['request_date'], data['start_time'], data['end_time'], data['details'], now, now))
        self.notify(con, '员工申请待审批', f"{user['display_name']}：{data['request_type']}（{data['request_date']}）", 'approval', source_key=f'schedule-request:{request_id}')
        self.audit(con, '提交排班申请', data['request_type'], actor, 'schedule_request', request_id, None, dict(data))
        return {'ok': True, 'id': request_id, 'status': 'pending'}

    @writing
    def review_schedule_request(self, con, user, actor, request_id, data):
        require(user, 'ext.schedule_requests.review')
        revision = None
        try:
            row = con.execute('SELECT * FROM schedule_requests WHERE id=?', (request_id,)).fetchone()
            if not row:
                raise SchedulingError(404, '申请不存在')
            if row['status'] != 'pending':
                raise SchedulingError(409, '该申请已处理')
            now = datetime.now().isoformat(timespec='seconds')
            if data['status'] == 'approved' and row['request_type'] == '休息申请':
                if not _validate_time(row['start_time']) or not _validate_time(row['end_time']):
                    raise SchedulingError(400, '休息申请必须填写有效的开始和结束时间')
                start_min = int(row['start_time'][:2]) * 60 + int(row['start_time'][3:])
                end_min = int(row['end_time'][:2]) * 60 + int(row['end_time'][3:])
                if end_min <= start_min or start_min % 5 or end_min % 5:
                    raise SchedulingError(400, '休息时间必须结束晚于开始，并按 5 分钟递增')
                try:
                    date.fromisoformat(row['request_date'])
                except ValueError as exc:
                    raise SchedulingError(400, '申请日期格式无效') from exc
                state_row = _schedule_row(con)
                if not state_row:
                    raise SchedulingError(409, '排班尚未初始化，无法落实休息申请')
                payload = json.loads(state_row['payload'])
                staff_id, day = (str(row['staff_id']), str(row['request_date']))
                if rest_reason(payload, staff_id, day):
                    raise SchedulingError(409, '该美容师当天已经安排休息')
                if extra_shifts(payload, staff_id, day):
                    raise SchedulingError(409, '该日已设置临时出勤，请直接调整临时出勤时段')
                shifts = _effective_staff_shifts(payload, staff_id, day)
                if not any((start_min >= int(x['start'][:2]) * 60 + int(x['start'][3:]) and end_min <= int(x['end'][:2]) * 60 + int(x['end'][3:]) for x in shifts)):
                    raise SchedulingError(409, '申请休息时间不在该美容师当天的工作时段内')
                for appt in payload.get('appointments', []):
                    if not isinstance(appt, dict) or str(appt.get('staffId')) != staff_id or str(appt.get('date')) != day or (appt.get('status') == 'cancelled'):
                        continue
                    try:
                        appt_start = int(appt['start'][:2]) * 60 + int(appt['start'][3:])
                        appt_end = appt_start + max(0, int(appt.get('duration', 0) or 0) + int(appt.get('buffer', 0) or 0) + int(appt.get('extend', 0) or 0))
                    except (KeyError, TypeError, ValueError):
                        continue
                    if start_min < appt_end and appt_start < end_min:
                        raise SchedulingError(409, f"休息时间与预约 {appt.get('customer') or appt.get('id')} 重叠，请先调整预约")
                remaining = []
                for shift in shifts:
                    a = int(shift['start'][:2]) * 60 + int(shift['start'][3:])
                    b = int(shift['end'][:2]) * 60 + int(shift['end'][3:])
                    if end_min <= a or start_min >= b:
                        remaining.append(dict(shift))
                        continue
                    if start_min > a:
                        remaining.append({'start': f'{a // 60:02d}:{a % 60:02d}', 'end': f'{start_min // 60:02d}:{start_min % 60:02d}'})
                    if end_min < b:
                        remaining.append({'start': f'{end_min // 60:02d}:{end_min % 60:02d}', 'end': f'{b // 60:02d}:{b % 60:02d}'})
                if not remaining:
                    payload.setdefault('daysOff', {}).setdefault(day, [])
                    if staff_id not in payload['daysOff'][day]:
                        payload['daysOff'][day].append(staff_id)
                    if isinstance(payload.get('daySchedules', {}).get(day), dict):
                        payload['daySchedules'][day].pop(staff_id, None)
                else:
                    payload.setdefault('daySchedules', {}).setdefault(day, {})[staff_id] = remaining
                revision = int(state_row['revision']) + 1
                con.execute('UPDATE schedule_state SET payload=?,revision=?,updated_at=?,updated_by=? WHERE id=1', (json.dumps(payload, ensure_ascii=False, separators=(',', ':')), revision, now, user['id']))
                self.audit(con, '落实员工休息申请', f"{day} {row['start_time']}-{row['end_time']}", actor, 'schedule_state', '1', {'revision': state_row['revision']}, {'revision': revision, 'staff_id': staff_id, 'date': day})
            elif data['status'] == 'approved' and row['request_type'] == '换班申请':
                state_row = _schedule_row(con)
                if not state_row:
                    raise SchedulingError(409, '排班尚未初始化，无法落实换班申请')
                payload = json.loads(state_row['payload'])
                day, staff_id, target_id = (str(row['request_date']), str(row['staff_id']), str(row['target_staff_id'] or ''))
                if not target_id or staff_id == target_id:
                    raise SchedulingError(400, '换班申请缺少有效的交换对象')
                staff_ids = {str(x.get('id')) for x in payload.get('staff', []) if isinstance(x, dict)}
                if staff_id not in staff_ids or target_id not in staff_ids:
                    raise SchedulingError(404, '换班美容师档案不存在')
                if any(rest_reason(payload, sid, day) or extra_shifts(payload, sid, day) for sid in (staff_id, target_id)):
                    raise SchedulingError(409, '交换对象当天已安排休息，不能直接互换班次')
                source_shifts = _effective_staff_shifts(payload, staff_id, day)
                target_shifts = _effective_staff_shifts(payload, target_id, day)
                if not source_shifts or not target_shifts or source_shifts == target_shifts:
                    raise SchedulingError(409, '两位美容师当天没有可互换的不同班次')
                for appt in payload.get('appointments', []):
                    if not isinstance(appt, dict) or str(appt.get('date')) != day or appt.get('status') == 'cancelled':
                        continue
                    sid = str(appt.get('staffId') or '')
                    new_shifts = target_shifts if sid == staff_id else source_shifts if sid == target_id else None
                    if new_shifts is None:
                        continue
                    try:
                        appt_start = int(appt['start'][:2]) * 60 + int(appt['start'][3:])
                        appt_end = appt_start + max(0, int(appt.get('duration', 0) or 0) + int(appt.get('buffer', 0) or 0) + int(appt.get('extend', 0) or 0))
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not any((appt_start >= int(x['start'][:2]) * 60 + int(x['start'][3:]) and appt_end <= int(x['end'][:2]) * 60 + int(x['end'][3:]) for x in new_shifts)):
                        raise SchedulingError(409, f"换班后预约 {appt.get('customer') or appt.get('id')} 不在新工作时段内，请先调整预约")
                day_map = payload.setdefault('daySchedules', {}).setdefault(day, {})
                day_map[staff_id] = target_shifts
                day_map[target_id] = source_shifts
                revision = int(state_row['revision']) + 1
                con.execute('UPDATE schedule_state SET payload=?,revision=?,updated_at=?,updated_by=? WHERE id=1', (json.dumps(payload, ensure_ascii=False, separators=(',', ':')), revision, now, user['id']))
                self.audit(con, '落实员工换班申请', f'{day} {staff_id} <-> {target_id}', actor, 'schedule_state', '1', {'revision': state_row['revision']}, {'revision': revision, 'staff_id': staff_id, 'target_staff_id': target_id, 'date': day})
                target_user = con.execute('SELECT user_id FROM schedule_staff_users WHERE staff_id=?', (target_id,)).fetchone()
                if target_user:
                    self.notify(con, '换班安排通知', f'{day} 已与另一位美容师互换当天班次，请查看排班。', 'approval', recipient_user_id=target_user['user_id'], source_key=f'schedule-swap-target:{request_id}')
            con.execute('UPDATE schedule_requests SET status=?,reviewed_by=?,reviewed_at=?,review_note=?,updated_at=? WHERE id=?', (data['status'], user['id'], now, data['review_note'], now, request_id))
            outcome = '通过' if data['status'] == 'approved' else '驳回'
            self.notify(con, '排班申请审批结果', f"你的{row['request_type']}已{outcome}。{data['review_note']}", 'approval', recipient_user_id=row['user_id'], source_key=f'schedule-request-result:{request_id}')
            self.audit(con, '审批排班申请', f"{row['request_type']} -> {data['status']}", actor, 'schedule_request', request_id, dict(row), {'status': data['status'], 'review_note': data['review_note']})
        except Exception:
            raise
        if revision is not None:
            pass
        return {'ok': True, 'status': data['status'], 'schedule_revision': revision}

