/* StoreInventory Permission SDK v1 — UI hints only; server API remains authoritative.
 * New pages: await StorePermissions.refresh(); StorePermissions.can('ext.members.view')
 * Multi-action checks: await StorePermissions.check(['ext.members.create'], 'all')
 * All calls use the current same-origin session; other users may never be queried.
 */
(function (root) {
  'use strict';
  let snapshot = null;
  const fetchJSON = async (url, options) => {
    const response = await root.fetch(url, {
      credentials: 'same-origin', cache: 'no-store', ...options
    });
    if (!response.ok) throw new Error('权限接口请求失败（HTTP ' + response.status + '）');
    return response.json();
  };
  const api = {
    async catalog() { return fetchJSON('/api/permissions/catalog'); },
    async refresh() {
      snapshot = await fetchJSON('/api/permissions/me');
      return snapshot;
    },
    clear() { snapshot = null; },
    can(key) {
      // Unknown keys are false even for admin; never trust cached UI grants for writes.
      return !!snapshot?.permissions?.[key];
    },
    canAll(keys) { return Array.isArray(keys) && keys.length > 0 && keys.every(key => api.can(key)); },
    canAny(keys) { return Array.isArray(keys) && keys.length > 0 && keys.some(key => api.can(key)); },
    async check(keys, mode = 'all') {
      if (!Array.isArray(keys) || !keys.length || !['all', 'any'].includes(mode)) {
        throw new Error('无效的权限校验参数');
      }
      return fetchJSON('/api/permissions/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys, mode })
      });
    }
  };
  Object.freeze(api);
  root.StorePermissions = api;
  root.addEventListener('pageshow', event => { if (event.persisted) snapshot = null; });
})(window);
