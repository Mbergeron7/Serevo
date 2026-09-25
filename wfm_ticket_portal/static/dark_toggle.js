/* ── Shared dark-mode toggle ─────────────────────────────────────────
   One copy for every page. Persists the preference in localStorage and
   updates the floating moon/sun button. Include at end of <body>:
     <script src="/static/dark_toggle.js"></script>
   (Each page keeps its own <button id="dark-toggle"> markup.)          */
(function () {
  var d = localStorage.getItem('wfm_dark') === '1';
  if (d) {
    document.body.classList.add('dark');
    document.documentElement.classList.add('dark');
  }
  var b = document.getElementById('dark-toggle');
  if (b) b.innerHTML = d ? '\u2600' : '\uD83C\uDF19';
})();
function toggleDark() {
  var d = document.body.classList.toggle('dark');
  document.documentElement.classList.toggle('dark', d);
  localStorage.setItem('wfm_dark', d ? '1' : '0');
  var b = document.getElementById('dark-toggle');
  if (b) b.innerHTML = d ? '\u2600' : '\uD83C\uDF19';
}

/* ── Interval cleanup on page navigation ──────────────────────────────
   Any page that sets window._wfmIntervals will have them cleared when
   navigating away, preventing timer accumulation across SPA-style nav.
   Usage: window._wfmIntervals = [setInterval(fn, ms), ...];          */
window._wfmIntervals = window._wfmIntervals || [];
window.wfmSetInterval = function(fn, ms) {
  var id = setInterval(fn, ms);
  window._wfmIntervals.push(id);
  return id;
};
document.addEventListener('visibilitychange', function() {
  if (document.hidden) {
    (window._wfmIntervals || []).forEach(clearInterval);
    window._wfmIntervals = [];
  }
});
