const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const [scriptPath, pagePath, cssPath] = process.argv.slice(2);
const body = {dataset: {}};
const timeline = {dataset: {}};
const wrap = {
  clientHeight: 564,
  scrollTop: 120,
  querySelector: () => ({offsetHeight: 54}),
};
const props = new Map();
let rendered = 0;
const elements = {timeline, timelineWrap: wrap};
const ctx = vm.createContext({
  $: key => elements[key],
  document: {body, documentElement: {style: {setProperty: (key, value) => props.set(key, value)}}},
  render: () => { rendered++; },
  window: {addEventListener() {}},
});
const page = fs.readFileSync(pagePath, 'utf8');
const css = fs.readFileSync(cssPath, 'utf8');
const get = code => vm.runInContext(code, ctx);
get(fs.readFileSync(scriptPath, 'utf8'));
get("let START=560, END=1255, PPM=2, DAY_HEIGHT=(END-START)*PPM, scheduleReady=true; let prefs=ScheduleDisplay.preferences({ppm:3, fontSize:18, theme:'warm'});");
get(page.slice(page.indexOf('function loadPrefs()'), page.indexOf('function localDate()')));
assert.equal(get('prefs.visibleHours'), 4, 'old pixels-per-hour preference defaults to 4 hours');
assert.equal(get('prefs.fontSize'), 18);
assert.equal(get('prefs.theme'), 'warm');
assert.equal(get('ScheduleDisplay.preferences({visibleHours:100}).visibleHours'), 6);
assert.equal(get('ScheduleDisplay.preferences({visibleHours:""}).visibleHours'), 4);
assert.deepEqual(JSON.parse(get('JSON.stringify(ScheduleDisplay.ticks(560, 665))')), [
  {time: 570, offset: 10, half: true},
  {time: 600, offset: 40, half: false},
  {time: 630, offset: 70, half: true},
  {time: 660, offset: 100, half: false},
]);
assert.deepEqual(JSON.parse(get('JSON.stringify(ScheduleDisplay.ticks(540, 600))')), [
  {time: 540, offset: 0, half: false},
  {time: 570, offset: 30, half: true},
]);

get('applyPrefs(); fitScheduleTimeline()');
assert.equal(rendered, 1);
assert.equal(get('PPM') * 240, 510);
assert.equal(wrap.scrollTop / get('PPM'), 60, 'resizing preserves time at viewport top');
assert.equal(props.get('--card-font'), '18px');
wrap.clientHeight = 408;
get("prefs = ScheduleDisplay.preferences({visibleHours: 5, halfHourLabels: false, cardNotes: false, columnWidth:'wide'}); applyPrefs(); fitScheduleTimeline();");
assert.equal(get('PPM') * 300, 354);
assert.equal(timeline.dataset.halfLabels, 'false');
assert.equal(timeline.dataset.cardNotes, 'false');
assert.equal(timeline.dataset.staffWidth, 'wide');
assert.equal(wrap.scrollTop / get('PPM'), 60);
assert.equal(rendered, 2);
assert.match(page, /body\.insertAdjacentHTML\('beforeend', gridLines\)/);
assert.match(page, /const top = \(visibleStart - START\) \* PPM;/);
assert.match(css, /\.time-grid-line \{ border-top: 1px solid/);
assert.match(css, /\.time-grid-line\.half-hour \{ border-top: 1px dashed/);
