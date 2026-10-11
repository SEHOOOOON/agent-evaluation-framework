// One disclosure level: fold long records, keep conclusions and scope visible.
(() => {
  const obsolete = document.getElementById('result-overview');
  if (obsolete) obsolete.remove();
  document.querySelectorAll('.portfolio-panel').forEach(panel => { panel.hidden = false; panel.removeAttribute('role'); panel.removeAttribute('aria-labelledby'); });
  document.querySelectorAll('.dashboard-tabs button').forEach(button => {
    const link = document.createElement('a');
    link.href = '#' + button.getAttribute('aria-controls');
    link.textContent = button.textContent;
    button.replaceWith(link);
  });
  const nav = document.querySelector('.dashboard-tabs');
  nav?.removeAttribute('role');
  document.querySelectorAll('.criteria-link, #show-assets').forEach(button => {
    const link = document.createElement('a');
    link.href = button.id === 'show-assets' ? '#assets-details' : '#group-' + button.dataset.role;
    link.textContent = button.textContent;
    link.className = button.className;
    button.replaceWith(link);
  });
  document.querySelectorAll('.controls, .disclosure-label').forEach(node => node.remove());
  const foldIds = new Set(['scenario-role-scores', 'power-result-details', 'pause-reuse-details',
    'comparison-disclosure', 'assets-details', 'latest-local-checks', 'latest-evaluation',
    'criteria-details', 'method-disclosure', 'improvement-disclosure',
    'prior-srs-improvement', 'historical-results', 'source-details', 'record-metadata']);
  document.querySelectorAll('details').forEach(detail => {
    if (foldIds.has(detail.id)) {
      detail.open = false;
      detail.classList.add('report-fold');
      return;
    }
    const section = document.createElement('section');
    for (const attribute of detail.attributes) {
      if (attribute.name !== 'open') section.setAttribute(attribute.name, attribute.value);
    }
    section.classList.add('report-open');
    const summary = detail.querySelector(':scope > summary');
    if (summary) {
      const heading = document.createElement('div');
      heading.className = 'report-heading';
      heading.setAttribute('role', 'heading');
      heading.setAttribute('aria-level', '3');
      heading.append(...summary.childNodes);
      summary.replaceWith(heading);
    }
    section.append(...detail.childNodes);
    detail.replaceWith(section);
  });
  document.documentElement.dataset.reportReady = 'true';
  function focusAnchor() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    const target = document.getElementById(id);
    if (target) {
      for (let node = target; node; node = node.parentElement) {
        if (node.tagName === 'DETAILS') node.open = true;
      }
      target.tabIndex = -1; target.focus({preventScroll:true}); target.scrollIntoView({block:'start'});
    }
  }
  window.addEventListener('hashchange', focusAnchor);
  // Also works when clicking a link whose hash is already in the address bar.
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (link && link.hash === location.hash) requestAnimationFrame(focusAnchor);
  });
  let printState;
  window.addEventListener('beforeprint', () => {
    printState = [...document.querySelectorAll('details')].map(node => [node, node.open]);
    printState.forEach(([node]) => { node.open = true; });
  });
  window.addEventListener('afterprint', () => {
    printState?.forEach(([node, open]) => { node.open = open; });
    printState = null;
  });
  focusAnchor();
})();
