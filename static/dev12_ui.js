/* V1.6.0-dev.12 viewport and layout helpers. No business/API mutations. */
(function () {
  'use strict';

  const root = document.documentElement;
  const body = document.body;
  let resizeFrame = 0;
  let layoutFrame = 0;

  function syncViewport() {
    const viewport = window.visualViewport;
    const height = viewport && Number.isFinite(viewport.height)
      ? viewport.height : window.innerHeight;
    const width = viewport && Number.isFinite(viewport.width)
      ? viewport.width : window.innerWidth;
    root.style.setProperty('--dev12-viewport-height', `${Math.max(240, Math.round(height))}px`);
    root.style.setProperty('--dev12-viewport-width', `${Math.max(240, Math.round(width))}px`);
    body.dataset.dev12Viewport = width <= 640 ? 'mobile' : (width <= 1100 ? 'tablet' : 'desktop');
  }

  function scheduleViewportSync() {
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = 0;
      syncViewport();
      refreshLayout();
    });
  }

  function markTableRegions(scope) {
    const base = scope && scope.querySelectorAll ? scope : document;
    base.querySelectorAll('.table-wrap, .table-scroll, .table-container, .table-responsive, .timeline-wrap')
      .forEach((region) => {
        region.dataset.dev12ScrollRegion = 'true';
        const table = region.querySelector('table');
        if (table) {
          if (!region.classList.contains('app-table-scroll')) {
            region.classList.add('app-table-scroll');
          }
          const overflow = table.scrollWidth > region.clientWidth + 2;
          region.dataset.dev12Overflow = overflow ? 'true' : 'false';
        } else {
          delete region.dataset.dev12Overflow;
        }
      });
  }

  function markDialogs() {
    document.querySelectorAll('.modal-box, .modal-back > .modal, .modal').forEach((dialog) => {
      if (dialog.closest('.modal, .modal-back') || dialog.classList.contains('modal-box')) {
        dialog.dataset.dev12Dialog = 'true';
      }
    });
  }

  function refreshLayout() {
    markTableRegions(document);
    markDialogs();
  }

  function scheduleLayout() {
    if (layoutFrame) return;
    layoutFrame = requestAnimationFrame(() => {
      layoutFrame = 0;
      refreshLayout();
    });
  }

  function boot() {
    body.dataset.dev12Ui = 'true';
    syncViewport();
    refreshLayout();

    if ('MutationObserver' in window) {
      const observer = new MutationObserver((records) => {
        let needsRefresh = false;
        for (const record of records) {
          if (record.type === 'childList' && (record.addedNodes.length || record.removedNodes.length)) {
            needsRefresh = true;
            break;
          }
          if (record.type === 'attributes' && ['class', 'style', 'hidden'].includes(record.attributeName)) {
            needsRefresh = true;
            break;
          }
        }
        if (needsRefresh) scheduleLayout();
      });
      observer.observe(document.body, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ['class', 'style', 'hidden']
      });
    }

    window.addEventListener('resize', scheduleViewportSync, { passive: true });
    window.addEventListener('orientationchange', scheduleViewportSync, { passive: true });
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', scheduleViewportSync, { passive: true });
      window.visualViewport.addEventListener('scroll', scheduleViewportSync, { passive: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }

  window.StoreInventoryDev12 = Object.freeze({
    refreshLayout,
    syncViewport
  });
})();
