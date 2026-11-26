const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(process.argv[2], 'utf8');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
};
const tick = () => new Promise(setImmediate);

function harness({ignoreAbort = false} = {}) {
  const events = [];
  const calls = [];
  const timers = new Map();
  const sockets = [];
  let now = 100;
  let timerId = 0;
  const window = new EventTarget();
  const document = new EventTarget();
  document.readyState = 'loading';
  document.hidden = false;
  window.fetch = (url, init) => {
    const request = deferred();
    calls.push({url, method: init?.method, ...request});
    if (!ignoreAbort) init?.signal?.addEventListener('abort', () => request.reject(new Error('abort')), {once: true});
    return request.promise;
  };
  window.addEventListener('store:server-reconnected', () => events.push('reconnected'));
  const context = {
    window, document, location:new URL('http://store.local/?page=purchase'), navigator:{onLine:true},
    URL, AbortController, CustomEvent, Date:class extends Date {static now() {return now;}},
    setTimeout(fn, delay) {const id=++timerId;timers.set(id,{fn,due:now+delay});return id;},
    clearTimeout(id) {timers.delete(id);},
    WebSocket:class {constructor() {sockets.push(this);} close() {this.onclose?.({code:1000});}},
  };
  vm.runInNewContext(source, context);
  return {
    window, document, events, calls, sockets, info:window.__storeNetworkDiagnostics,
    advanceClock(ms) {now += ms;},
    runNext() {
      assert.ok(timers.size);
      const [id, timer] = [...timers.entries()].sort((a,b)=>a[1].due-b[1].due)[0];
      timers.delete(id);now = Math.max(now, timer.due);timer.fn();
    },
  };
}

(async () => {
  const h = harness();
  const last = () => h.calls.at(-1);
  h.runNext();
  assert.equal(last().url, '/api/health/live');
  const oldProbe = last();
  const realApi = h.window.fetch('/api/stock');
  last().resolve({ok:false, status:503});
  await realApi;
  oldProbe.reject(new Error('idle LAN socket failed'));
  await tick();
  assert.equal(h.info().serverState, 'online');
  assert.equal(h.info().consecutiveHealthFailures, 0);

  h.runNext();
  h.document.hidden = true;
  h.document.dispatchEvent(new Event('visibilitychange'));
  await tick();
  assert.equal(h.info().consecutiveHealthFailures, 0);
  h.document.hidden = false;
  h.document.dispatchEvent(new Event('visibilitychange'));
  for (let i=0;i<3;i++) {
    h.runNext(); last().reject(new Error('Wi-Fi offline')); await tick();
  }
  assert.equal(h.info().serverState, 'unreachable');
  h.runNext(); last().resolve({ok:true, json:async()=>({ok:true})}); await tick();
  assert.equal(h.info().serverState, 'online');
  assert.deepEqual(h.events,['reconnected']);

  const write = h.window.fetch('/api/submit',{method:'POST', body:'{}'});
  last().reject(new Error('write acknowledgement lost'));
  await assert.rejects(write, /保存确认/);
  assert.equal(h.calls.filter(c=>c.method==='POST').length, 1);

  const retry = h.window.fetch('/api/purchase');
  last().reject(new Error('stale socket'));
  await tick();
  h.runNext();
  assert.equal(last().url, '/api/health/live');
  last().resolve({ok:true, json:async()=>({ok:true})}); await tick();
  h.runNext();
  await tick();
  assert.equal(last().url, '/api/purchase');
  last().resolve({ok:true}); await retry;
  assert.equal(h.calls.filter(c=>c.url==='/api/purchase').length, 2);

  const socket = h.window.StoreNetwork.createSocket(() => {});
  assert.equal(h.sockets.length, 1);
  h.sockets[0].onclose({code:1006});
  h.runNext();
  assert.equal(h.sockets.length, 2);
  h.document.hidden=true;
  h.document.dispatchEvent(new Event('visibilitychange'));
  h.document.hidden=false;
  h.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(h.sockets.length, 3);
  socket.close();

  // A timer that executes much later than scheduled after suspend is discarded.
  const paused = harness();
  paused.runNext(); paused.advanceClock(30000); paused.runNext();
  await tick();
  assert.equal(paused.info().consecutiveHealthFailures, 0);

  // The manual button should supersede a hung probe, even when fetch ignores abort.
  const manual = harness({ignoreAbort:true});
  const status = [];
  manual.window.addEventListener('store:manual-network-check', e => status.push(e.detail));
  manual.runNext();
  manual.window.StoreNetwork.check();
  manual.window.StoreNetwork.check();
  await tick();
  assert.deepEqual(status.map(event=>event.busy), [true]);
  manual.runNext();
  assert.match(manual.calls.at(-1).url, /^\/api\/health\/live\?check=/);
  manual.runNext();
  await tick();
  assert.match(status.at(-1).message, /本次未连上/);
  assert.equal(status.at(-1).busy, false);
  manual.window.StoreNetwork.check();
  manual.runNext();
  assert.notEqual(manual.calls.at(-1).url, manual.calls.at(-2).url);
  manual.calls.at(-1).resolve({ok:true, json:async()=>({ok:true})});
  await tick();
  assert.equal(manual.info().serverState, 'online');
  assert.match(status.at(-1).message, /已连接/);

})().catch(error => {console.error(error);process.exitCode=1;});
