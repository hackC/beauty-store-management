const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8') + '\nthis.handoff=ScheduleHandoff;', context);
const {suggest, freeWindows, offerTail, links} = context.handoff;
const plain = value => JSON.parse(JSON.stringify(value));
const a = {id:'a', windows:freeWindows([{start:540,end:630}], [{start:575,end:630}])};
const b = {id:'b', windows:[{start:575,end:630}]};
assert.deepEqual(plain(a.windows), [{start:540,end:575}]);
assert.deepEqual(plain(suggest([a,b], 540, 75, 10)), {
  first:'a', second:'b', start:540, transfer:575, serviceEnd:615, end:625,
});
assert.equal(suggest([a,b], 540, 75, 20), null);
assert.equal(suggest([a,b], 550, 75, 10), null);
assert.equal(suggest([a,b], 540, 55, 0), null);
assert.deepEqual(plain(offerTail(b.windows, 575, 540, 605)), {start:575, end:605});
assert.equal(offerTail(b.windows, 575, 540, 604), null);
assert.deepEqual(plain(links([
  {id:'one',segmentOf:'one',staffId:'a',start:'09:00'},
  {id:'two',segmentOf:'one',staffId:'b',start:'09:30'},
  {id:'other',segmentOf:'one',staffId:'c',start:'09:50',status:'cancelled'},
])), [{from:'one',to:'two',colorIndex:0}]);

async function dragTests() {
  const source = {id:'source', date:'2026-11-17', staffId:'a', start:'09:00', duration:75, status:'pending'};
  const target = {id:'target', date:source.date, staffId:'b', start:'09:00', duration:30, status:'pending'};
  const saves = [], exchanges = [], choices = [], notices = [];
  let next = 'cancel';
  const html = fs.readFileSync(process.argv[3], 'utf8');
  const getTime = text => Number(text.slice(0,2)) * 60 + Number(text.slice(3));
  const formatTime = n => [Math.floor(n/60),n%60].map(value => String(value).padStart(2,'0')).join(':');
  Object.assign(context, {
    data:{appointments:[source,target]}, ScheduleHandoff:context.handoff, savingAppointment:false, scheduleRevision:4,
    $:() => ({open:false}), storeScheduleHasPermission:() => true, isDayOff:() => false,
    notify: message => notices.push(message), mins:getTime, hhmm:formatTime, occupied:item=>item.duration,
    apptsForDate: () => [source,target], staffFreeWindows:() => [{start:570,end:615}], fitsWorkPeriodLoose:() => true,
    chooseHandoff:async options=>{choices.push(plain(options)); return next;},
    submitHandoff:async (...args)=>exchanges.push(args),
    save:async (...args)=>{saves.push(args);return true;}, render() {},
  });
  const start = html.indexOf('async function moveAppt(');
  const end = html.indexOf("$('datePicker').onchange", start);
  vm.runInContext(html.slice(start,end),context);
  const drop = () => vm.runInContext("moveAppt('source','b','target')", context);
  await drop();
  assert.deepEqual(choices[0], {split:{start:570,end:615},swap:true,staffId:'b'});
  assert.equal(saves.length + exchanges.length, 0);
  next = 'split';
  await drop();
  assert.deepEqual(exchanges[0], ['source','b','09:30','10:15',4]);
  next = 'swap';
  await drop();
  assert.equal(saves[0][1], 4);
  assert.equal(saves[0][0].appointments[0].staffId, 'b');
  assert.equal(saves[0][0].appointments[1].staffId, 'a');
  assert.equal(source.staffId, 'a');
}
dragTests().catch(error => {console.error(error);process.exitCode=1;});
