const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const page = fs.readFileSync(process.argv[2], 'utf8');
const initial = JSON.parse(fs.readFileSync(0, 'utf8'));
const notifications = [];
let shown = true;
let response = {ok:true, status:200, json:async () => ({revision:2})};
const requests = [];
const elem = new Map();
const $ = id => {
    if (!elem.has(id)) elem.set(id, {value:'', textContent:'', style:{}, dataset:{}});
    return elem.get(id);
};
const fields = initial.appointments.filter(a => a.status !== 'cancelled').map(a => {
    const values = {
        person:a.customer, duration:String(a.duration), buffer:String(a.buffer), extend:String(a.extend),
        staff:a.staffId, service:a.serviceId, start:a.start,
    };
    const controls = Object.fromEntries(Object.entries(values).map(([k,v]) => [`[data-guest-${k}]`, {value:v}]));
    controls['[data-guest-member]'] = {checked:a.id==='primary'};
    controls['[data-guest-overtime]'] = {checked:a.id==='companion'};
    if (a.projectIds) {
        controls['[data-project-picker]'] = {
            projectSelection:{state:()=>({ids:a.projectIds,together:a.projectsTogether,complete:true})},
        };
    }
    return {dataset:{originalId:a.id}, querySelector: key => controls[key]};
});
$('apptGuestRows').querySelectorAll = selector => selector === '.batch-row' ? fields : [guestInput];
const singleInput = {disabled:false};
const guestInput = {disabled:false};
$('singleFields').querySelectorAll = () => [singleInput];
$('apptModal').classList = {remove(){shown = false;}};
Object.entries({apptId:'primary',apptDate:initial.bookingGroups[0].date,customer:'林',
    customerTitle:'女士',phoneTail:'0123',apptNote:'整组备注',guestCount:2,apptStart:'10:00'})
    .forEach(([id,value]) => {$(id).value = value;});

let accepted = false;
const ctx = vm.createContext({
    confirm: () => accepted,
    initial, window: {}, $, console, clearTimeout, notify: value => notifications.push(value), render() {},
    normalizeDigits: value => value, joinCustomerTitle: (name,title) => name + title,
    fetch: async (_, {body}) => { requests.push(JSON.parse(body)); return response; },
    colorFromGroupId: () => '#3355aa', esc: String, uid: () => 'generated',
    isDayOff: () => false, fitsWorkPeriodLoose: (_staff,_date,start,end) => start >= 540 && end <= 1260,
    fitsRestOvertime: (_staff,_date,start,end) => start >= 540 && end <= 1260,
    getStaffDayLoad: () => ({projected:0, appointmentCount:0, occupiedMinutes:0}),
    occupied: a => a.duration + a.buffer + a.extend,
    mins: t => {const [h,m] = t.split(':').map(Number);return h*60+m;},
    hhmm: t => `${String(Math.floor(t/60)).padStart(2,'0')}:${String(t%60).padStart(2,'0')}`,
});
const run = code => vm.runInContext(code, ctx);
run(`let data=initial, scheduleReady=true, scheduleConflictBlocked=false, scheduleRevision=1,
    scheduleRemoteRevision=0, scheduleSaving=false, scheduleSaveQueue=Promise.resolve(),
    groupPreviewTimer, selectedDate, START=540, END=1260;`);
function add(start,end) {
    run(page.slice(page.indexOf(start),page.indexOf(end,page.indexOf(start)+1)));
}
run(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[2]), 'schedule_projects.js'), 'utf8'));
run('const ScheduleProjects = window.ScheduleProjects;');
run(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[2]), 'schedule_group.js'), 'utf8'));
add('function projectState(', 'function fillSelects(');
add('function save(draft=', 'async function reloadScheduleFromServer');
add('let groupBookingPlan=null;', 'function apptGuestStaffOptions');
add('function moveGroupAnchor(', 'function updateAppointmentStart');
add('function previewGroupBooking(', "$('deleteAppt').onclick=");
run("editingAppointmentId='primary'; editingGroupId='group-1'; groupAnchorStart='10:00'; editingRevision=1;");
const submit = () => run("$('apptForm').onsubmit({preventDefault(){}})");
const start = index => fields[index].querySelector('[data-guest-start]');

(async()=>{
    run('showGroupFields(true)');
    assert.equal(singleInput.disabled,true);
    assert.equal(guestInput.disabled,false);
    // Manually setting the same time for consecutive appointments on one employee must block.
    start(1).value = '10:20';
    await submit();
    assert.equal(requests.length,0);
    assert.match($('groupStatus').textContent,/无法安排/);
    const savedId = fields[1].dataset.originalId;
    fields[1].dataset.originalId = '';
    run('previewGroupBooking(true)');
    assert.equal(start(1).value,'10:20');
    accepted = true;
    run('previewGroupBooking(true)');
    assert.equal(start(1).value,'11:00');
    assert.equal(requests.length,0);
    fields[1].dataset.originalId = savedId;
    delete fields[1].dataset.startTouched;

    // First a failed write. A server refusal keeps both the dialog and its draft open.
    response = {ok:false,status:403,json:async()=>({detail:'该账号无权修改此预约'})};
    $('apptStart').value = '11:02';
    await submit();
    assert.equal(shown,true);
    assert.equal(notifications.at(-1),'该账号无权修改此预约');
    assert.equal(run("data.appointments.find(a=>a.id==='primary').start"),'10:00');
    assert.equal(start(0).value,'11:02');
    assert.equal(start(1).value,'12:02');

    // A manually set companion keeps its own time when the shared time moves.
    fields[1].dataset.startTouched = 'true';
    start(1).value = '13:00';
    $('apptStart').value = '12:00';
    response = {ok:true,status:200,json:async()=>({revision:2})};
    await submit();
    assert.equal(shown,false);
    assert.equal(notifications.at(-1),'预约已更新');
    const submitted = requests.at(-1);
    assert.equal(submitted.revision,1);
    const lookup = Object.fromEntries(submitted.payload.appointments.map(a=>[a.id,a]));
    assert.equal(lookup.primary.start,'12:00');
    assert.equal(lookup.primary.isMember,true);
    assert.equal(lookup.primary.temporaryOvertime,false);
    assert.equal(lookup.companion.isMember,false);
    assert.equal(lookup.companion.temporaryOvertime,true);
    assert.deepEqual(Array.from(lookup.primary.projectIds), ['p1','p2']);
    assert.equal(lookup.primary.projectsTogether,true);
    assert.equal(lookup.primary.duration,60);
    assert.equal(lookup.companion.start,'13:00');
    assert.equal(lookup.companion.status,'done');
    assert.equal(lookup.companion.note,'同行专属备注');
    assert.equal(lookup.companion.phoneTail,'9999');
    assert.equal(lookup.companion.segmentOf,'old-segment');
    assert.equal(lookup.companion.projectName,'旧项目名');
    assert.equal(lookup.cancelled.status,'cancelled');
    assert.deepEqual(Array.from(submitted.payload.bookingGroups[0].appointmentIds),['primary','companion','cancelled']);
    console.log(JSON.stringify(submitted));
})().catch(error=>{console.error(error);process.exitCode=1});
