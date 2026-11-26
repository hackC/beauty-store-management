"""Recurring rest and dated attendance policy for schedule snapshots."""
from datetime import date, datetime, timedelta, timezone

from .policy import SchedulingError


FIELDS = ('weeklyRest', 'extraAttendance')
DEFAULT_REST_LEAD = 50
MAX_REST_LEAD = 240
REST_OVERRUN = 60


def shop_today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def weekly_rest(payload, staff, day):
    try:
        weekday = date.fromisoformat(day).isoweekday()
    except (TypeError, ValueError):
        return False
    rules = payload.get('weeklyRest', {}).get(staff, [])
    applicable = [rule for rule in rules if rule['from'] <= day]
    return bool(applicable and weekday in max(applicable, key=lambda x: x['from'])['weekdays'])


def extra_shifts(payload, staff, day):
    return payload.get('extraAttendance', {}).get(staff, {}).get(day)


def rest_reason(payload, staff, day):
    if extra_shifts(payload, staff, day):
        return ''
    if staff in payload.get('permanentRestStaffIds', []):
        return '持续休息'
    if staff in payload.get('daysOff', {}).get(day, []):
        return '单日休息'
    if weekly_rest(payload, staff, day):
        return '固定休息'
    return ''


def minute(value):
    h, m = map(int, value.split(':'))
    if not (0 <= h < 24 and 0 <= m < 60 and len(value) == 5):
        raise ValueError('invalid time')
    return h * 60 + m


def validate_settings(payload):
    def valid_date(value):
        return date.fromisoformat(value).isoformat() == value

    lead = payload.get('restBookingLeadMinutes', DEFAULT_REST_LEAD)
    if type(lead) is not int or not 0 <= lead <= MAX_REST_LEAD:
        raise SchedulingError(400, '休息前预约门槛须为 0–240 的整数分钟')
    try:
        for field in FIELDS:
            if not isinstance(payload.get(field, {}), dict):
                raise ValueError
        for records in payload.get('weeklyRest', {}).values():
            if not isinstance(records, list):
                raise ValueError
            dates = set()
            for item in records:
                begin, weekdays = item['from'], item['weekdays']
                if (not valid_date(begin) or begin in dates or not isinstance(weekdays, list)
                        or any(type(n) is not int or not 1 <= n <= 7 for n in weekdays)
                        or len(set(weekdays)) != len(weekdays)):
                    raise ValueError
                dates.add(begin)
        for dates in payload.get('extraAttendance', {}).values():
            for day, shifts in dates.items():
                if not valid_date(day) or not isinstance(shifts, list) or not shifts:
                    raise ValueError
                periods = sorted((minute(s['start']), minute(s['end'])) for s in shifts)
                if any(a >= b or a % 5 or b % 5 or (i and a < periods[i - 1][1])
                       for i, (a, b) in enumerate(periods)):
                    raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SchedulingError(400, '固定休息或临时出勤的日期、星期或班次格式无效') from exc


def effective_staff_shifts(payload: dict, staff_id: str, day: str):
    if rest_reason(payload, staff_id, day):
        return []
    exception = extra_shifts(payload, staff_id, day)
    if exception:
        return exception
    day_map = payload.get('daySchedules') if isinstance(payload.get('daySchedules'), dict) else {}
    if isinstance(day_map.get(day), dict) and staff_id in day_map[day]:
        return list(day_map[day].get(staff_id) or [])
    staff = next((x for x in payload.get('staff', []) if str(x.get('id')) == staff_id), None)
    if not staff:
        return []
    rules = payload.get('scheduleRules') or []
    mobile = [r for r in rules if not r.get('fixed')]
    rotation = payload.get('rotation') or {}
    try:
        diff = (date.fromisoformat(day) - date.fromisoformat(rotation.get('anchorDate') or day)).days
    except Exception:
        diff = 0
    steps = int(rotation.get('manualOffset', 0) or 0)
    if rotation.get('enabled') and diff >= 0:
        steps += diff // (7 if rotation.get('mode') == 'weekly' else 1)
    for rule in rules:
        members = list(rule.get('staffIds') or [])
        if not rule.get('fixed') and len(mobile) > 1 and (rule in mobile):
            idx = mobile.index(rule)
            offset = steps % len(mobile)
            members = list(mobile[(idx - offset + len(mobile)) % len(mobile)].get('staffIds') or [])
        if staff_id in members:
            return list(rule.get('shifts') or [])
    return list(staff.get('shifts') or [])


def work_periods(shifts):
    """Coalesce neighboring shifts so a zero-length break is not a rest period."""
    periods = []
    for start, end in sorted((minute(s['start']), minute(s['end'])) for s in shifts):
        if end <= start:
            continue
        if periods and start <= periods[-1][1]:
            periods[-1] = (periods[-1][0], max(periods[-1][1], end))
        else:
            periods.append((start, end))
    return periods


def timed_rest_blocks(periods, start, end, lead):
    for (_, rest_start), (rest_end, _) in zip(periods, periods[1:]):
        if rest_start <= start < rest_end:
            return True
        if start < rest_start and (rest_start - start < lead
                                  or end > min(rest_start + REST_OVERRUN, rest_end)):
            return True
        if start < rest_start:
            break
    return False


def fits_rest_overtime(payload, shifts, start, end):
    """Only an internal gap with work on both sides is eligible."""
    hours = payload.get('businessHours') or {}
    opening = minute(hours.get('start', '09:00'))
    closing = minute(hours.get('end', '21:00'))
    periods = [(max(a, opening), min(b, closing)) for a, b in work_periods(shifts)
               if a < closing and b > opening]
    return any(left_start <= start < end <= right_end and start < rest_end and end > rest_start
               for (left_start, rest_start), (rest_end, right_end) in zip(periods, periods[1:]))


def blocked_by_rest(payload, appointment, include_legacy=True, check_lead=True, pauses=None):
    staff, day = appointment.get('staffId'), appointment.get('date')
    reason = rest_reason(payload, staff, day)
    if reason and (include_legacy or reason == '固定休息'):
        return True
    extra = extra_shifts(payload, staff, day)
    try:
        start = minute(appointment['start'])
        end = start + sum(max(0, int(appointment.get(k) or 0)) for k in ('duration', 'buffer', 'extend'))
        shifts = effective_staff_shifts(payload, staff, day)
        hours = payload.get('businessHours') or {}
        opening = minute(hours.get('start', '09:00'))
        closing = minute(hours.get('end', '21:00'))
        periods = [(max(a, opening), min(b, closing)) for a,b in work_periods(shifts)
                   if a<closing and b>opening]
        if appointment.get('restBlock') is True:
            return not any(a<=start<end<=b for a,b in periods)
        if appointment.get('temporaryOvertime') is True:
            return not fits_rest_overtime(payload, shifts, start, end)
    except (KeyError, TypeError, ValueError):
        return bool(extra) or appointment.get('temporaryOvertime') is True or appointment.get('restBlock') is True
    lead = payload.get('restBookingLeadMinutes', DEFAULT_REST_LEAD) if check_lead else 0
    if timed_rest_blocks(periods, start, end, lead):
        return True
    if check_lead and periods:
        last_start, last_end = periods[-1]
        if last_start <= start < closing and (start>=last_end or last_end-start<lead):
            return True
        breaks = (pauses if pauses is not None else rest_block_starts(payload)).get((staff,day), ())
        if any(0 < pause-start < lead for pause in breaks):
            return True
    if extra:
        return not periods or not periods[0][0] <= start < end <= periods[-1][1]
    return False


def rest_block_starts(payload):
    starts = {}
    for item in payload.get('appointments', []):
        if item.get('restBlock') is True and item.get('status') != 'cancelled':
            starts.setdefault((item['staffId'], item['date']), []).append(minute(item['start']))
    return starts


def affected_appointments(old, new, today):
    before = {a['id']: a for a in old.get('appointments', [])}
    keys = ('staffId', 'date', 'start', 'duration', 'buffer', 'extend')
    affected = []
    pauses = rest_block_starts(new)
    for a in new.get('appointments', []):
        if a.get('status') in ('cancelled', 'done'):
            continue
        previous = before.get(a['id'])
        changed = (not previous or previous.get('status') in ('cancelled', 'done')
                   or any(a.get(k) != previous.get(k) for k in keys)
                   or any(bool(a.get(flag)) != bool(previous.get(flag))
                          for flag in ('temporaryOvertime', 'restBlock')))
        if a.get('restBlock') is True and not changed:
            continue
        if (changed or str(a.get('date', '')) >= today) and blocked_by_rest(new, a, check_lead=changed, pauses=pauses):
            if changed or not blocked_by_rest(old, previous, check_lead=False):
                affected.append(a)
    return affected
