/**
 * filter_persist.js
 * Saves and restores filter state (selects, inputs, checkboxes) to localStorage.
 * Keyed by page path so each page has independent state.
 * On pages with server-side GET filters: submits the form on load if saved state differs.
 * On pages with client-side JS filters: restores values and fires the filter function.
 */
(function () {
  var PAGE_KEY = 'fp_' + location.pathname.replace(/\//g, '_');

  // ── Elements we care about ──────────────────────────────────────────────
  function getFilterEls() {
    // Named form inputs + any element with data-fp attribute
    var els = [];
    document.querySelectorAll(
      'select[name], input[type="text"][name], input[type="search"][name],' +
      'input[type="date"][name], input[type="checkbox"][name], input[type="checkbox"][data-fp],' +
      'input[type="checkbox"].pu-cb, input[type="checkbox"].st-cb,' +
      'input[type="checkbox"].u5-cb, input[type="checkbox"].snap-section-item input'
    ).forEach(function(el) { els.push(el); });
    // Also grab named selects inside filter bars
    return els;
  }

  // ── Save current state ──────────────────────────────────────────────────
  function save() {
    var state = {};
    getFilterEls().forEach(function(el) {
      var key = el.getAttribute('data-fp') || el.name || el.id;
      if (!key) return;
      if (el.type === 'checkbox') {
        if (!state[key]) state[key] = [];
        if (el.checked) state[key].push(el.value || '1');
      } else {
        state[key] = el.value;
      }
    });
    // Also save the current URL search params (for server-side filters)
    state['__url__'] = location.search;
    try { localStorage.setItem(PAGE_KEY, JSON.stringify(state)); } catch(e) {}
  }

  // ── Restore saved state ─────────────────────────────────────────────────
  function restore() {
    var raw;
    try { raw = localStorage.getItem(PAGE_KEY); } catch(e) {}
    if (!raw) return false;
    var state;
    try { state = JSON.parse(raw); } catch(e) { return false; }

    // If URL already has params (user navigated directly), save those instead
    if (location.search && location.search !== state['__url__']) {
      save();
      return false;
    }

    // Restore form fields
    var changed = false;
    getFilterEls().forEach(function(el) {
      var key = el.getAttribute('data-fp') || el.name || el.id;
      if (!key || !(key in state)) return;
      if (el.type === 'checkbox') {
        var vals = state[key] || [];
        var should = vals.indexOf(el.value || '1') > -1;
        if (el.checked !== should) { el.checked = should; changed = true; }
      } else {
        if (el.value !== state[key]) { el.value = state[key]; changed = true; }
      }
    });

    // If page has server-side GET forms and state has a URL, redirect to it
    var savedUrl = state['__url__'];
    if (savedUrl && savedUrl !== location.search &&
        document.querySelector('form[method="GET"]')) {
      location.search = savedUrl;
      return true;  // navigating, stop here
    }

    return changed;
  }

  // ── On load ─────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function() {
    var restored = restore();

    // Fire client-side filter functions if they exist
    if (restored) {
      if (typeof filterDir    === 'function') filterDir();
      if (typeof applyU5Filter === 'function') applyU5Filter();
      if (typeof applySnap    === 'function') {/* don't auto-apply snap */}
    }

    // Save on any change
    document.addEventListener('change', function(e) {
      var el = e.target;
      if (el.tagName === 'SELECT' || el.tagName === 'INPUT') save();
    });
    document.addEventListener('input', function(e) {
      var el = e.target;
      if (el.tagName === 'INPUT') save();
    });

    // Save before page unloads / refresh
    window.addEventListener('beforeunload', save);
    // Also save whenever a form is submitted
    document.querySelectorAll('form[method="GET"]').forEach(function(form) {
      form.addEventListener('submit', save);
    });
  });

  // ── Clear saved state for this page ─────────────────────────────────────
  window.fpClear = function() {
    try { localStorage.removeItem(PAGE_KEY); } catch(e) {}
  };

})();
