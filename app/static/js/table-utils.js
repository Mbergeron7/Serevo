/**
 * table-utils.js — Reusable sorting & filtering for data tables
 * =============================================================
 *
 * SORTING:
 *   Add class="data-table sortable" to a <table>.
 *   Each <th> becomes clickable. Clicking toggles asc/desc.
 *   Add data-sort="none" to a <th> to disable sorting on that column.
 *   Add data-sort="num" for numeric, data-sort="date" for date columns.
 *
 * FILTERING:
 *   Call TableFilter.init(tableId, inputId, columnIndex) to wire a text
 *   input to filter a specific column (or all columns if index is -1).
 *
 * DATE RANGE:
 *   Call TableFilter.dateRange(tableId, startId, endId, columnIndex)
 *   to wire date inputs to filter by a date column.
 */

(function () {
  'use strict';

  // ── Sortable Tables ───────────────────────────────────────
  function initSortable(table) {
    const headers = table.querySelectorAll('thead th');
    headers.forEach((th, idx) => {
      if (th.dataset.sort === 'none') return;
      th.style.cursor = 'pointer';
      th.style.userSelect = 'none';
      th.style.position = 'relative';
      // Add sort indicator
      const indicator = document.createElement('span');
      indicator.className = 'sort-indicator';
      indicator.textContent = ' ↕'; // ↕
      indicator.style.opacity = '0.3';
      indicator.style.fontSize = '0.75em';
      th.appendChild(indicator);

      th.addEventListener('click', () => sortTable(table, idx, th));
    });
  }

  function sortTable(table, colIdx, th) {
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll('tr'));
    if (rows.length < 2) return;

    // Determine sort direction
    const currentDir = th.dataset.sortDir || '';
    const newDir = currentDir === 'asc' ? 'desc' : 'asc';

    // Clear all indicators
    table.querySelectorAll('thead th').forEach(h => {
      h.dataset.sortDir = '';
      const ind = h.querySelector('.sort-indicator');
      if (ind) { ind.textContent = ' ↕'; ind.style.opacity = '0.3'; }
    });

    th.dataset.sortDir = newDir;
    const ind = th.querySelector('.sort-indicator');
    if (ind) {
      ind.textContent = newDir === 'asc' ? ' ↑' : ' ↓'; // ↑ ↓
      ind.style.opacity = '0.7';
    }

    const sortType = th.dataset.sort || 'auto';

    rows.sort((a, b) => {
      const cellA = a.children[colIdx];
      const cellB = b.children[colIdx];
      if (!cellA || !cellB) return 0;
      let valA = (cellA.dataset.sortValue !== undefined ? cellA.dataset.sortValue : cellA.textContent).trim();
      let valB = (cellB.dataset.sortValue !== undefined ? cellB.dataset.sortValue : cellB.textContent).trim();

      let cmp = 0;
      if (sortType === 'num' || (sortType === 'auto' && isNumericPair(valA, valB))) {
        cmp = parseNum(valA) - parseNum(valB);
      } else if (sortType === 'date' || (sortType === 'auto' && isDatePair(valA, valB))) {
        cmp = parseDate(valA) - parseDate(valB);
      } else {
        cmp = valA.localeCompare(valB, undefined, { sensitivity: 'base', numeric: true });
      }
      return newDir === 'asc' ? cmp : -cmp;
    });

    rows.forEach(r => tbody.appendChild(r));
  }

  function parseNum(s) {
    const n = parseFloat(s.replace(/[^0-9.\-]/g, ''));
    return isNaN(n) ? -Infinity : n;
  }
  function parseDate(s) {
    const d = new Date(s);
    return isNaN(d.getTime()) ? 0 : d.getTime();
  }
  function isNumericPair(a, b) {
    return /^[\d,.$%\-+]+$/.test(a) && /^[\d,.$%\-+]+$/.test(b);
  }
  function isDatePair(a, b) {
    return !isNaN(new Date(a).getTime()) && !isNaN(new Date(b).getTime()) && a.length > 4 && b.length > 4;
  }


  // ── Text Filter ───────────────────────────────────────────
  const TableFilter = {
    init(tableId, inputId, colIdx) {
      const input = document.getElementById(inputId);
      const table = document.getElementById(tableId);
      if (!input || !table) return;
      input.addEventListener('input', () => {
        const q = input.value.toLowerCase().trim();
        const rows = table.querySelectorAll('tbody tr');
        rows.forEach(row => {
          if (!q) { row.style.display = ''; return; }
          if (colIdx === -1) {
            row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
          } else {
            const cell = row.children[colIdx];
            row.style.display = cell && cell.textContent.toLowerCase().includes(q) ? '' : 'none';
          }
        });
      });
    },

    dropdown(tableId, selectId, colIdx) {
      const sel = document.getElementById(selectId);
      const table = document.getElementById(tableId);
      if (!sel || !table) return;
      sel.addEventListener('change', () => {
        const v = sel.value.toLowerCase();
        const rows = table.querySelectorAll('tbody tr');
        rows.forEach(row => {
          if (!v) { row.style.display = ''; return; }
          const cell = row.children[colIdx];
          row.style.display = cell && cell.textContent.toLowerCase().trim() === v ? '' : 'none';
        });
      });
    },

    dateRange(tableId, startId, endId, colIdx) {
      const startIn = document.getElementById(startId);
      const endIn = document.getElementById(endId);
      const table = document.getElementById(tableId);
      if (!startIn || !endIn || !table) return;
      const apply = () => {
        const s = startIn.value;
        const e = endIn.value;
        const rows = table.querySelectorAll('tbody tr');
        rows.forEach(row => {
          const cell = row.children[colIdx];
          if (!cell) return;
          const val = cell.textContent.trim().substring(0, 10); // YYYY-MM-DD
          let show = true;
          if (s && val < s) show = false;
          if (e && val > e) show = false;
          row.style.display = show ? '' : 'none';
        });
      };
      startIn.addEventListener('change', apply);
      endIn.addEventListener('change', apply);
    }
  };

  // ── Auto-init ─────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('table.sortable').forEach(initSortable);
  });

  // ── Expose to global scope ────────────────────────────────
  window.TableFilter = TableFilter;
  window.initSortable = initSortable;

})();
