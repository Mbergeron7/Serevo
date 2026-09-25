/* picker_fit.js — keeps multi-select dropdown footers (All/None/Apply)
   reachable on every page. Whenever a .lob-dropdown opens, its max-height
   is capped to the space between it and the bottom of the viewport, so the
   list scrolls internally and the footer is always visible on screen.
   No re-parenting, no changes to any page's own toggle/click-away logic. */
(function () {
  function fitOpenDropdowns() {
    document.querySelectorAll('.lob-dropdown.open').forEach(function (dd) {
      var r = dd.getBoundingClientRect();
      var space = window.innerHeight - r.top - 12;
      dd.style.maxHeight = Math.max(160, Math.min(340, space)) + 'px';
    });
  }
  // capture-phase so it runs after each page's own toggle handler flips
  // the .open class; rAF waits for layout before measuring
  document.addEventListener('click', function () {
    requestAnimationFrame(fitOpenDropdowns);
  }, true);
  window.addEventListener('resize', fitOpenDropdowns);
})();
