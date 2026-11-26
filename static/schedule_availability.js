/* Pure availability projection shared by the timeline and the overview. */
const ScheduleAvailability = (() => {
  const clock = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  });

  function shopClock(now = new Date()) {
    const parts = Object.fromEntries(clock.formatToParts(now).map(p => [p.type, p.value]));
    return {day: [parts.year, parts.month, parts.day].join('-'),
      minute: Number(parts.hour) * 60 + Number(parts.minute) + Number(parts.second) / 60};
  }

  function ruleFor(payload, staff, day) {
    return (payload.weeklyRest?.[staff] || []).reduce((rule, next) =>
      next.from <= day && (!rule || next.from > rule.from) ? next : rule, null);
  }

  function extraShifts(payload, staff, day) {
    return payload.extraAttendance?.[staff]?.[day];
  }

  function restReason(payload, staff, day) {
    if (extraShifts(payload,staff,day)?.length) return '';
    if (payload.permanentRestStaffIds?.includes(staff)) return '持续休息';
    if (payload.daysOff?.[day]?.includes(staff)) return '单日休息';
    const weekday = new Date(day + 'T00:00:00Z').getUTCDay() || 7;
    return ruleFor(payload, staff, day)?.weekdays.includes(weekday) ? '固定休息' : '';
  }

  const defaultRestLead = 50;

  function workPeriods(shifts, from, to) {
    const periods = [];
    for (const shift of shifts.slice().sort((a,b) => a.start-b.start)) {
      const start = Math.max(from, shift.start), end = Math.min(to, shift.end);
      if (end <= start) continue;
      const last = periods.at(-1);
      if (last && start <= last.end) last.end = Math.max(last.end, end);
      else periods.push({start, end});
    }
    return periods;
  }

  function fitsRestOvertime(shifts, from, to, start, end) {
    const periods = workPeriods(shifts,from,to);
    return periods.slice(1).some((next,index) => {
      const previous = periods[index];
      return previous.start <= start && start < end && end <= next.end &&
        start < next.start && end > previous.end;
    });
  }

  function bookingWindows(shifts, from, to, lead, overtime = 0, allowRestOverrun = true, breaks = []) {
    const periods = workPeriods(shifts,from,to);
    const windows = periods.map(({start,end}, index) => {
      const next = periods[index+1];
      return {
        start,
        end: next ? Math.min(next.start, end + (allowRestOverrun ? 60 : 0)) : Math.min(to+overtime,end+overtime),
        latestStart: end - Math.max(1, lead),
      };
    });
    return breaks.reduce((usable, pause)=>usable.flatMap(window=>{
      if (pause.end<=window.start || pause.start>=window.end) return [window];
      const left = {...window,end:pause.start,latestStart:Math.min(window.latestStart,pause.start-Math.max(1,lead))};
      const right = {...window,start:pause.end};
      return [left,right].filter(w=>w.start<w.end && w.start<=w.latestStart);
    }),windows);
  }

  function candidateStarts(windows, total, from, to, now = null) {
    if (!Number.isInteger(total) || total <= 0) return [];
    return windows.flatMap(window => {
      const starts = [];
      const lower = Math.max(window.start, from, now === null ? -Infinity : Math.ceil(now / 5) * 5);
      const upper = Math.min(window.end, to);
      for (let minute = lower; minute <= (window.latestStart ?? Infinity) && minute + total <= upper; minute += total) starts.push(minute);
      return starts;
    });
  }

  function closingStarts(windows, standardStarts, total, from, closing, now = null) {
    if (!Number.isInteger(total) || total <= 0) return [];
    const lastStandardEnd = standardStarts.length ? Math.max(...standardStarts)+total : from;
    const earliest = Math.max(from, lastStandardEnd, (Math.floor((closing-total)/5)+1)*5);
    return candidateStarts(windows.filter(window=>window.end > closing),total,earliest,Infinity,now)
      .filter(start=>start<closing);
  }

  return {shopClock, ruleFor, extraShifts, restReason, bookingWindows, fitsRestOvertime, defaultRestLead, candidateStarts, closingStarts};
})();
