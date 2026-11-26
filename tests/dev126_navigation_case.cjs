const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const shell = fs.readFileSync(process.argv[2], 'utf8');
const html = fs.readFileSync(process.argv[3], 'utf8');
const calls = [];
const events = {};
const loc = new URL('http://store.local/?page=settings');
const nodes = new Map();
const $ = id => {
  if (!nodes.has(id)) nodes.set(id, {children:[],classList:{toggle(){}},textContent:''});
  return nodes.get(id);
};
const context = {
  document: {addEventListener(){}, querySelectorAll(){return [];}, querySelector(){return null;}},
  window: {location:loc,matchMedia:()=>({}),addEventListener:(type,fn)=>events[type]=fn},
  history: {replaceState(_state,_name,target){loc.href=target.href;}},
  location:loc, URL, URLSearchParams, $,
  currentUser:{role:'staff',permissions:{'ext.inventory.outbound_create':true,'ext.catalog.read':true}},
  toggleMobileMenu(){},
  hasPermission(key){return Boolean(context.currentUser.permissions[key]);},
  resetOp(){},
  connectWS(){},
  startDeviceHeartbeat(){calls.push('heartbeat');},
  refreshNotificationCount(){calls.push('notifications');},
  loadAllProducts(){calls.push('operation catalog');},
  loadSummary(){calls.push('dashboard');},
  loadPurchase(){calls.push('purchase');},
  loadSettings(){calls.push('settings');},
  loadProducts(){calls.push('products');},
  loadControlCenter(){calls.push('control');},
  toast(message){throw Error(message);},
};
vm.createContext(context);
vm.runInContext(shell,context);
context.StoreInventoryShell=context.window.StoreInventoryShell;
vm.runInContext(html.slice(html.indexOf('const navigationOrder'),html.indexOf('document.querySelectorAll("[data-page]").forEach(button => {\n    button.addEventListener'))+
  html.slice(html.indexOf('async function startApplication()'),html.indexOf('// 商品数据和选项')),context);
(async()=>{
  assert.equal(context.StoreInventoryShell.canOpenPage('constructor', context.currentUser), false);
  assert.equal(context.StoreInventoryShell.canOpenPage('settings', context.currentUser), false);
  assert.equal(context.StoreInventoryShell.canOpenPage('schedule', context.currentUser), false);
  await vm.runInContext('startApplication()', context);
  assert.equal(loc.search,'?page=operation');
  assert.deepEqual(calls,['operation catalog','heartbeat']);
  vm.runInContext('go("settings")', context);
  assert.deepEqual(calls,['operation catalog','heartbeat']);
})().catch(error=>{console.error(error);process.exitCode=1;});
