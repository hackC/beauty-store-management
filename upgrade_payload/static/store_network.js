/* Shared LAN transport for inventory and schedule. */
(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window);
  const requestTimeout = 18000;
  const probeTimeout = 4500;
  const probeInterval = 15000;
  let epoch = 0;
  let evidence = 0;
  let lastSuccess = 0;
  let lastError = '';
  let probeFailures = 0;
  let state = 'checking';
  let timer;
  let probeController;
  let cancelProbe;
  let activeProbe = false;
  let manualCheck = false;
  let manualAttempt = 0;
  let manualMessage = '';
  let manualMessageTimer;

  function reportManual(message, busy) {
    if (manualMessageTimer !== undefined) clearTimeout(manualMessageTimer);
    manualMessageTimer = undefined;
    manualMessage = message;
    window.dispatchEvent(new CustomEvent('store:manual-network-check', {detail: {busy, message}}));
    if (!busy && state === 'online') {
      manualMessageTimer = setTimeout(() => {
        manualMessageTimer = undefined;
        manualMessage = '';
        window.dispatchEvent(new CustomEvent('store:manual-network-check', {detail: {busy: false, message: ''}}));
      }, 1800);
    }
  }

  function publish(next) {
    if (state === next) return;
    const previous = state;
    state = next;
    window.dispatchEvent(new CustomEvent('store:network-state', {detail: {state, previous}}));
    if (next === 'online' && ['degraded','unreachable'].includes(previous)) {
      window.dispatchEvent(new CustomEvent('store:server-reconnected'));
    }
  }

  function reachable() {
    evidence++;
    lastSuccess = Date.now();
    probeFailures = 0;
    lastError = '';
    publish('online');
  }

  function schedule(ms) {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    if (!document.hidden) timer = setTimeout(() => {timer = undefined; void probe();}, ms);
  }

  async function probe() {
    if (activeProbe || document.hidden) return;
    const probeEpoch = epoch;
    const requestedByUser = manualCheck;
    const priorEvidence = evidence;
    const controller = new AbortController();
    probeController = controller;
    activeProbe = true;
    const startedAt = Date.now();
    let timeout;
    const deadline = new Promise((_, reject) => {
      cancelProbe = () => reject(new Error('Health check superseded'));
      timeout = setTimeout(() => {
        reject(new Error('Health request timed out'));
        controller.abort();
      }, probeTimeout);
    });
    try {
      const path = requestedByUser
        ? '/api/health/live?check=' + Date.now() + '-' + (++manualAttempt)
        : '/api/health/live';
      const validation = async () => {
        const response = await nativeFetch(path, {
          cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
        });
        if (!response.ok || (await response.json()).ok !== true) {
          throw new Error('Health response HTTP ' + response.status);
        }
      };
      await Promise.race([validation(), deadline]);
      if (probeEpoch === epoch) {
        reachable();
        if (requestedByUser) {
          manualCheck = false;
          reportManual('已连接门店服务器', false);
          window.dispatchEvent(new CustomEvent('store:manual-check-online'));
        }
      }
    } catch (error) {
      const timerWasSuspended = Date.now() - startedAt > probeTimeout + 1500;
      if (probeEpoch === epoch && !document.hidden) {
        if (priorEvidence === evidence && (!timerWasSuspended || requestedByUser)) {
          lastError = String(error);
          probeFailures++;
          publish(probeFailures >= 3 ? 'unreachable' : 'degraded');
        }
        if (requestedByUser) {
          manualCheck = false;
          const working = priorEvidence !== evidence;
          reportManual(working ? '已连接门店服务器' : '本次未连上，请确认门店 Wi-Fi 和服务器地址。', false);
          if (working) window.dispatchEvent(new CustomEvent('store:manual-check-online'));
        }
      }
    } finally {
      clearTimeout(timeout);
      activeProbe = false;
      probeController = undefined;
      cancelProbe = undefined;
      schedule(probeEpoch !== epoch ? 0 : probeFailures ? Math.min(12000, 1500 * 2 ** (probeFailures - 1)) : probeInterval);
    }
  }

  function wake() {
    epoch++;
    cancelProbe?.();
    probeController?.abort();
    if (!activeProbe) schedule(0);
  }

  function checkNow() {
    if (manualCheck) return;
    manualCheck = true;
    reportManual('正在重新检测门店服务器…', true);
    wake();
  }

  async function attempt(input, init, method, external) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (external?.aborted) abort();
    else external?.addEventListener('abort', abort, {once:true});
    const timer = setTimeout(abort, requestTimeout);
    try {
      const response = await nativeFetch(input, {...init, method, signal: controller.signal});
      // Authentication failures, validation errors and DB errors still prove HTTP reachability.
      reachable();
      return response;
    } finally {
      clearTimeout(timer);
      external?.removeEventListener('abort', abort);
    }
  }

  window.fetch = async function(input, init = {}) {
    const url = new URL(typeof input === 'string' ? input : input.url || String(input), location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return nativeFetch(input, init);
    const method = (init.method || input.method || 'GET').toUpperCase();
    const read = method === 'GET' || method === 'HEAD';
    const external = init.signal || input.signal;
    for (let i = 0; ; i++) {
      try {
        return await attempt(input, init, method, external);
      } catch (error) {
        if (external?.aborted) throw error;
        lastError = String(error);
        if (read && i === 0 && !document.hidden) {
          if (!activeProbe) schedule(0);
          await new Promise(resolve => setTimeout(resolve, 350));
          continue;
        }
        if (!activeProbe) schedule(0);
        const failure = new Error(read
          ? '读取暂时未完成，请重试。'
          : '未收到服务器的保存确认。请核对结果后再提交。');
        failure.name = 'StoreNetworkError';
        throw failure;
      }
    }
  };

  function createSocket(onMessage, onStatus = () => {}) {
    let socket, retry, stopped = false, failures = 0, opened = false;
    const reconnect = () => {
      if (stopped || document.hidden || socket || retry !== undefined || state === 'unreachable') return;
      const connection = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
      socket = connection;
      connection.onopen = () => {
        failures = 0;
        onStatus(true, opened);
        opened = true;
      };
      connection.onmessage = onMessage;
      connection.onclose = event => {
        if (socket !== connection) return;
        socket = undefined;
        onStatus(false);
        if (!stopped && !document.hidden && ![4401,4403].includes(event.code)) {
          retry = setTimeout(() => {retry = undefined; reconnect();}, Math.min(15000, 1000 * 2 ** Math.min(failures++, 4)));
        }
      };
    };
    const pause = () => {
      if (retry !== undefined) clearTimeout(retry);
      retry = undefined;
      const old = socket;
      socket = undefined;
      old?.close();
    };
    const visibility = () => document.hidden ? pause() : reconnect();
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('store:server-reconnected', reconnect);
    reconnect();
    return {close() {
      stopped = true;
      pause();
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('store:server-reconnected', reconnect);
    }};
  }

  function mountStatus() {
    const banner = document.createElement('div');
    banner.id = 'storeNetworkStatus';
    banner.setAttribute('role', 'status');
    banner.hidden = true;
    const label = document.createElement('span');
    const check = document.createElement('button');
    check.type = 'button';
    check.textContent = '重新检测';
    check.onclick = checkNow;
    banner.append(label, check);
    document.body.appendChild(banner);
    const update = () => {
      banner.hidden = state !== 'unreachable' && !manualMessage;
      banner.dataset.networkState = state;
      label.textContent = manualMessage || '暂时无法访问门店服务器，正在自动重连。';
      check.disabled = manualCheck;
      check.textContent = manualCheck ? '检测中…' : '重新检测';
      for (const id of ['serverStatus', 'scheduleServerStatus']) {
        const indicator = document.getElementById(id);
        if (indicator) indicator.textContent = state === 'online' ? '服务器连接正常' :
          state === 'unreachable' ? '正在自动重连' : '正在确认连接';
      }
    };
    window.addEventListener('store:network-state', () => {
      if (!manualCheck) manualMessage = '';
      update();
    });
    window.addEventListener('store:manual-network-check', update);
    update();
  }

  window.StoreNetwork = Object.freeze({createSocket, check: checkNow});
  window.__storeNetworkDiagnostics = () => ({
    page: location.pathname + location.search, browserOnline: navigator.onLine,
    serverState: state, consecutiveHealthFailures: probeFailures, lastError,
    lastSuccess: lastSuccess ? new Date(lastSuccess).toISOString() : null,
  });
  window.addEventListener('online', wake);
  window.addEventListener('offline', wake);
  window.addEventListener('pageshow', wake);
  document.addEventListener('visibilitychange', wake);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountStatus, {once:true});
  else mountStatus();
  schedule(0);
})();
