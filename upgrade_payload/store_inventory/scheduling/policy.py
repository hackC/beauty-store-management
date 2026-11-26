"""Action-specific scheduling authorization, independent of HTTP and SQLite."""
from copy import deepcopy


class SchedulingError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code, self.message = status_code, message


def granted(user, key):
    return user.get('role') == 'admin' or bool(user.get('permissions', {}).get(key))


def require(user, key):
    if not granted(user, key):
        raise SchedulingError(403, '当前账号没有此操作权限：' + key)


def scoped(user, key, staff_id, today, *records):
    if granted(user, key):
        return
    if (granted(user, key + '_self_today') and staff_id and records and
            all(str(r.get('staffId')) == staff_id and r.get('date') == today for r in records)):
        return
    raise SchedulingError(403, '没有操作该员工或日期的权限：' + key)


CONTACT_FIELDS = ('phoneTail', 'groupPhoneTail', 'note', 'notes')
GROUP_CONTACT_FIELDS = ('phoneTail', 'note', 'notes')


def _index(items):
    if not isinstance(items, list) or any(not isinstance(x, dict) or not x.get('id') for x in items):
        raise SchedulingError(400, '预约或预约分组格式无效')
    indexed = {str(x['id']): x for x in items}
    if len(indexed) != len(items):
        raise SchedulingError(400, '预约或预约分组 ID 重复')
    return indexed


def redact_state(payload, user, staff_id=None):
    result = deepcopy(payload)
    if not granted(user, 'ext.appointments.read'):
        result['bookingGroups'] = []
        if not granted(user, 'ext.scheduling.rules_update'):
            for field in ('weeklyRest', 'extraAttendance'):
                if field in result:
                    records = result[field]
                    result[field] = ({staff_id: records[staff_id]}
                                     if staff_id in records and granted(user, 'ext.appointments.read_self') else {})
        if not granted(user, 'ext.appointments.read_self'):
            result['appointments'] = []
            return result
        # Return only this employee's appointment data. Shared roster names
        # remain useful for selecting a shift-swap partner.
        result['appointments'] = [
            {**item, 'groupId': '', 'groupCustomer': '', 'groupPhoneTail': '', 'groupSize': 1}
            for item in result.get('appointments', [])
            if staff_id and str(item.get('staffId')) == staff_id
        ]
        result['staff'] = [
            item if str(item.get('id')) == staff_id else
            {key: item[key] for key in ('id', 'name', 'color') if key in item}
            for item in result.get('staff', [])
        ]
        if not granted(user, 'ext.scheduling.read'):
            result['daySchedules'] = {
                day: {staff_id: shifts[staff_id]}
                for day, shifts in (result.get('daySchedules') or {}).items()
                if staff_id and isinstance(shifts, dict) and staff_id in shifts
            }
            result['daysOff'] = {
                day: [staff_id] for day, ids in (result.get('daysOff') or {}).items()
                if staff_id and isinstance(ids, list) and staff_id in ids
            }
            result['permanentRestStaffIds'] = [sid for sid in result.get('permanentRestStaffIds', []) if sid == staff_id]
            # Preserve this employee's effective rule periods, without disclosing
            # the assignments of other employees.
            result['scheduleRules'] = [
                {**rule, 'staffIds': [sid for sid in rule.get('staffIds', []) if sid == staff_id]}
                for rule in result.get('scheduleRules', [])
            ]
    if granted(user, 'ext.appointments.contact_read'):
        return result
    for name, fields in [('appointments', CONTACT_FIELDS), ('bookingGroups', GROUP_CONTACT_FIELDS)]:
        for item in result.get(name, []):
            for field in fields:
                if field in item:
                    item[field] = ''
    return result


def _guard_contact(old, new, fields, user):
    # Preserve withheld values when a redacted full-state client submits blanks.
    # A caller without field-edit permission can never overwrite a hidden field.
    for key in fields:
        a, b = (old or {}).get(key, ''), new.get(key, '')
        if not granted(user, 'ext.appointments.contact_read') and not b and a:
            new[key] = a
            b = a
        if a != b:
            require(user, 'ext.appointments.contact_edit')


def authorize_state(old, incoming, user, staff_id, today):
    """Return the validated write payload; reject the entire composite on any forbidden diff."""
    new = deepcopy(incoming)
    before = _index(old.get('appointments', []))
    after = _index(new.get('appointments', []))
    changes = {}
    for aid in before.keys() | after.keys():
        a, b = before.get(aid), after.get(aid)
        if b is not None:
            _guard_contact(a, b, CONTACT_FIELDS, user)
        if a == b:
            continue
        if a is None:
            scoped(user, 'ext.appointments.create', staff_id, today, b)
            changes[aid] = 'create'
        elif b is None:
            scoped(user, 'ext.appointments.delete', staff_id, today, a)
            changes[aid] = 'delete'
        else:
            if a.get('staffId') != b.get('staffId'):
                require(user, 'ext.appointments.transfer')
            if a.get('status') != b.get('status'):
                scoped(user, 'ext.appointments.status_change', staff_id, today, a, b)
            if {k:v for k,v in a.items() if k != 'status'} != {k:v for k,v in b.items() if k != 'status'}:
                scoped(user, 'ext.appointments.update', staff_id, today, a, b)
            changes[aid] = 'update'

    old_groups = _index(old.get('bookingGroups', []))
    new_groups = _index(new.get('bookingGroups', []))
    for aid in changes:
        a, b = before.get(aid), after.get(aid)
        old_gid = str((a or {}).get('groupId') or '')
        new_gid = str((b or {}).get('groupId') or '')
        if old_gid != new_gid and new_gid:
            group = new_groups.get(new_gid)
            if not group or aid not in group.get('appointmentIds', []):
                raise SchedulingError(400, '预约与分组关联不一致')
        if old_gid != new_gid:
            for group, index in ((old_groups.get(old_gid), before), (new_groups.get(new_gid), after)):
                if not group:
                    continue
                for member in group.get('appointmentIds', []):
                    if member != aid and member in index:
                        scoped(user, 'ext.appointments.' + changes.get(member, 'update'), staff_id, today, index[member])
    for gid in old_groups.keys() | new_groups.keys():
        a, b = old_groups.get(gid), new_groups.get(gid)
        if b is not None:
            _guard_contact(a, b, GROUP_CONTACT_FIELDS, user)
        if a == b:
            continue
        ids = set()
        for group, index in ((a, before), (b, after)):
            if group is None:
                continue
            members = group.get('appointmentIds', [])
            if not isinstance(members, list) or not members or any(not isinstance(x, str) or x not in index for x in members):
                raise SchedulingError(400, '预约分组必须引用存在的预约')
            ids.update(members)
        # Group membership can affect every referenced appointment, even when its
        # own record did not change. Each old/new record must be in scope.
        for aid in ids:
            records = [x for x in (before.get(aid), after.get(aid)) if x is not None]
            action = changes.get(aid, 'update')
            scoped(user, 'ext.appointments.' + action, staff_id, today, *records)

    for field in ('daySchedules', 'daysOff'):
        a, b = old.get(field, {}), new.get(field, {})
        if not isinstance(a, dict) or not isinstance(b, dict):
            raise SchedulingError(400, '班次或休息数据格式无效')
        for day in a.keys() | b.keys():
            av, bv = a.get(day, {} if field == 'daySchedules' else []), b.get(day, {} if field == 'daySchedules' else [])
            if field == 'daySchedules':
                if not isinstance(av, dict) or not isinstance(bv, dict):
                    raise SchedulingError(400, '日班次格式无效')
                changed = {sid for sid in av.keys() | bv.keys() if av.get(sid) != bv.get(sid)}
            else:
                if not isinstance(av, list) or not isinstance(bv, list) or any(not isinstance(x, str) for x in av+bv):
                    raise SchedulingError(400, '休息名单格式无效')
                changed = set(av) ^ set(bv)
            for sid in changed:
                scoped(user, 'ext.scheduling.shift_update', staff_id, today, {'staffId': sid, 'date': day})
    for key in old.keys() | new.keys():
        if key not in {'appointments', 'bookingGroups', 'daySchedules', 'daysOff'} and old.get(key) != new.get(key):
            require(user, 'ext.scheduling.rules_update')
    return new


def redact_customer(row, user, namespace='customers'):
    result = dict(row)
    if not granted(user, 'ext.' + namespace + '.phone_read'):
        result['phone_tail'] = ''
    if not granted(user, 'ext.' + namespace + '.notes_read'):
        result['notes'] = ''
    return result
