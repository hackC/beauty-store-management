/* Scheduling handoffs use minute intervals and do not mutate the appointment list. */
const ScheduleHandoff = (() => {
  const minimum = 30;
  const step = 5;

  function freeWindows(shifts, occupied) {
    const busy = occupied.slice().sort((a, b) => a.start - b.start);
    return shifts.flatMap(shift => {
      const free = [];
      let cursor = shift.start;
      for (const block of busy) {
        if (block.end <= cursor || block.start >= shift.end) continue;
        if (cursor < block.start) free.push({...shift, start: cursor, end: block.start});
        cursor = Math.max(cursor, block.end);
      }
      if (cursor < shift.end) free.push({...shift, start: cursor});
      return free;
    });
  }

  function suggest(staff, earliest, duration, extra) {
    if (duration < minimum * 2 || duration % step || extra % step) return null;
    let best = null;
    for (const first of staff) {
      for (const second of staff) {
        if (first.id === second.id) continue;
        for (const a of first.windows) {
          for (const b of second.windows) {
            const start = Math.ceil(Math.max(a.start, earliest, b.start - (duration - minimum)) / step) * step;
            if (start > (a.latestStart ?? Infinity) || start + duration + extra > b.end) continue;
            const transfer = Math.ceil(Math.max(start + minimum, b.start) / step) * step;
            if (transfer > Math.min(a.end, start + duration - minimum, b.latestStart ?? Infinity)) continue;
            const candidate = {first: first.id, second: second.id, start, transfer,
                               serviceEnd: start + duration, end: start + duration + extra};
            if (!best || start < best.start || (start === best.start && transfer > best.transfer)) best = candidate;
          }
        }
      }
    }
    return best;
  }

  function offerTail(windows, earliest, start, end) {
    for (const window of windows) {
      const from = Math.ceil(Math.max(earliest, start + step, window.start) / step) * step;
      const to = Math.floor(Math.min(end, window.end) / step) * step;
      if (from <= (window.latestStart ?? Infinity) && to - from >= minimum) return {start: from, end: to};
    }
    return null;
  }

  function links(appointments) {
    const groups = new Map();
    for (const item of appointments) {
      if (!item.segmentOf || item.status === 'cancelled') continue;
      if (!groups.has(item.segmentOf)) groups.set(item.segmentOf, []);
      groups.get(item.segmentOf).push(item);
    }
    return [...groups.values()].flatMap((items, colorIndex) => {
      items.sort((a, b) => a.start.localeCompare(b.start));
      return items.slice(1).flatMap((to, index) => {
        const from = items[index];
        return from.staffId === to.staffId ? [] : [{from: from.id, to: to.id, colorIndex}];
      });
    });
  }

  return {minimum, freeWindows, suggest, offerTail, links};
})();
