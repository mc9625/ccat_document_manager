// Cheshire Cat Document Manager - Native UI v3.0
document.addEventListener('DOMContentLoaded', () => {

    let allChunks = [];
    let pendingAction = null;

    const searchInput = document.getElementById('searchInput');
    const documentsContent = document.getElementById('documentsContent');
    const infoPanel = document.getElementById('infoPanel');
    const infoPanelOverlay = document.getElementById('infoPanel-overlay');
    const infoPanelTitle = document.getElementById('infoPanelTitle');
    const infoPanelBody = document.getElementById('infoPanelBody');
    const closeInfoPanelBtn = document.getElementById('closeInfoPanel');
    const confirmModal = document.getElementById('confirmModal');
    const confirmTitle = document.getElementById('confirmTitle');
    const confirmBody = document.getElementById('confirmBody');
    const confirmButton = document.getElementById('confirmButton');
    const cancelButton = document.getElementById('cancelButton');
    
    // --- INITIALIZATION ---
    function initializeApp() {
        setupThemeDetection();
        refreshDocuments();
        setupEventListeners();
    }

    function setupThemeDetection() {
        const setTheme = (theme) => {
            document.documentElement.setAttribute('data-theme', theme);
        };

        // 1. Try to get theme from the parent window (Cheshire Cat app)
        try {
            const catTheme = window.parent.document.documentElement.getAttribute('data-theme');
            if (catTheme) {
                setTheme(catTheme);
                return;
            }
        } catch (e) {
            // Parent access might be blocked, fall through to OS preference
        }

        // 2. Fallback to OS preference and add a listener for changes
        const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
        setTheme(mediaQuery.matches ? 'dark' : 'light');
        mediaQuery.addEventListener('change', (e) => {
            setTheme(e.matches ? 'dark' : 'light');
        });
    }

    function setupEventListeners() {
        searchInput?.addEventListener('input', debounce(renderDocuments, 300));
        closeInfoPanelBtn?.addEventListener('click', closeInfoPanel);
        infoPanelOverlay?.addEventListener('click', closeInfoPanel);
        cancelButton?.addEventListener('click', closeConfirmModal);
        confirmButton?.addEventListener('click', executeAction);
    }

    // --- API FUNCTIONS ---
    async function fetchData(url, options = {}) {
        try {
            const response = await fetch(url, options);
            if (!response.ok) throw new Error(`Network response was not ok: ${response.statusText}`);
            return await response.json();
        } catch (error) {
            console.error(`Fetch error for ${url}:`, error);
            showNotification(`Error: ${error.message}`, 'error');
            return null;
        }
    }
    
    async function refreshDocuments() {
        documentsContent.innerHTML = `<div class="state-container"><p>Loading documents...</p></div>`;
        const data = await fetchData('/custom/document/api/documents');
        if (data && data.success) {
            allChunks = data.documents || [];
            renderDocuments();
        } else {
            documentsContent.innerHTML = `<div class="state-container"><p>Could not load documents.</p></div>`;
        }
    }
    
    // --- RENDERING ---
    function renderDocuments() {
        if (!documentsContent) return;
        const filter = searchInput?.value.toLowerCase() || '';

        const aggregatedDocs = allChunks.reduce((acc, doc) => {
            if (!acc[doc.source]) {
                acc[doc.source] = { source: doc.source, chunks: 0, totalSize: 0, chunksList: [] };
            }
            acc[doc.source].chunks++;
            acc[doc.source].totalSize += doc.page_content_length;
            acc[doc.source].chunksList.push(doc);
            return acc;
        }, {});

        let documentsToRender = Object.values(aggregatedDocs);
        if (filter) {
            documentsToRender = documentsToRender.filter(doc => doc.source.toLowerCase().includes(filter));
        }

        if (documentsToRender.length === 0) {
            documentsContent.innerHTML = `<div class="state-container"><h3>No documents found</h3></div>`;
            return;
        }

        documentsContent.innerHTML = documentsToRender.map(doc => {
            const fileExtension = doc.source.split('.').pop().toUpperCase();
            const isUrl = doc.source.startsWith('http');
            const iconType = isUrl ? 'URL' : (['PDF', 'TXT', 'DOCX'].includes(fileExtension) ? fileExtension : 'FILE');
            const escapedSource = escapeHtml(doc.source);
            const preview = doc.chunksList[0]?.preview || 'No preview available.';

            return `
            <div class="doc-card">
                <div class="doc-icon" style="background-color: ${getColorForType(iconType)};">${iconType}</div>
                <div class="doc-content-wrapper">
                    <div class="doc-info">
                        <h3 class="text-xl font-bold">${escapedSource}</h3>
                        <p class="truncate text-sm mt-1 opacity-70">${escapeHtml(preview)}</p>
                    </div>
                    <div class="doc-actions">
                        <button class="btn btn-error btn-xs" onclick="window.confirmRemoveDocument('${escapedSource}')">
                            <svg viewBox="0 0 24 24" width="1.2em" height="1.2em" class="size-3"><path fill="currentColor" fill-rule="evenodd" d="M16.5 4.478v.227a49 49 0 0 1 3.878.512a.75.75 0 1 1-.256 1.478l-.209-.035l-1.005 13.07a3 3 0 0 1-2.991 2.77H8.084a3 3 0 0 1-2.991-2.77L4.087 6.66l-.209.035a.75.75 0 0 1-.256-1.478A49 49 0 0 1 7.5 4.705v-.227c0-1.564 1.213-2.9 2.816-2.951a53 53 0 0 1 3.369 0c1.603.051 2.815 1.387 2.815 2.951m-6.136-1.452a51 51 0 0 1 3.273 0C14.39 3.05 15 3.684 15 4.478v.113a50 50 0 0 0-6 0v-.113c0-.794.609-1.428 1.364-1.452m-.355 5.945a.75.75 0 1 0-1.5.058l.347 9a.75.75 0 1 0 1.499-.058zm5.48.058a.75.75 0 1 0-1.498-.058l-.347 9a.75.75 0 0 0 1.5.058z" clip-rule="evenodd"></path></svg>
                            DELETE
                        </button>
                        <button class="btn btn-ghost btn-circle btn-sm" title="Show Info" onclick="window.showInfoPanel('${escapedSource}')">
                             <svg viewBox="0 0 24 24" width="1.2em" height="1.2em" class="size-5"><path fill="currentColor" fill-rule="evenodd" d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75s-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12m8.706-1.442c1.146-.573 2.437.463 2.126 1.706l-.709 2.836l.042-.02a.75.75 0 0 1 .67 1.34l-.04.022c-1.147.573-2.438-.463-2.127-1.706l.71-2.836l-.042.02a.75.75 0 1 1-.671-1.34zM12 9a.75.75 0 1 0 0-1.5a.75.75 0 0 0 0 1.5" clip-rule="evenodd"></path></svg>
                        </button>
                    </div>
                </div>
            </div>`;
        }).join('');
    }
    
    // --- INTERACTIONS & MODALS ---
    window.showInfoPanel = (source) => {
        const docData = allChunks.filter(c => c.source === source);
        const aggregated = docData.reduce((acc, doc) => {
            acc.totalSize += doc.page_content_length;
            acc.chunks.push(doc);
            return acc;
        }, { totalSize: 0, chunks: [] });

        infoPanelTitle.innerText = 'Document Info';
        infoPanelBody.innerHTML = `
            <div class="info-section">
                <h4>Source</h4>
                <p>${escapeHtml(source)}</p>
            </div>
            <div class="info-section">
                <h4>Statistics</h4>
                <ul>
                    <li><strong>Total Chunks:</strong> ${aggregated.chunks.length}</li>
                    <li><strong>Total Size:</strong> ${formatBytes(aggregated.totalSize)}</li>
                </ul>
            </div>
            <div class="info-section">
                <h4>Chunks Content Preview</h4>
                <ul>
                    ${aggregated.chunks.map(c => `<li><strong>Chunk ${c.chunk_index || 0}:</strong> ${escapeHtml(c.preview || '')}</li>`).join('')}
                </ul>
            </div>
        `;
        infoPanelOverlay.classList.add('visible');
        infoPanel.classList.add('visible');
    };

    function closeInfoPanel() {
        infoPanel.classList.remove('visible');
        infoPanelOverlay.classList.remove('visible');
    }

    window.confirmRemoveDocument = (source) => {
        pendingAction = { type: 'remove', source };
        confirmTitle.innerText = `Remove Document`;
        confirmBody.innerHTML = `Are you sure you want to remove <strong>${escapeHtml(source)}</strong>?`;
        confirmModal.classList.add('visible');
    };

    function closeConfirmModal() {
        confirmModal.classList.remove('visible');
        pendingAction = null;
    }

    async function executeAction() {
        if (!pendingAction) return;
        const { type, source } = pendingAction;
        
        const result = await fetchData('/custom/document/api/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source })
        });
        
        closeConfirmModal();

        if (result && result.success) {
            showNotification(result.message, 'success');
            setTimeout(refreshDocuments, 300);
        } else {
            showNotification(result?.message || 'An error occurred', 'error');
        }
    }

    // --- UTILITY FUNCTIONS ---
    const formatBytes = (bytes = 0, decimals = 2) => {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    };
    const escapeHtml = (text = '') => {
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
        return text.replace(/[&<>"']/g, (m) => map[m]);
    };
    function getColorForType(type) {
        const colors = { 'PDF': '#D32F2F', 'TXT': '#757575', 'DOCX': '#1976D2', 'URL': '#388E3C', 'FILE': '#512DA8' };
        return colors[type] || colors['FILE'];
    }
    function showNotification(message, type = 'success') {
        const container = document.getElementById('notifications');
        const alertClass = type === 'error' ? 'alert-error' : 'alert-success';
        const notification = document.createElement('div');
        notification.className = `alert ${alertClass} shadow-lg`;
        notification.innerHTML = `<span>${message}</span>`;
        container.appendChild(notification);
        setTimeout(() => notification.remove(), 5000);
    }
    const debounce = (func, wait) => {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    };

    initializeApp();
});
