const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const [projectsFile, inventoryFile] = process.argv.slice(2);
const catalog = {
  serviceCategories: [{id:'face',name:'面部'}, {id:'body',name:'身体'}],
  services: [{id:'facial',name:'补水护理',categoryId:'face'},
    {id:'massage',name:'背部按摩',categoryId:'body'},
    {id:'old',name:'旧服务',categoryId:'face',enabled:false},
    {id:'legacy',name:'旧未分类项目'}],
};
const page = {window:{}};
vm.runInNewContext(fs.readFileSync(projectsFile, 'utf8'), page);
const projects = page.window.ScheduleProjects;
const ids = rows => Array.from(rows, row => row.id);
assert.deepEqual(ids(projects.types(catalog)), ['face','body','']);
assert.deepEqual(ids(projects.forType(catalog, 'face')), ['facial']);
assert.deepEqual(ids(projects.forType(catalog, 'face', 'old')), ['facial','old']);
assert.deepEqual(ids(projects.forType(catalog, '')), ['legacy']);
assert.equal(projects.remark(catalog,{serviceId:'facial',projectName:'历史项目名称'}), '历史项目名称');
assert.equal(projects.remark({services:catalog.services},{serviceId:'legacy'}), '旧未分类项目');

const inventory = fs.readFileSync(inventoryFile, 'utf8');
const begin = inventory.indexOf('const mobileScanPermissions =');
const end = inventory.indexOf('function drawBarcodeAimRegion', begin);
const fields = {};
const scans = [];
const notices = [];
const modeButtons = ['入库', '出库', '盘点调整'].map(scanMovement => ({
  dataset:{scanMovement}, addEventListener(){},
}));
const mode = {shown:false};
const type = {value:'盘点调整'};
let grants = new Set(['ext.inventory.outbound_create']);
let rows = [];
function row(id, selected = false) {
  const fieldsBySelector = {
    '.op-qty': {value:''},
    '.op-note': {value:''},
    '.op-search': {id, value:'', classList:{contains:x=>x==='op-search'}, dispatchEvent(){}},
  };
  const rowData = {
    dataset:selected ? {productId:'p1'} : {},
    querySelector:name=>fieldsBySelector[name],
  };
  fieldsBySelector['.op-search'].closest = () => rowData;
  fields[id] = fieldsBySelector['.op-search'];
  return rowData;
}
const context = vm.createContext({
  window:{}, Event,
  document:{querySelectorAll:()=>modeButtons},
  $:id => ({
    opRows:{replaceChildren(){rows=[];}, querySelector:sel=>rows[0]?.querySelector(sel)},
    opType:type,
    mobileScanMode:{classList:{add(){mode.shown=true;}, remove(){mode.shown=false;}}},
  }[id] || fields[id]),
  toast:text=>notices.push(text),
  hasPermission:key=>grants.has(key),
  pendingInventorySubmission:null,
  addOpRow(){rows.push(row('row'+rows.length));},
  startBarcodeScan:(id, movement)=>scans.push([id,movement]),
  barcodeScanActive:true,
  barcodeTargetId:'',
  barcodeMovementType:'',
  normalizeLinearBarcode:value=>value,
  stopBarcodeScan(){context.barcodeMovementType='';},
  loadAllProducts:async()=>{},
  allProducts:[{id:'p1',name:'测试商品',barcode:'123456789012'}],
  chooseOpProduct:(input,id)=>{input.closest('.op-row').dataset.productId=id;},
});
vm.runInContext(inventory.slice(begin,end), context);
(async () => {
  rows = [row('old',true), row('other',true)];
  context.pendingInventorySubmission = {uncertain:true};
  vm.runInContext('startMobileScan()', context);
  assert.equal(mode.shown,false);
  assert.equal(rows.length,2);
  assert.equal(notices.length,1);
  context.pendingInventorySubmission = null;
  vm.runInContext('startMobileScan()', context);
  assert.equal(mode.shown,true);
  assert.equal(rows.length,1);
  assert.equal(rows[0].dataset.productId,undefined);
  assert.deepEqual(modeButtons.map(button=>button.hidden), [true,false,true]);
  vm.runInContext('selectMobileScan("入库")',context);
  assert.equal(scans.length,0);
  assert.equal(type.value,'盘点调整');
  for (const [movement,required,quantity] of [
    ['入库','ext.inventory.inbound_create','1'],
    ['出库','ext.inventory.outbound_create','1'],
    ['盘点调整','ext.inventory.stocktake_adjust',''],
  ]) {
    grants = new Set([required]);
    rows = [row('row0')];
    vm.runInContext(`selectMobileScan(${JSON.stringify(movement)})`,context);
    const [id,selected] = scans.at(-1);
    assert.equal(selected,movement);
    Object.assign(context,{barcodeTargetId:id,barcodeMovementType:selected,barcodeScanActive:true});
    await vm.runInContext('applyScannedBarcode("123456789012")',context);
    assert.equal(rows[0].querySelector('.op-qty').value,quantity);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
