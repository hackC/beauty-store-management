const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');

const staticDir = process.argv[2];
const schedule = fs.readFileSync(path.join(staticDir, 'index_V2_5_5.html'), 'utf8');
const axis = {offsetWidth: 54, querySelector: () => ({offsetHeight: 54})};
const wrap = {
    clientHeight: 534, scrollHeight: 1494, scrollLeft: 0, scrollTop: 0,
    querySelector: () => axis,
    querySelectorAll: () => [
        {dataset: {staffId: 's1'}, offsetLeft: 54},
        {dataset: {staffId: 's2'}, offsetLeft: 190},
        {dataset: {staffId: 's3'}, offsetLeft: 326},
    ],
};
const ctx = vm.createContext({$: () => wrap,
    ScheduleAvailability: {shopClock: () => ({day:'today', minute:855})}});
const run = code => vm.runInContext(code, ctx);
run(fs.readFileSync(path.join(staticDir, 'schedule_display.js'), 'utf8'));
run("let selectedDate='today', START=540, PPM=2, boundScheduleStaff='s3'; function localDate(){return 'today';}");
run(schedule.slice(schedule.indexOf('function focusInitialSchedule()'), schedule.indexOf('function saveDisplayPrefs(')));
run('focusInitialSchedule()');
assert.equal(wrap.scrollLeft, 272);
assert.equal(wrap.scrollTop, 510);
wrap.scrollLeft = 0;
run("boundScheduleStaff=null; START=960; focusInitialSchedule()");
assert.equal(wrap.scrollLeft, 0);
assert.equal(wrap.scrollTop, 0);
run("START=0; focusInitialSchedule()");
assert.equal(wrap.scrollTop, 960);
assert.equal((schedule.match(/requestAnimationFrame\(focusInitialSchedule\)/g) || []).length, 1);
