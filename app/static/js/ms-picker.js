/**
 * Multi-select picker component for LOB / Planning Unit filters.
 *
 * Usage:
 *   <div class="ms-picker" id="myPicker" data-placeholder="All LOBs">
 *     <div class="ms-picker-btn" onclick="MSPicker.toggle('myPicker')">
 *       <span class="ms-label">All LOBs</span>
 *     </div>
 *     <div class="ms-dropdown">
 *       <input class="ms-search" placeholder="Search…"
 *              oninput="MSPicker.filter('myPicker', this.value)">
 *       <div class="ms-actions">
 *         <a onclick="MSPicker.all('myPicker', true)">Select All</a>
 *         <a onclick="MSPicker.all('myPicker', false)">Clear All</a>
 *       </div>
 *       <div class="ms-items"></div>
 *     </div>
 *   </div>
 *
 *   MSPicker.init('myPicker', ['LOB A', 'LOB B'], onChangeFn);
 *   // or server-side: items already rendered, just bind:
 *   MSPicker.bind('myPicker', onChangeFn);
 */
const MSPicker = (() => {
  const cbs = {};       // pickerId -> onChange callback

  /** Populate items from an array of strings (clears existing) */
  function init(id, items, onChange, allChecked) {
    const el = document.getElementById(id);
    if (!el) return;
    const box = el.querySelector('.ms-items');
    box.innerHTML = '';
    items.forEach(val => {
      const lbl = document.createElement('label');
      lbl.innerHTML = `<input type="checkbox" value="${val}" ${allChecked !== false ? 'checked' : ''}> ${val}`;
      lbl.querySelector('input').addEventListener('change', () => _update(id));
      box.appendChild(lbl);
    });
    cbs[id] = onChange || null;
    _update(id);
  }

  /** Bind change events for server-rendered checkboxes */
  function bind(id, onChange) {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelectorAll('.ms-items input[type=checkbox]').forEach(cb => {
      cb.addEventListener('change', () => _update(id));
    });
    cbs[id] = onChange || null;
    _update(id);
  }

  function toggle(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const dd = el.querySelector('.ms-dropdown');
    const wasOpen = dd.classList.contains('open');
    // Close all other open pickers first
    document.querySelectorAll('.ms-dropdown.open').forEach(d => {
      d.classList.remove('open');
      d.style.position = '';
      d.style.top = '';
      d.style.left = '';
    });
    if (wasOpen) return;
    // Check if inside an overflow-clipping ancestor
    const btn = el.querySelector('.ms-picker-btn');
    const rect = btn.getBoundingClientRect();
    dd.style.position = 'fixed';
    dd.style.top = (rect.bottom + 2) + 'px';
    dd.style.left = rect.left + 'px';
    dd.classList.add('open');
  }

  function filter(id, q) {
    const el = document.getElementById(id);
    if (!el) return;
    q = q.toLowerCase();
    el.querySelectorAll('.ms-items label').forEach(l => {
      l.style.display = l.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  }

  function all(id, checked) {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelectorAll('.ms-items input[type=checkbox]').forEach(cb => {
      if (cb.closest('label').style.display !== 'none') cb.checked = checked;
    });
    _update(id);
  }

  /** Return array of selected values */
  function selected(id) {
    const el = document.getElementById(id);
    if (!el) return [];
    return [...el.querySelectorAll('.ms-items input:checked')].map(cb => cb.value);
  }

  /** Update label text and fire callback */
  function _update(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const all_cbs = el.querySelectorAll('.ms-items input[type=checkbox]');
    const checked = [...all_cbs].filter(cb => cb.checked);
    const lbl = el.querySelector('.ms-label');
    const placeholder = el.dataset.placeholder || 'All';
    if (checked.length === all_cbs.length || checked.length === 0) {
      lbl.textContent = checked.length === 0 ? 'None selected' : placeholder;
    } else if (checked.length <= 2) {
      lbl.textContent = checked.map(cb => cb.value).join(', ');
    } else {
      lbl.textContent = `${checked.length} of ${all_cbs.length} selected`;
    }
    if (cbs[id]) cbs[id](checked.map(cb => cb.value));
  }

  // Close any open picker when clicking outside
  document.addEventListener('click', e => {
    if (!e.target.closest('.ms-picker') && !e.target.closest('.ms-dropdown')) {
      document.querySelectorAll('.ms-dropdown.open').forEach(d => {
        d.classList.remove('open');
        d.style.position = '';
        d.style.top = '';
        d.style.left = '';
      });
    }
  });

  return { init, bind, toggle, filter, all, selected };
})();
