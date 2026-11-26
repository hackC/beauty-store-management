from pathlib import Path
import shutil
import subprocess

import pytest


def test_group_solver_and_report_detail():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    static = Path(__file__).resolve().parents[1] / 'static'
    script = r"""
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const assert = require('node:assert/strict');
for (const file of ['schedule_group.js','schedule_report.js']) {
  vm.runInThisContext(fs.readFileSync(path.join(process.argv[1],file),'utf8'));
}
const slot = (id,start=600,end=660) => ({staff:{id},start,end});
// All three have two choices; a greedy pass assigning A, then B, would strand the third.
assert.deepEqual(ScheduleGroup.solve([
  [slot('A'),slot('C')],[slot('B'),slot('C')],[slot('A'),slot('B')]
]).map(s=>s.staff.id).sort(),['A','B','C']);
assert.equal(ScheduleGroup.solve([[slot('A')],[slot('A')]]),null);
assert.deepEqual(ScheduleGroup.solve([
  [slot('A')],[slot('A',615,675),slot('A',660,720)]
]).map(s=>s.start),[600,660]);
assert.equal(ScheduleGroup.solve([[slot('A')],[]]),null);
const roles = ScheduleGroup.roles([{id:'a'},{id:'b'},{id:'c',segmentOf:'a'}],[
  {appointmentIds:['gone','b','a','c','b']}
]);
assert.deepEqual([...roles],[['b',{main:'b',primary:true}],['a',{main:'b',primary:false}]]);
assert.equal(ScheduleGroup.roles([{id:'a'},{id:'b'}],[{appointmentIds:['a','b'],automaticSplit:true}]).size,0);

const row = (staffId,staff,start,customer) => ({staffId,staff,start,customer,project:'护理',service:50,extra:10});
const build = rows=>ScheduleReport.build({date:'2026-11-23',opening:540,closing:1260,rows,
  working:[],resting:[],conflicts:0});
const html = build([row('1','郭郭',660,'乙'),row('2','同名',600,'甲'),row('1','郭郭',570,'<早>'),row('3','同名',700,'丙')]);
assert.equal((html.match(/class="staff-detail"/g)||[]).length,3);
assert.match(html,/共 4 笔/);
assert.match(html,/郭郭<span>2 笔/);
assert.ok(html.indexOf('&lt;早&gt;')<html.indexOf('11:00–12:00'));
assert.match(build([]),/共 0 笔/);
"""
    subprocess.run([node, '-e', script, str(static)], check=True, timeout=15)
