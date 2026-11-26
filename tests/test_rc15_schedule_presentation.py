from pathlib import Path
import shutil
import subprocess

import pytest


def test_schedule_presentation_rules():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    static = Path(__file__).resolve().parents[1] / 'static'
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
global.window = {};
const asset = file => fs.readFileSync(path.join(process.argv[1],file),'utf8');
for (const file of ['schedule_projects.js','schedule_display.js','schedule_report.js','schedule_availability.js','schedule_handoff.js']) {
  vm.runInThisContext(asset(file), {filename:file});
}
const {fullNames, fitNames} = window.ScheduleProjects;
const catalog = {services:[{id:'1',name:'深层清洁补水护理'},{id:'2',name:'深层清洁控油护理'},{id:'3',name:'肩颈放松'}]};
const record = {projectName:'深层清',projectIds:['2','1','3']};
const names = fullNames(catalog,record);
assert.deepEqual(names, ['深层清洁控油护理','深层清洁补水护理','肩颈放松']);
assert.deepEqual(fullNames(catalog,{serviceId:'gone',projectName:'旧项目备注'}), ['旧项目备注']);
assert.deepEqual(fullNames(catalog,{projectIds:['2','gone']}), ['深层清洁控油护理','已移除项目']);
assert.equal(fitNames(names,()=>true), '深层清洁控油护理＋深层清洁补水护理＋肩颈放松');
const catalogue = catalog.services.map(item=>item.name);
assert.match(fitNames(names.slice(0,1),s=>s.length<=7,catalogue), /….*控油/);
assert.match(fitNames(names.slice(1,2),s=>s.length<=7,catalogue), /….*补水/);
assert.match(fitNames(names,s=>s.length<=10,catalogue), / \+2$/);
const colors = ScheduleDisplay.customerColors(['林','陈','林']);
assert.equal(colors.size,2);
assert.notEqual(colors.get('陈').edge,colors.get('林').edge);
assert.notEqual(colors.get('陈').background,colors.get('林').background);
const variants = [...ScheduleDisplay.customerColors(Array.from({length:12},(_,i)=>'访客'+i)).values()];
assert.equal(new Set(variants.map(variant=>variant.background)).size,12);
assert.equal(new Set(variants.map(variant=>variant.pattern)).size,2);
assert.deepEqual(colors,ScheduleDisplay.customerColors(['陈','林']));

const {candidateStarts, bookingWindows, closingStarts} = ScheduleAvailability;
const from = 19*60, closing = 23*60, length = 90;
const shifts = [{start:from,end:closing}];
const regular = candidateStarts(bookingWindows(shifts,from,closing,50),length,from,closing);
const extended = bookingWindows(shifts,from,closing,50,60);
assert.deepEqual(regular,[1140,1230]);
assert.deepEqual(closingStarts(extended,regular,length,from,closing),[22*60]);
const busy = ScheduleHandoff.freeWindows(extended,[{start:23*60,end:24*60}]);
assert.deepEqual(closingStarts(busy,regular,length,from,closing),[]);
assert.deepEqual(closingStarts(extended,regular,length,from,closing,22*60+20),[]);

assert.deepEqual(closingStarts(extended,regular,length,from,closing,22*60+40),[]);

const rows = [
  {staff:'甲',staffId:'a',customer:'<示例>',project:names[0],start:540,service:70,extra:20},
  {staff:'乙',staffId:'b',customer:'乙客',project:names[1],start:660,service:60,extra:0},
  {staff:'甲',staffId:'a',customer:'甲客',project:'访客项目',start:721,service:40,extra:10},
];
const summary = ScheduleReport.summarize(rows,540,720);
assert.deepEqual(summary.workload.map(({rows, ...group})=>group),[{name:'甲',service:110,extra:30},{name:'乙',service:60,extra:0}]);
assert.deepEqual(summary.bins.map(x=>x.count),[1,1,1]);
assert.equal(summary.service+summary.extra,200);
const report = ScheduleReport.build({date:'2026-11-23',opening:540,closing:720,rows,working:['甲','乙'],resting:[],conflicts:0});
assert.match(report,/人员占用时长/);
assert.match(report,/预约开始时段/);
assert.match(report,/预约明细/);
assert.match(report,/深层清洁控油护理/);
assert.match(report,/&lt;示例&gt;/);
assert.doesNotMatch(report,/预约状态|<th>状态<\/th>/);
const page = asset('index_V2_5_5.html');
for (const [,inline] of page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) new vm.Script(inline);
assert.match(page, /class="aname">\$\{esc\(names\.join/);
assert.match(page, /class="appt-flags">/);
assert.match(page, /ScheduleReport\.build/);
assert.match(page, /card\.dataset\.cardPattern = colors\.pattern/);
const styles = asset('schedule_display.css');
assert.match(styles,/border-inline-start: 8px solid var\(--customer-line/);
assert.match(styles,/background-color: var\(--customer-bg/);
"""
    subprocess.run([node, '-e', script, str(static)], check=True, timeout=15)
