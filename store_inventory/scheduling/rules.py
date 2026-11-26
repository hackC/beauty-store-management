"""Pure scheduling validations and conflict rules."""
import json
from datetime import date
from .policy import SchedulingError
from .availability import effective_staff_shifts as _effective_staff_shifts
from .availability import rest_reason, validate_settings, work_periods

def _validate_time(value):
    try:
        if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
            return False
        h, m = map(int, value.split(':'))
        return 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        return False

def _validate_schedule_payload(payload):
    if not isinstance(payload, dict):
        raise SchedulingError(400, '排班数据格式必须为对象')
    for key in ('staff', 'services', 'appointments'):
        if not isinstance(payload.get(key), list):
            raise SchedulingError(400, f'排班数据缺少有效的 {key} 列表')
        if any((not isinstance(item, dict) for item in payload[key])):
            raise SchedulingError(400, f'{key} 列表中包含无效项目')
    for key in ('daysOff', 'daySchedules', 'rotation', 'businessHours'):
        if key in payload and (not isinstance(payload[key], dict)):
            raise SchedulingError(400, f'{key} 数据格式不正确')
    for key in ('scheduleRules', 'bookingGroups', 'permanentRestStaffIds', 'staffOrder'):
        if key in payload and (not isinstance(payload[key], list)):
            raise SchedulingError(400, f'{key} 数据格式不正确')
    for key in ('staff', 'services', 'appointments'):
        ids = [str(item.get('id') or '') for item in payload[key]]
        if any((not item_id for item_id in ids)) or len(ids) != len(set(ids)):
            raise SchedulingError(400, f'{key} 项目 ID 不能为空且不能重复')
    validate_settings(payload)
    categories = payload.get('serviceCategories', [])
    if not isinstance(categories, list) or len(categories) > 300:
        raise SchedulingError(400, '项目类型列表格式不正确')
    category_ids, category_names = set(), set()
    for category in categories:
        if not isinstance(category, dict):
            raise SchedulingError(400, '项目类型格式不正确')
        key, name = category.get('id'), category.get('name')
        if not isinstance(key, str) or not key.strip() or len(key) > 100:
            raise SchedulingError(400, '项目类型 ID 格式不正确')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
            raise SchedulingError(400, '项目类型名称必须为 1–40 字')
        name_key = name.strip().casefold()
        if key in category_ids or name_key in category_names:
            raise SchedulingError(400, '项目类型 ID 或名称重复')
        category_ids.add(key)
        category_names.add(name_key)
    for service in payload['services']:
        category_id = service.get('categoryId', '')
        if not isinstance(category_id, str) or (category_id and category_id not in category_ids):
            raise SchedulingError(400, '项目所属类型不存在')
    for appointment in payload['appointments']:
        for flag in ('isMember', 'temporaryOvertime', 'restBlock'):
            if flag in appointment and type(appointment[flag]) is not bool:
                raise SchedulingError(400, f'预约标识 {flag} 必须为布尔值')
        project_id = appointment.get('serviceId')
        if appointment.get('restBlock') is True:
            if (not isinstance(project_id,str) or not project_id or not _validate_time(appointment.get('start'))
                    or type(appointment.get('duration')) is not int or appointment['duration'] != 0
                    or type(appointment.get('buffer')) is not int or not 5 <= appointment['buffer'] <= 720
                    or appointment['buffer'] % 5
                    or appointment.get('extend',0) != 0 or appointment.get('groupId')
                    or appointment.get('status') not in ('pending','cancelled')
                    or any(appointment.get(key) for key in ('customer','phoneTail','groupCustomer','groupPhoneTail',
                                                            'isMember','temporaryOvertime','segmentOf'))
                    or not isinstance(appointment.get('staffId'),str) or not appointment['staffId']):
                raise SchedulingError(400, '休息时间块格式无效')
            try:
                date.fromisoformat(appointment.get('date'))
            except (TypeError,ValueError) as exc:
                raise SchedulingError(400, '休息时间块日期无效') from exc
        remark = appointment.get('projectName', '')
        if not isinstance(remark, str) or len(remark) > (3000 if 'projectIds' in appointment else 120):
            raise SchedulingError(400, '项目备注格式不正确')
        if 'projectIds' in appointment:
            ids = appointment['projectIds']
            if (not isinstance(ids, list) or not ids
                    or any(not isinstance(item, str) or not item for item in ids)
                    or len(set(ids)) != len(ids) or ids[0] != appointment.get('serviceId')):
                raise SchedulingError(400, '预约项目清单格式不正确')
        if 'projectsTogether' in appointment and not isinstance(appointment['projectsTogether'], bool):
            raise SchedulingError(400, '预约项目计时方式格式不正确')
    if len(json.dumps(payload, ensure_ascii=False)) > 20 * 1024 * 1024:
        raise SchedulingError(413, '排班数据超过 20 MB')

def validate_rest_projects(old, new):
    before = {a['id']: a for a in old.get('appointments', [])}
    rest_ids = {s['id'] for s in new.get('services', []) if type(s.get('duration')) is int and s['duration'] == 0}
    for item in new.get('appointments', []):
        previous = before.get(item['id'], {})
        ids = item.get('projectIds') or [item.get('serviceId')]
        rest = item.get('restBlock') is True
        if (bool(previous.get('restBlock')) == rest
                and (previous.get('projectIds') or [previous.get('serviceId')]) == ids):
            continue
        if rest:
            if len(ids) != 1 or ids[0] not in rest_ids:
                raise SchedulingError(400, '请为休息时间块选择一个零分钟项目')
        elif any(isinstance(key, str) and key in rest_ids for key in ids):
            raise SchedulingError(400, '零分钟休息项目请单独添加')


def _schedule_conflict_keys(payload):
    """返回同一美容师同一天的重叠预约集合；包含服务、等待和延长时间。"""
    grouped = {}
    for appt in payload.get('appointments', []):
        if not isinstance(appt, dict) or appt.get('status') == 'cancelled':
            continue
        start = appt.get('start')
        if not _validate_time(start):
            continue
        try:
            h, m = map(int, start.split(':'))
            start_min = h * 60 + m
            duration = max(0, int(appt.get('duration', 0) or 0))
            buffer = max(0, int(appt.get('buffer', 0) or 0))
            extend = max(0, int(appt.get('extend', 0) or 0))
        except (TypeError, ValueError):
            continue
        end_min = start_min + duration + buffer + extend
        if end_min <= start_min:
            continue
        key = (str(appt.get('date') or ''), str(appt.get('staffId') or ''))
        grouped.setdefault(key, []).append((start_min, end_min, str(appt.get('id') or '')))
    conflicts = set()
    for (appt_date, staff_id), items in grouped.items():
        items.sort(key=lambda item: (item[0], item[1], item[2]))
        for i, left in enumerate(items):
            for right in items[i + 1:]:
                if right[0] >= left[1]:
                    break
                conflicts.add((appt_date, staff_id, *sorted((left[2], right[2]))))
    return conflicts

def validate_new_handoffs(old_payload, new_payload, user):
    """Validate newly committed automatic handoffs against the current roster."""
    from .policy import require

    saved_handoffs = {g['id'] for g in old_payload.get('bookingGroups', []) if g.get('automaticSplit') is True}
    appointments = {a['id']: a for a in new_payload.get('appointments', [])}
    old_ids = {a['id'] for a in old_payload.get('appointments', [])}
    staff_ids = {s['id'] for s in new_payload.get('staff', [])}
    hours = new_payload.get('businessHours') or {'start': '09:00', 'end': '21:00'}

    def minute(value):
        if not _validate_time(value):
            raise SchedulingError(400, '自动分单时间格式无效')
        return int(value[:2]) * 60 + int(value[3:])

    def amount(record, field):
        value = record.get(field, 0)
        if type(value) is not int or value < 0 or value % 5:
            raise SchedulingError(400, '自动分单时长须为非负的五分钟倍数')
        return value

    created = []
    for group in new_payload.get('bookingGroups', []):
        if group.get('automaticSplit') is not True or group['id'] in saved_handoffs:
            continue
        require(user, 'ext.appointments.transfer')
        members = group.get('appointmentIds', [])
        if len(members) != 2 or any(key in old_ids or key not in appointments for key in members):
            raise SchedulingError(400, '自动分单须在同次新增两段预约')
        first, second = sorted((appointments[key] for key in members), key=lambda a: minute(a.get('start')))
        day = first.get('date')
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise SchedulingError(400, '自动分单的预约日期无效') from exc
        if (day != second.get('date') or first.get('staffId') == second.get('staffId')
                or first.get('serviceId') != second.get('serviceId')
                or any(a.get('groupId') != group['id'] or a.get('segmentOf') != first['id']
                       or a.get('status') != 'pending' or a.get('temporaryOvertime') is True for a in (first, second))):
            raise SchedulingError(400, '自动分单必须是两位美容师同日连续接手同一项预约')
        boundary = minute(second['start'])
        if (minute(first['start']) + amount(first, 'duration') != boundary
                or amount(first, 'buffer') or amount(first, 'extend')):
            raise SchedulingError(400, '分单两段必须连续，等待和延长只记在尾段')
        for item in (first, second):
            sid = item.get('staffId')
            start = minute(item['start'])
            duration = amount(item, 'duration')
            end = start + duration + amount(item, 'buffer') + amount(item, 'extend')
            if (sid not in staff_ids or start % 5 or duration < 30
                    or not (minute(hours.get('start')) <= start < end <= minute(hours.get('end')))):
                raise SchedulingError(400, '自动分单每人须服务至少半小时，并位于营业时段内')
            if rest_reason(new_payload, sid, day):
                raise SchedulingError(409, '分单美容师当天休息，请重新计算')
            if not any(a <= start < end <= b for a,b in work_periods(_effective_staff_shifts(new_payload, sid, day))):
                raise SchedulingError(409, '分单已超出美容师班次，请重新计算')
        created.append((group, first, second))
    return created
