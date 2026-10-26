// Preserve incoming links from P1 and older portfolio bookmarks.
// This page does not evaluate, fetch data, or execute the project.
(() => {
  function resolveOldAnchor() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    if (id && !document.getElementById(id)) {
      location.replace('report.html#' + encodeURIComponent(id));
    }
  }
  window.addEventListener('hashchange', resolveOldAnchor);
  resolveOldAnchor();
})();
