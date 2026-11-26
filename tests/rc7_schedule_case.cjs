'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(process.argv[2], 'utf8');
const extract = (start, end) => {
  const first = html.indexOf(start);
  const last = html.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first);
  return html.slice(first, last);
};

async function testLogout() {
  const button = {disabled:false, textContent:'退出', addEventListener(name, action) {
    assert.equal(name, 'click');
    this.click = action;
  }};
  const notices = [], locations = [], posted = [];
  let response = {ok:false, json:async () => ({detail:'服务暂忙'})};
  const context = {
    $:id => {assert.equal(id, 'scheduleLogoutBtn'); return button;},
    clearInterval:() => {},
    scheduleDeviceHeartbeatTimer:1,
    scheduleSocket:{close:() => {}},
    notify:msg => notices.push(msg),
    window:{location:{replace:path => locations.push(path)}},
    fetch:async (path, options) => {posted.push([path, options.method]); return response;},
  };
  vm.runInNewContext(extract('async function logoutScheduleUser()', 'async function refreshScheduleNotificationCount'), context);
  await button.click();
  assert.deepEqual(locations, []);
  assert.match(notices.at(-1), /退出未确认.*服务暂忙/);
  assert.equal(button.disabled, false);
  response = {ok:true};
  await Promise.all([button.click(), button.click()]);
  assert.deepEqual(locations, ['/']);
  assert.deepEqual(posted, [['/api/auth/logout', 'POST'], ['/api/auth/logout', 'POST']]);
}

function testAutoAssignment() {
  const byId = {
    guestCount:{value:1}, apptDate:{value:'2026-11-20'}, apptDuration:{value:45},
    apptBuffer:{value:0}, apptExtend:{value:0}, apptService:{value:'p1'},
    apptStaff:{value:'', get selectedOptions() {return [{textContent:this.value}];}},
    apptStart:{value:''}, apptAutoStatus:{hidden:true, textContent:''}, apptId:{value:''},
    apptAutoAssign:{hidden:false, addEventListener(name, handler) {
      assert.equal(name,'click');
      this.click = handler;
    }},
  };
  const shifts = {A:[{start:'09:00',end:'12:00'}], B:[{start:'09:05',end:'11:00'}]};
  const context = vm.createContext({
    $:id => byId[id], START:540, END:720, STEP:5, OVERTIME_TOLERANCE:0,
    getExcludeIds:() => byId.apptId.value ? [byId.apptId.value] : [],
    storeScheduleHasPermission:() => false,
    getWorkSegments:(staff,date) => date==='2026-11-21' && staff==='A' ? [] : shifts[staff], isDayOff:(staff, date) => date==='2026-11-21' && staff==='A',
    mins:t => Number(t.slice(0,2))*60 + Number(t.slice(3)),
    hhmm:n => String(Math.floor(n/60)).padStart(2,'0') + ':' + String(n%60).padStart(2,'0'),
    occupied:x => x.duration+x.buffer+x.extend,
    data:{staff:[{id:'A'},{id:'B'}], appointments:[
      {date:'2026-11-20',staffId:'A',start:'09:00',duration:30,buffer:0,extend:0,status:'pending'},
    ]},
  });
  const run = code => vm.runInContext(code, context);
  run(fs.readFileSync(path.join(path.dirname(process.argv[2]),'schedule_availability.js'),'utf8'));
  run("ScheduleAvailability.shopClock = () => ({day:'2026-11-16',minute:720});");
  run(extract('function staffBookingWindows(', 'function collectShiftRows('));
  run('let singleStartAuto = true, singleStaffAuto = true, singleHandoff = null;');
  run(extract('function findNextSlot(', 'function openAppt('));
  run('updateSingleAssignment()');
  assert.equal(byId.apptStaff.value, 'B');
  assert.equal(byId.apptStart.value, '09:05');
  byId.apptDuration.value = 130;
  run('updateSingleAssignment()');
  assert.equal(byId.apptStaff.value, 'A');
  assert.equal(byId.apptStart.value, '09:30');
  byId.apptDate.value = '2026-11-21';
  run('updateSingleAssignment()');
  assert.equal(byId.apptStaff.value, '');
  assert.equal(byId.apptStart.value, '');
  assert.match(byId.apptAutoStatus.textContent, /没有完整空档/);
  byId.apptDuration.value = 45;
  run('updateSingleAssignment()');
  assert.equal(byId.apptStaff.value, 'B');
  byId.apptStaff.value = 'A';
  run('singleStaffAuto = false; updateSingleAssignment()');
  assert.equal(byId.apptStaff.value, 'A');
  assert.equal(byId.apptStart.value, '');

  run(extract("$('apptAutoAssign').addEventListener", "['apptDuration','apptBuffer','apptExtend']"));
  byId.apptDate.value = '2026-11-20';
  byId.apptStart.value = '09:07';
  byId.apptAutoAssign.click();
  assert.equal(byId.apptStaff.value, 'B');
  assert.equal(byId.apptStart.value, '09:07');

  context.data.appointments.push({id:'edit',date:'2026-11-20',staffId:'B',start:'09:07',
    duration:45,buffer:0,extend:0,status:'pending'});
  byId.apptAutoAssign.click();
  assert.equal(byId.apptStaff.value, '');
  assert.equal(byId.apptStart.value, '09:07');
  assert.match(byId.apptAutoStatus.textContent, /没有美容师能在 09:07 接单/);

  byId.apptId.value = 'edit';
  byId.apptAutoAssign.click();
  assert.equal(byId.apptStaff.value, 'B');
  assert.equal(byId.apptStart.value, '09:07');
  byId.apptDuration.value = 125;
  run('updateSingleAssignment()');
  assert.equal(byId.apptStaff.value, '');
  assert.equal(byId.apptStart.value, '09:07');

  byId.apptId.value = '';
  byId.apptDuration.value = 45;
  byId.apptStart.value = '';
  byId.apptAutoAssign.click();
  assert.equal(byId.apptStaff.value, 'A');
  assert.equal(byId.apptStart.value, '09:30');
}

(async () => {await testLogout(); testAutoAssignment();})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
