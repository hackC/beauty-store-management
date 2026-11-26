const fs = require('node:fs');
const assert = require('node:assert/strict');
const vm = require('node:vm');
function element() {
    return {children: [], classList: {add(){}}, append(...nodes){this.children.push(...nodes);},
        replaceChildren(){this.children = [];}, setAttribute(){}, querySelectorAll(){return [];}, querySelector(){return null;}};
}
const ctx = vm.createContext({window:{},document:{createElement:element,createTextNode:t=>t}});
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const moduleApi = ctx.window.ScheduleProjects;
const data = {
    serviceCategories: [{id:'set',name:'套餐'},{id:'facial',name:'护理'}],
    services: [
        {id:'p1',name:'深层洁肤补水套餐',categoryId:'set',duration:70,buffer:20},
        {id:'p2',name:'丝滑护发护理',categoryId:'facial',duration:35,buffer:15},
    ],
};
const serial = x => JSON.parse(JSON.stringify(x));
assert.deepEqual(serial(moduleApi.totals(data, ['p1','p2'], false)),
    {duration:105,buffer:20,projectName:'深层洁+肤补水+丝滑护'});
assert.deepEqual(serial(moduleApi.totals(data, ['p1','p2'], true)),
    {duration:70,buffer:20,projectName:'深层洁+肤补水+丝滑护'});
const tree = element();
const changed = [];
const inputs = moduleApi.mount(tree, data, {ids:['p1']}, (selection, totals) => changed.push(serial({selection,totals})));
const find = (root,text) => root.textContent === text ? root :
    (root.children || []).map(node=>find(node,text)).find(Boolean);
assert.ok(find(tree,'深层洁肤补水套餐'));
find(tree,'＋ 增加项目').onclick();
assert.equal(inputs.state().complete,false);
find(tree,'护理 ›').onclick();
assert.ok(find(tree,'‹ 返回项目类型'));
find(tree,'丝滑护发护理').onclick();
assert.deepEqual(serial(inputs.state().ids), ['p1','p2']);
assert.ok(find(tree,'深层洁+肤补水+丝滑护'));
const together = tree.children.find(node=>node.className==='project-pick-together').children[0];
together.checked = true;
together.onchange();
assert.equal(changed.at(-1).totals.duration,70);
assert.equal(changed.at(-1).totals.buffer,20);
