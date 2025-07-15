/* Cheshire Cat AI – Document Manager UI v4.3 (admin only) */
document.addEventListener('DOMContentLoaded', () => {

  /* ────── helpers ────── */
  const $ = id => document.getElementById(id);

  const debounce = (fn, ms = 300) => {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); };
  };

  const fmtBytes = (b = 0, d = 2) => {
    if (!b) return '0 Bytes';
    const k = 1024, i = Math.floor(Math.log(b) / Math.log(k));
    return (b / Math.pow(k, i)).toFixed(d) + ' ' + ['Bytes','KB','MB','GB','TB'][i];
  };

  const esc = s => (s ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'})[c]);

  const colorFor = t =>
    ({ PDF:'#D32F2F', TXT:'#757575', DOCX:'#1976D2', URL:'#388E3C', FILE:'#512DA8' }[t] || '#512DA8');

  const toast = (msg, kind = 'success') => {
    const host = $('notifications'); if (!host) return;
    const el = document.createElement('div');
    el.className = `alert ${kind === 'error' ? 'alert-error' : 'alert-success'} shadow-lg`;
    el.innerHTML = `<span>${msg}</span>`;
    host.appendChild(el); setTimeout(() => el.remove(), 5_000);
  };

  /* ────── state ────── */
  let chunks = [];
  let pendingSrc = null;

  /* ────── DOM refs ────── */
  const listHost = $('documentsContent');
  const searchIn = $('searchInput');
  const countLbl = $('loadedCount');

  /* info panel */
  const infoOv  = $('infoPanel-overlay');
  const infoPan = $('infoPanel');
  const infoT   = $('infoPanelTitle');
  const infoB   = $('infoPanelBody');
  $('closeInfoPanel')?.addEventListener('click', closeInfo);
  infoOv?.addEventListener('click', closeInfo);

  /* confirm modal */
  const ovl  = $('confirmOverlay');
  const wrap = $('confirmWrapper');
  $('confirmCancelBtn')?.addEventListener('click', closeConfirm);
  $('confirmOkBtn')    ?.addEventListener('click', executeDeletion);

  /* upload */
  const uploadBtn  = $('uploadBtn');
  const fileInput  = document.createElement('input');
  fileInput.type   = 'file';
  fileInput.multiple = true;
  uploadBtn?.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files?.length) uploadFiles(fileInput.files);
    fileInput.value = '';
  });

  /* expose for inline HTML */
  window.openConfirmModal = openConfirmModal;
  window.showInfoPanel    = showInfoPanel;

  /* ────── boot ────── */
  syncTheme();
  searchIn?.addEventListener('input', debounce(renderList));
  refreshList();

  listHost.addEventListener('click', event => {
    const btn = event.target.closest('button[data-action]');
    if (!btn) return;

    const action = btn.getAttribute('data-action');
    const src    = btn.getAttribute('data-src');

    if (action === 'delete') {
      openConfirmModal(src);
    } else if (action === 'info') {
      showInfoPanel(src);
    }
  });



  /* ────── functions ────── */

  function syncTheme() {
    const set = t => document.documentElement.setAttribute('data-theme', t);
    try {
      const p = window.parent.document.documentElement.getAttribute('data-theme');
      if (p) return set(p);
    } catch {/* cross-origin */}
    const mq = matchMedia('(prefers-color-scheme: dark)');
    set(mq.matches ? 'dark' : 'light');
    mq.addEventListener('change', e => set(e.matches ? 'dark' : 'light'));
  }

  /* confirm modal */
  function openConfirmModal(src) {
    pendingSrc = src;
    $('confirmPanelTitle').textContent = 'Remove document';
    $('confirmPanelBody').innerHTML =
      `Are you sure you want to remove <span class="font-bold">${esc(src)}</span>?<br>This action cannot be undone.`;
    ovl.classList.remove('hidden');
    wrap.classList.remove('hidden');
  }
  function closeConfirm() { ovl.classList.add('hidden'); wrap.classList.add('hidden'); pendingSrc = null; }

  async function executeDeletion() {
    if (!pendingSrc) return;
    const src = pendingSrc; closeConfirm();
    /* optimistic */
    chunks = chunks.filter(c => c.source !== src); renderList();
    const r = await api('/custom/documents/api/remove', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ source: src })
    });
    if (r?.success) toast(r.message);
    else { toast('Deletion failed: ' + (r?.message||''), 'error'); refreshList(); }
  }

  /* info panel */
  function showInfoPanel(src) {
    const docs  = chunks.filter(c => c.source === src);
    const total = docs.reduce((s,c) => s + c.page_content_length, 0);
    infoT.textContent = 'Document info';
    infoB.innerHTML = `
      <div class="info-section"><h4>Source</h4><p>${esc(src)}</p></div>
      <div class="info-section"><h4>Statistics</h4>
        <ul><li><b>Total chunks:</b> ${docs.length}</li>
            <li><b>Total size:</b> ${fmtBytes(total)}</li>
            <li><b>Average chunk:</b> ${fmtBytes(total / docs.length)}</li></ul>
      </div>
      <div class="info-section"><h4>Preview</h4>
        <ul>
          ${docs.slice(0,5).map(c=>`<li><b>Chunk ${c.chunk_index??0}:</b> ${esc(c.preview||'')}</li>`).join('')}
          ${docs.length>5 ? `<li><em>…and ${docs.length-5} more</em></li>` : ''}
        </ul>
      </div>`;
    infoOv .classList.add('visible');
    infoPan.classList.add('visible');
  }
  function closeInfo(){ infoPan.classList.remove('visible'); infoOv.classList.remove('visible'); }

  /* upload */
  async function uploadFiles(files) {
    const fd = new FormData();
    [...files].forEach(f => fd.append('files', f, f.name));
    fd.append('chunk_size', 512);
    fd.append('metadata', '{}');

    toast(`Uploading ${files.length} file${files.length>1 ? 's' : ''}…`);
    const r = await fetch('/rabbithole/batch', { method:'POST', body: fd });
    if (!r.ok) { toast('Upload failed', 'error'); return; }
    toast('Upload complete');
    /* prima ricarica subito, seconda dopo 2 s per attendere la vectorizzazione */
    refreshList();
    setTimeout(refreshList, 2000);
  }

  /* API + list */
  async function api(url, opt={}) {
    try {
      const r = await fetch(url, opt);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    } catch (e) { console.error(e); toast(e.message,'error'); return null; }
  }

  async function refreshList() {
    listHost.innerHTML = '<div class="state-container"><p>Loading documents…</p></div>';
    /* chiediamo fino a 1000 doc per non perderne nessuno */
    const d = await api('/custom/documents/api/documents?limit=1000');
    if (d?.success) { chunks = d.documents || []; renderList(); }
    else listHost.innerHTML = '<div class="state-container"><p>Could not load documents.</p></div>';
  }

  function renderList() {
    const term = (searchIn?.value || '').toLowerCase();
    const grouped = {};
    for (const c of chunks) {
      const g = grouped[c.source] ?? (grouped[c.source] = { src:c.source, n:0, size:0, list:[] });
      g.n++; g.size += c.page_content_length; g.list.push(c);
    }
    let docs = Object.values(grouped);
    if (term) docs = docs.filter(d => d.src.toLowerCase().includes(term));

    if (countLbl) countLbl.textContent = `Loaded documents ${docs.length}`;

    if (!docs.length) {
      listHost.innerHTML = `<div class="state-container"><h3>${term ? `No documents found for “${esc(term)}”`
                                                                     : 'No documents found'}</h3></div>`;
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
              <h3 class="text-xl font-bold">${esc(d.src)}</h3>
              <p class="truncate text-sm mt-1 opacity-70">${esc(prev)}</p>
              <div class="text-xs mt-2 opacity-60">${d.n} chunks • ${fmtBytes(d.size)}</div>
            </div>
            <div class="doc-actions">
              <button class="btn btn-error btn-xs" data-action="delete" data-src="${esc(d.src)}">
                DELETE
              </button>
              <button class="btn btn-circle btn-ghost btn-sm" title="Details"
                      data-action="info" data-src="${esc(d.src)}">
                <svg viewBox="0 0 24 24" width="1.2em" height="1.2em">
                  <path fill="currentColor" fill-rule="evenodd"
                        d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75s-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12m8.706-1.442c1.146-.573 2.437.463 2.126 1.706l-.709 2.836l.042-.02a.75.75 0 0 1 .67 1.34l-.04.022c-1.147.573-2.438-.463-2.127-1.706l.71-2.836l-.042.02a.75.75 0 1 1-.671-1.34zM12 9a.75.75 0 1 0 0-1.5a.75.75 0 0 0 0 1.5"
                        clip-rule="evenodd"/>
                </svg>
              </button>
            </div>
          </div>
        </div>`;
    }).join('');
  }
});