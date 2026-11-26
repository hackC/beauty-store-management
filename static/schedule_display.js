/* Browser-local preferences and geometry for the daily schedule. */
const ScheduleDisplay = (() => {
  const defaults = Object.freeze({
    visibleHours: 4, fontSize: 15, cardPadding: 6, theme: 'soft',
    columnWidth: 'standard', halfHourLabels: true, cardNotes: true,
  });

  function number(value, fallback, min, max) {
    return value == null || value === '' || !Number.isFinite(Number(value))
      ? fallback : Math.max(min, Math.min(max, Math.round(Number(value))));
  }

  function preferences(saved) {
    const raw = saved || {};
    return {
      visibleHours: number(raw.visibleHours, defaults.visibleHours, 3, 6),
      fontSize: number(raw.fontSize, defaults.fontSize, 12, 22),
      cardPadding: number(raw.cardPadding, defaults.cardPadding, 2, 18),
      theme: ['soft', 'clean', 'warm'].includes(raw.theme) ? raw.theme : defaults.theme,
      columnWidth: ['compact', 'standard', 'wide'].includes(raw.columnWidth)
        ? raw.columnWidth : defaults.columnWidth,
      halfHourLabels: raw.halfHourLabels !== false,
      cardNotes: raw.cardNotes !== false,
    };
  }

  function pixelsPerMinute(viewportHeight, headerHeight, visibleHours) {
    return Math.max(1, viewportHeight - headerHeight) / (visibleHours * 60);
  }

  function ticks(start, end) {
    const result = [];
    for (let time = Math.ceil(start / 30) * 30; time < end; time += 30) {
      result.push({time, offset: time - start, half: time % 60 !== 0});
    }
    return result;
  }

  function initialScrollTop({now, start, pixelsPerMinute, viewportHeight, headerHeight, max}) {
    const lead = Math.max(0, viewportHeight - headerHeight) / 4;
    return Math.max(0, Math.min(max, (now - start) * pixelsPerMinute - lead));
  }

  function customerColors(keys) {
    const palette = [
      {edge:'#5481aa',background:'#e8f1fc',shade:'#ccdff4',pattern:'corner'},
      {edge:'#488372',background:'#e6f3ed',shade:'#cce8dc',pattern:'gradient'},
      {edge:'#766ba4',background:'#eeeafa',shade:'#dfd6f5',pattern:'corner'},
      {edge:'#9a704c',background:'#fbefe3',shade:'#f3ddc8',pattern:'gradient'},
      {edge:'#a06078',background:'#f9eaf1',shade:'#f1d6e4',pattern:'corner'},
      {edge:'#477e89',background:'#e5f3f6',shade:'#cae8ef',pattern:'gradient'},
      {edge:'#747f4c',background:'#eff3e3',shade:'#e1ebcd',pattern:'corner'},
      {edge:'#955e5a',background:'#f9ece9',shade:'#f1d8d4',pattern:'gradient'},
      {edge:'#5d7094',background:'#e9eef8',shade:'#d3def0',pattern:'corner'},
      {edge:'#5b8a9d',background:'#e7f2f8',shade:'#cfe6f0',pattern:'gradient'},
      {edge:'#8a668e',background:'#f4eaf7',shade:'#e8d6ed',pattern:'corner'},
      {edge:'#837954',background:'#f5f0e1',shade:'#ece1c6',pattern:'gradient'},
    ];
    const assigned = new Map(), used = new Set();
    for (const key of [...new Set(keys)].sort()) {
      let hash = 0;
      for (const character of key) hash = (Math.imul(hash,31)+character.codePointAt(0)) >>> 0;
      let index = hash % palette.length;
      if (used.size < palette.length) {
        while (used.has(index)) index = (index+1) % palette.length;
        used.add(index);
      }
      assigned.set(key,palette[index]);
    }
    return assigned;
  }

  function fitCardTitle(card, names, catalogue) {
    const title = card.querySelector('.aname');
    if (!title?.clientWidth) return;
    const fits = value => {
      title.textContent = value;
      return title.scrollHeight <= title.clientHeight+1 && title.scrollWidth <= title.clientWidth+1;
    };
    title.textContent = window.ScheduleProjects.fitNames(names, fits, catalogue);
  }

  return {preferences, pixelsPerMinute, ticks, initialScrollTop, customerColors, fitCardTitle};
})();
