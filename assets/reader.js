(() => {
  const buttons = [...document.querySelectorAll('[data-view]')];
  const views = ['notes', 'timeline'];
  const search = document.getElementById('search');
  const chapters = [...document.querySelectorAll('#notes .chapter')];
  const links = [...document.querySelectorAll('#toc a')];
  let active = 'notes';
  const openedBySearch = new Set();

  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    openedBySearch.forEach(details => { details.open = false; });
    openedBySearch.clear();
    let found = false;
    document.querySelectorAll('#' + active + ' .search-item').forEach(item => {
      item.hidden = !!query && !item.textContent.toLocaleLowerCase().includes(query);
      if (!item.hidden) {
        found = true;
        if (query) {
          item.querySelectorAll('details').forEach(details => {
            if (!details.open && details.textContent.toLocaleLowerCase().includes(query)) {
              details.open = true;
              openedBySearch.add(details);
            }
          });
          let ancestor = item.parentElement;
          while (ancestor && ancestor.id !== active) {
            if (ancestor.tagName === 'DETAILS' && !ancestor.open) {
              ancestor.open = true;
              openedBySearch.add(ancestor);
            }
            ancestor = ancestor.parentElement;
          }
        }
      }
    });
    chapters.forEach(chapter => {
      const intro = chapter.querySelector('.chapter-overview');
      const headerMatches = !!query && intro.textContent.toLocaleLowerCase().includes(query);
      if (active === 'notes' && headerMatches) {
        chapter.querySelectorAll('.search-item').forEach(item => { item.hidden = false; });
        intro.querySelectorAll('details').forEach(details => {
          if (!details.open && details.textContent.toLocaleLowerCase().includes(query)) {
            details.open = true;
            openedBySearch.add(details);
          }
        });
        found = true;
      }
      chapter.hidden = active === 'notes' && !!query && !headerMatches &&
        ![...chapter.querySelectorAll('.search-item')].some(item => !item.hidden);
    });
    document.getElementById('empty').hidden = found;
  }

  function select(view) {
    active = view;
    views.forEach(id => { document.getElementById(id).hidden = id !== active; });
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === active)));
    document.getElementById('toc').hidden = active !== 'notes';
    document.getElementById('toc-label').textContent = active === 'notes' ? '章节' : '按播放顺序';
    filter();
  }
  buttons.forEach(button => button.addEventListener('click', () => select(button.dataset.view)));
  search.addEventListener('input', filter);
  function mark(id) {
    links.forEach(link => link.setAttribute('aria-current', String(link.hash === '#' + id)));
  }
  function followHash() {
    const id = location.hash.slice(1);
    if (/^chapter-\d+$/.test(id)) { select('notes'); mark(id); }
    if (/^segment-\d+$/.test(id)) select('timeline');
  }
  links.forEach(link => link.addEventListener('click', () => { select('notes'); mark(link.hash.slice(1)); }));
  window.addEventListener('hashchange', followHash);
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      if (active !== 'notes') return;
      const visible = entries.filter(entry => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible.length) mark(visible[0].target.id);
    }, { rootMargin: '-5% 0px -65% 0px', threshold: 0 });
    chapters.forEach(chapter => observer.observe(chapter));
  }
  followHash();
})();
