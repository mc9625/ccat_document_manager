/* Cheshire Cat AI – Document Manager UI v4.0 (admin only) */
document.addEventListener('DOMContentLoaded', () => {

  /* ───────────── utilities ───────────── */
  const $ = id => document.getElementById(id);

  const debounce = (fn, ms = 300) => {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); };
  };

  const bytes = (b = 0, d = 2) => {
    if (!b) return '0 Bytes';
    const k = 1024, i = Math.floor(Math.log(b) / Math.log(k));
    return (b / Math.pow(k, i)).toFixed(d) + ' ' + ['Bytes', 'KB', 'MB', 'GB', 'TB'][i];
  };

  const escape = s =>
    (s ?? '').replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'})[c]);

  const colorFor = t =>
    ({ PDF:'#D32F2F', TXT:'#757575', DOCX:'#1976D2', URL:'#388E3C', FILE:'#512DA8' }[t] || '#512DA8');

  const notify = (msg, kind = 'success') => {
    const host = $('notifications'); if (!host) return;
    const el = document.createElement('div');
    el.className = `alert ${kind === 'error' ? 'alert-error' : 'alert-success'} shadow-lg`;
    el.innerHTML = `<span>${msg}</span>`;
    host.appendChild(el); setTimeout(() => el.remove(), 5_000);
  };

  /* ───────────── state ───────────── */
  let chunks = [];
  let pendingSrc = null;

  /* ───────────── DOM refs ─────────── */
  const searchInput  = $('searchInput');
  const listHost     = $('documentsContent');

  /* info panel */
  const infoOverlay  = $('infoPanel-overlay');
  const infoPanel    = $('infoPanel');
  const infoTitle    = $('infoPanelTitle');
  const infoBody     = $('infoPanelBody');
  $('closeInfoPanel')?.addEventListener('click', closeInfo);
  infoOverlay       ?.addEventListener('click', closeInfo);

  /* confirm modal */
  const ovl    = $('confirmOverlay');
  const wrap   = $('confirmWrapper');
  const cTitle = $('confirmPanelTitle');
  const cBody  = $('confirmPanelBody');
  const cYes   = $('confirmOkBtn');
  const cNo    = $('confirmCancelBtn');

  cNo ?.addEventListener('click', closeConfirm);
  cYes?.addEventListener('click', executeDeletion);

  /* expose for inline buttons */
  window.openConfirmModal = openConfirmModal;
  window.showInfoPanel    = showInfoPanel;

  /* ───────────── bootstrap ─────────── */
  syncTheme();
  searchInput?.addEventListener('input', debounce(renderList));
  refreshList();

  /* ───────────── theme sync ────────── */
  function syncTheme () {
    const apply = t => document.documentElement.setAttribute('data-theme', t);
    try {
      const parentTheme = window.parent.document.documentElement.getAttribute('data-theme');
      if (parentTheme) return apply(parentTheme);
    } catch {/* cross-origin */}
    const mq = matchMedia('(prefers-color-scheme: dark)');
    apply(mq.matches ? 'dark' : 'light');
    mq.addEventListener('change', e => apply(e.matches ? 'dark' : 'light'));
  }

  /* ───────────── confirm modal ─────── */
  function openConfirmModal (source) {
    pendingSrc = source;
    cTitle.textContent = 'Remove document';
    cBody.innerHTML =
      `Are you sure you want to remove <span class="font-bold">${escape(source)}</span>?<br>` +
      'This action cannot be undone.';
    ovl .classList.remove('hidden');
    wrap.classList.remove('hidden');
  }

  function closeConfirm () {
    ovl .classList.add('hidden');
    wrap.classList.add('hidden');
    pendingSrc = null;
  }

  async function executeDeletion () {
    if (!pendingSrc) return;
    const src = pendingSrc; closeConfirm();

    /* optimistic update */
    chunks = chunks.filter(c => c.source !== src); renderList();

    try {
      const res = await api('/custom/documents/api/remove', {
        method : 'POST',
        headers: { 'Content-Type':'application/json' },
        body   : JSON.stringify({ source: src })
      });
      if (!res?.success) throw new Error(res?.message || 'Unknown error');
      notify(res.message);
    } catch (e) {
      notify('Deletion failed: ' + e.message, 'error');
      refreshList();                            // rollback from backend
    }
  }

  /* ───────────── info panel ────────── */
  function showInfoPanel (source) {
    const docs  = chunks.filter(c => c.source === source);
    const total = docs.reduce((s, c) => s + c.page_content_length, 0);

    infoTitle.textContent = 'Document info';
    infoBody.innerHTML = `
      <div class="info-section"><h4>Source</h4><p>${escape(source)}</p></div>
      <div class="info-section"><h4>Statistics</h4>
        <ul>
          <li><b>Total chunks:</b> ${docs.length}</li>
          <li><b>Total size:</b> ${bytes(total)}</li>
          <li><b>Average chunk:</b> ${bytes(total / docs.length)}</li>
        </ul>
      </div>
      <div class="info-section"><h4>Preview</h4>
        <ul>
          ${docs.slice(0,5).map(c => `<li><b>Chunk ${c.chunk_index ?? 0}:</b> ${escape(c.preview || '')}</li>`).join('')}
          ${docs.length > 5 ? `<li><em>…and ${docs.length - 5} more</em></li>` : ''}
        </ul>
      </div>`;
    infoOverlay.classList.add('visible');
    infoPanel  .classList.add('visible');
  }

  function closeInfo () {
    infoPanel  .classList.remove('visible');
    infoOverlay.classList.remove('visible');
  }

  /* ───────────── API helpers ───────── */
  async function api (url, opt = {}) {
    try {
      const r = await fetch(url, opt);
      if (!r.ok) {
        if ([401,403].includes(r.status)) throw new Error('Access denied: admin rights required');
        throw new Error(`HTTP ${r.status}`);
      }
      const d = await r.json();
      if (d?.error?.includes('Access denied')) throw new Error(d.error);
      return d;
    } catch (e) { console.error(e); notify(e.message, 'error'); return null; }
  }

  async function refreshList () {
    listHost.innerHTML = `<div class="state-container"><p>Loading documents…</p></div>`;
    const data = await api('/custom/documents/api/documents');
    if (data?.success) { chunks = data.documents || []; renderList(); }
    else listHost.innerHTML = `<div class="state-container"><p>Could not load documents.</p></div>`;
  }

  /* ───────────── rendering ─────────── */
  function renderList () {
    const term = (searchInput?.value || '').toLowerCase();
    const grouped = {};
    for (const c of chunks) {
      const g = grouped[c.source] ?? (grouped[c.source] = { src:c.source, n:0, size:0, list:[] });
      g.n++; g.size += c.page_content_length; g.list.push(c);
    }
    let docs = Object.values(grouped);
    if (term) docs = docs.filter(d => d.src.toLowerCase().includes(term));

    if (!docs.length) {
      listHost.innerHTML =
        `<div class="state-container"><h3>${term ? `No documents found for “${escape(term)}”` : 'No documents found'}</h3></div>`;
      return;
    }

    listHost.innerHTML = docs.map(d => {
      const ext  = d.src.split('.').pop().toUpperCase();
      const icon = d.src.startsWith('http') ? 'URL' : (['PDF','TXT','DOCX'].includes(ext) ? ext : 'FILE');
      const prev = d.list[0]?.preview || 'No preview available.';
      return `
        <div class="doc-card">
          <div class="doc-icon" style="background:${colorFor(icon)}">${icon}</div>
          <div class="doc-content-wrapper">
            <div class="doc-info">
              <h3 class="text-xl font-bold">${escape(d.src)}</h3>
              <p class="truncate text-sm mt-1 opacity-70">${escape(prev)}</p>
              <div class="text-xs mt-2 opacity-60">${d.n} chunks • ${bytes(d.size)}</div>
            </div>
            <div class="doc-actions">
              <button class="btn btn-error btn-xs" onclick="window.openConfirmModal('${escape(d.src)}')">DELETE</button>
              <button class="btn btn-circle btn-ghost btn-sm" title="Details" onclick="window.showInfoPanel('${escape(d.src)}')"><svg viewBox="0 0 24 24" width="1.2em" height="1.2em" class="size-5"><path fill="currentColor" fill-rule="evenodd" d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75s-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12m8.706-1.442c1.146-.573 2.437.463 2.126 1.706l-.709 2.836l.042-.02a.75.75 0 0 1 .67 1.34l-.04.022c-1.147.573-2.438-.463-2.127-1.706l.71-2.836l-.042.02a.75.75 0 1 1-.671-1.34zM12 9a.75.75 0 1 0 0-1.5a.75.75 0 0 0 0 1.5" clip-rule="evenodd"></path></svg></button>
            </div>
          </div>
        </div>`;
    }).join('');
  }
});