/* V1.6.0-dev.8 shared shell behavior. No business rules or automatic write retries. */
(function () {
  'use strict';

  const mobileQuery = window.matchMedia('(max-width: 640px)');
  let liveRegion;

  const pagePermissions = {
    home: ['dashboard'],
    operation: ['operation', 'ext.inventory.inbound_create', 'ext.inventory.outbound_create', 'ext.inventory.stocktake_adjust'],
    products: ['products_view', 'products_manage', 'ext.catalog.read'],
    transactions: ['transactions_view', 'ext.transactions.read'],
    archive: ['transactions_view', 'ext.transactions.read'],
    purchase: ['purchase_view', 'ext.procurement.read'],
    schedule: ['ext.scheduling.read','ext.appointments.read','ext.appointments.read_self'],
    settings: ['settings_view','settings_manage'],
    data: ['backup_view','backup_manage','backup_restore'],
    help: ['dashboard'],
    controlcenter: ['products_view','ext.inventory.read','audit_view','devices_view','customers_view',
      'ext.customers.read','ext.waitlist.read','reports_view','ext.scheduling.workload_read_all','ext.scheduling.workload_read_self'],
  };
  function canOpenPage(page, user) {
    if (!user || user.must_set_password) return false;
    if (page === 'users') return user.role === 'admin';
    return Object.prototype.hasOwnProperty.call(pagePermissions, page) && pagePermissions[page].some(
      key => user.role === 'admin' || Boolean(user.permissions?.[key])
    );
  }

  function closeMobileDrawers() {
    if (typeof window.toggleMobileMenu === 'function') {
      window.toggleMobileMenu(false);
    }
    if (typeof window.toggleScheduleMobileMenu === 'function') {
      window.toggleScheduleMobileMenu(false);
    }
  }

  function setCurrentNavigation() {
    const isSchedule = new URLSearchParams(location.search).get('page') === 'schedule';
    const page = new URLSearchParams(window.location.search).get('page') || 'home';
    document.querySelectorAll('[data-store-page]').forEach((link) => {
      const active = isSchedule ? link.dataset.storePage === 'schedule' : link.dataset.storePage === page;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    document.querySelectorAll('.sidebar [data-page], .mobile-nav [data-page]').forEach((link) => {
      const active = !isSchedule && link.dataset.page === page;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }

  function markTableScrollRegions() {
    const selectors = [
      '.table-wrap', '.table-scroll', '.table-container', '.table-responsive',
      '.mobile-card-table-wrap', '.transactions-table-wrap'
    ];
    document.querySelectorAll(selectors.join(',')).forEach((region) => {
      if (region.querySelector('table thead')) region.classList.add('app-table-scroll');
    });
  }

  function ensureLiveRegion() {
    if (liveRegion || !document.body) return;
    liveRegion = document.createElement('div');
    liveRegion.className = 'app-shell-live-region';
    liveRegion.setAttribute('aria-live', 'polite');
    liveRegion.setAttribute('aria-atomic', 'true');
    liveRegion.dataset.appShellLiveRegion = 'true';
    document.body.appendChild(liveRegion);
  }

  function announce(message) {
    ensureLiveRegion();
    if (liveRegion) liveRegion.textContent = String(message || '');
  }

  function setBusy(element, busy, label) {
    if (!element) return;
    if (busy) {
      if (!element.hasAttribute('data-app-shell-original-label')) {
        element.setAttribute('data-app-shell-original-label', element.textContent || '');
      }
      element.setAttribute('aria-busy', 'true');
      if (label) element.dataset.appShellBusyLabel = label;
      element.disabled = true;
    } else {
      element.removeAttribute('aria-busy');
      element.disabled = false;
      const original = element.getAttribute('data-app-shell-original-label');
      if (original !== null) element.textContent = original;
      element.removeAttribute('data-app-shell-original-label');
      delete element.dataset.appShellBusyLabel;
    }
  }

  async function request(url, options) {
    const config = Object.assign({ credentials: 'same-origin' }, options || {});
    const response = await fetch(url, config);
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json')
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const message = payload && typeof payload === 'object' && payload.detail
        ? payload.detail : `请求失败（${response.status}）`;
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  document.addEventListener('DOMContentLoaded', function () {
    ensureLiveRegion();
    markTableScrollRegions();
    setCurrentNavigation();

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeMobileDrawers();
    });

    document.addEventListener('click', function (event) {
      if (!mobileQuery.matches) return;
      const navigationLink = event.target.closest(
        '.sidebar [data-page], .store-sidebar [data-store-page], .mobile-nav [data-page], .store-mobile-nav [data-store-page]'
      );
      if (navigationLink) closeMobileDrawers();
    });

    window.addEventListener('resize', function () {
      if (!mobileQuery.matches) closeMobileDrawers();
    }, { passive: true });

    window.addEventListener('orientationchange', markTableScrollRegions, { passive: true });
  });

  window.StoreInventoryShell = Object.freeze({
    request,
    canOpenPage,
    setCurrentNavigation,
    announce,
    setBusy,
    closeMobileDrawers,
    refreshLayoutMarkers: markTableScrollRegions
  });
})();
