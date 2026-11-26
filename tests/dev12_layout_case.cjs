const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const callbacks = [];
let mutate;
let observing = false;
let classWrites = 0;
const regions = [];
function region() {
  const classes = new Set();
  return {
    dataset: {}, clientWidth: 390,
    querySelector: () => ({scrollWidth: 500}),
    classList: {
      contains: name => classes.has(name),
      add(name) {
        ++classWrites;
        assert.ok(classWrites <= 2, 'layout rewrote a class it already added');
        classes.add(name);
        if (observing) mutate([{type: 'attributes', attributeName: 'class'}]);
      }
    }
  };
}
const context = {
  document: {
    documentElement: {style: {setProperty() {}}},
    body: {dataset: {}}, readyState: 'complete',
    querySelectorAll: names => names.startsWith('.table-wrap') ? regions : []
  },
  window: {innerWidth: 390, innerHeight: 844, addEventListener() {}},
  requestAnimationFrame(fn) {return callbacks.push(fn);},
  cancelAnimationFrame() {},
  MutationObserver: class {
    constructor(callback) { mutate = callback; }
    observe() { observing = true; }
  }
};
context.window.MutationObserver = context.MutationObserver;
regions.push(region());
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
regions.push(region());
mutate([{type:'childList', addedNodes:[{}], removedNodes:[]}]);
let frames = 0;
while (callbacks.length) {
  assert.ok(++frames <= 3, 'observer keeps the event loop busy');
  callbacks.shift()();
}
assert.equal(classWrites, 2);
assert.ok(regions.every(item => item.dataset.dev12Overflow === 'true'));
assert.equal(context.document.body.dataset.dev12Viewport, 'mobile');
