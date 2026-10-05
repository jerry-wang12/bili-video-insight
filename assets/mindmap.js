(() => {
  'use strict';
  const plan = JSON.parse(document.getElementById('map-data').textContent);
  const viewport = document.getElementById('viewport'), canvas = document.getElementById('canvas');
  const svg = canvas.querySelector('svg'), nodes = new Map(plan.nodes.map(n => [n.id, n]));
  let scale = 1, x = 0, y = 0, focused = false, drag = null, moved = false, selected = null;
  const paint = () => {
    canvas.style.transform = `translate(${x}px,${y}px) scale(${scale})`;
    document.getElementById('zoom').textContent = `${Math.round(scale * 100)}%`;
  };
  const bounds = () => {
    const list = plan.nodes.filter(n => !focused || n.depth < 2);
    const left = Math.min(...list.map(n => n.x)) - 60, top = Math.min(...list.map(n => n.y)) - 60;
    return {left, top, width: Math.max(...list.map(n => n.x + n.width)) - left + 60,
      height: Math.max(...list.map(n => n.y + n.height)) - top + 60};
  };
  const fit = () => {
    const b = bounds();
    scale = Math.min(viewport.clientWidth / b.width, viewport.clientHeight / b.height, 1);
    x = (viewport.clientWidth - b.width * scale) / 2 - b.left * scale;
    y = (viewport.clientHeight - b.height * scale) / 2 - b.top * scale;
    paint();
  };
  const zoom = (factor, px = viewport.clientWidth / 2, py = viewport.clientHeight / 2) => {
    const next = Math.min(3, Math.max(.05, scale * factor));
    x = px - (px - x) * next / scale; y = py - (py - y) * next / scale; scale = next; paint();
  };
  const close = () => {
    document.getElementById('note').hidden = true;
    svg.querySelectorAll('.selected').forEach(n => n.classList.remove('selected'));
    selected?.focus(); selected = null;
  };
  const select = element => {
    const n = nodes.get(element.dataset.id); if (!n) return;
    selected = element;
    svg.querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
    element.classList.add('selected');
    document.getElementById('note').hidden = false;
    document.getElementById('note-title').textContent = n.title;
    document.getElementById('note-time').textContent = n.time;
    document.getElementById('note-key').textContent = n.takeaway;
    const prose = document.getElementById('note-body'); prose.replaceChildren();
    n.body.split('\n\n').filter(p => p.trim()).forEach(text => {
      const p = document.createElement('p'); p.textContent = text; prose.append(p);
    });
    document.getElementById('note-link').href = n.chapter ? `reading.html#chapter-${n.chapter}` : 'reading.html';
  };
  document.getElementById('fit').addEventListener('click', fit);
  document.getElementById('plus').addEventListener('click', () => zoom(1.25));
  document.getElementById('minus').addEventListener('click', () => zoom(.8));
  document.getElementById('close').addEventListener('click', close);
  document.getElementById('focus').addEventListener('click', event => {
    focused = !focused; close();
    svg.querySelectorAll('[data-depth]').forEach(el => {
      const hidden = focused && Number(el.dataset.depth) > 1;
      el.style.display = hidden ? 'none' : '';
      if (el.hasAttribute('tabindex')) el.setAttribute('tabindex', hidden ? '-1' : '0');
    });
    event.currentTarget.textContent = focused ? '展开全部' : '只看主线'; fit();
  });
  viewport.addEventListener('wheel', event => {
    event.preventDefault(); const r = viewport.getBoundingClientRect();
    zoom(Math.exp(-event.deltaY * .0015), event.clientX - r.left, event.clientY - r.top);
  }, {passive: false});
  viewport.addEventListener('pointerdown', event => {
    moved = false;
    if (event.button !== 0 || event.target.closest('.map-node')) return;
    drag = {px:event.clientX, py:event.clientY, x, y}; moved = false;
    viewport.setPointerCapture(event.pointerId); viewport.classList.add('dragging');
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag) return;
    x = drag.x + event.clientX - drag.px; y = drag.y + event.clientY - drag.py;
    moved = Math.hypot(event.clientX - drag.px, event.clientY - drag.py) > 4; paint();
  });
  const end = () => { drag = null; viewport.classList.remove('dragging'); };
  viewport.addEventListener('pointerup', end); viewport.addEventListener('pointercancel', end);
  svg.addEventListener('click', event => { const el = event.target.closest('.map-node'); if (el && !moved) select(el); moved = false; });
  svg.addEventListener('keydown', event => {
    const el = event.target.closest('.map-node');
    if (el && (event.key === 'Enter' || event.key === ' ')) {event.preventDefault(); select(el);}
  });
  document.addEventListener('keydown', event => {if (event.key === 'Escape') close();});
  const status = text => { document.getElementById('status').textContent = text; };
  document.getElementById('png').addEventListener('click', async () => {
    const button = document.getElementById('png'); button.disabled = true;
    let url;
    try {
      status('正在生成图片…');
      const copy = svg.cloneNode(true), b = bounds();
      copy.setAttribute('viewBox', `${b.left} ${b.top} ${b.width} ${b.height}`);
      copy.setAttribute('width', b.width); copy.setAttribute('height', b.height);
      const paper = copy.querySelector('#paper'); paper.setAttribute('x', b.left); paper.setAttribute('y', b.top);
      paper.setAttribute('width', b.width); paper.setAttribute('height', b.height);
      url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)], {type:'image/svg+xml'}));
      const img = new Image(); await new Promise((resolve, reject) => {img.onload = resolve; img.onerror = reject; img.src = url;});
      const ratio = Math.min(2, 6000 / Math.max(b.width, b.height), Math.sqrt(16000000 / (b.width * b.height)));
      const surface = document.createElement('canvas'); surface.width = Math.ceil(b.width * ratio); surface.height = Math.ceil(b.height * ratio);
      surface.getContext('2d').drawImage(img, 0, 0, surface.width, surface.height);
      const blob = await new Promise(resolve => surface.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('empty image');
      const download = URL.createObjectURL(blob), a = document.createElement('a');
      a.href = download; a.download = 'mindmap.png'; a.click(); setTimeout(() => URL.revokeObjectURL(download), 30000);
      status('图片已生成');
    } catch {status('浏览器未能导出 PNG，请下载 SVG 矢量图。');}
    finally {button.disabled = false; if (url) URL.revokeObjectURL(url);}
  });
  window.addEventListener('resize', fit); fit();
})();
