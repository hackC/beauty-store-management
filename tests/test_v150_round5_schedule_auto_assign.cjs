const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");
const html = fs.readFileSync(path.join(__dirname, "..", "static", "index_V2_5_5.html"), "utf8");
const start = html.indexOf("function findNextSlot(");
const middle = html.indexOf("\nconst AUTO_ASSIGN_BALANCE_WINDOW", start);
const end = html.indexOf("\nfunction computeSearchStart", middle);
assert(start >= 0 && middle > start && end > middle, "schedule algorithm functions must exist");
const code = html.slice(start, middle) + "\n" + html.slice(middle, end);
function runScenario(secondStaffStart) {
  const sandbox = {
    START: 540, END: 1080, STEP: 5, OVERTIME_TOLERANCE: 0,
    mins: value => typeof value === "number" ? value : Number(value.slice(0,2))*60 + Number(value.slice(3,5)),
    occupied: a => Number(a.duration || 0) + Number(a.buffer || 0) + Number(a.extend || 0),
    isDayOff: () => false,
    getWorkSegments: staffId => sandbox.data.staff.find(s => s.id === staffId).shifts,
    getExcludeIds: () => [],
    data: {
      staff: [
        {id:"A", shifts:[{start:"09:00",end:"18:00"}]},
        {id:"B", shifts:[{start:"09:00",end:"18:00"}]}
      ],
      appointments: [
        {id:"a1",date:"2026-11-14",staffId:"A",start:"09:00",duration:60,status:"pending"},
        {id:"a2",date:"2026-11-14",staffId:"A",start:"12:00",duration:120,status:"pending"},
        {id:"b1",date:"2026-11-14",staffId:"B",start:"09:00",duration:secondStaffStart-540,status:"pending"}
      ]
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/schedule_availability.js"), "utf8"), sandbox);
  vm.runInContext(html.slice(html.indexOf("function staffBookingWindows("), html.indexOf("function fitsWorkPeriodLoose(")), sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.computeSingleEarliest("2026-11-14", null, 60, 0, 0, 600);
}
const withinWindow = runScenario(610);
assert.strictEqual(withinWindow.staffId, "B");
assert.strictEqual(withinWindow.start, 610);
const outsideWindow = runScenario(615);
assert.strictEqual(outsideWindow.staffId, "A");
assert.strictEqual(outsideWindow.start, 600);
console.log("PASS: load balancing inside 10-minute window; earliest-slot priority outside window");
