// Cheshire Cat Document Manager - JavaScript v2 (Official Release)

// Global state
let allChunks = [];
let pendingAction = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => initializeApp());

function initializeApp() {
    detectCatTheme();
    refreshDocuments();
    setupEventListeners();
}

// Function to detect Cheshire Cat's theme and apply it
function detectCatTheme() {
    try {
        const catTheme = window.parent.document.documentElement.getAttribute('data-theme');
        if (catTheme) {
            document.documentElement.setAttribute('data-theme', catTheme);
        }
    } catch (e) {
        console.warn('Could not access parent theme. Using default dark theme.');
    }
}

function setupEventListeners() {
    document.getElementById('searchInput')?.addEventListener('input', debounce(renderDocuments, 300));
    document.getElementById('refreshButton')?.addEventListener('click', refreshDocuments);
    document.getElementById('clearAllButton')?.addEventListener('click', confirmClearAll);
    
    // Close dropdowns or modals when clicking outside
    document.addEventListener('click', (e) => {
        const openDropdown = document.querySelector('.action-dropdown-clone');
        if (openDropdown && !e.target.closest('.action-menu-btn')) {
            openDropdown.remove();
        }
        if (e.target.classList.contains('modal')) {
            closeModal(e.target.id);
        }
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.querySelector('.action-dropdown-clone')?.remove();
            closeModal('confirmModal');
            closeModal('previewModal');
        }
    });
}

// API Functions
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

// UI Functions
async function refreshDocuments() {
    const contentDiv = document.getElementById('documentsContent');
    contentDiv.innerHTML = `<div class="state-container"><div class="spinner"></div><p>Loading documents...</p></div>`;

    const data = await fetchData('/custom/document/api/documents');
    
    if (data && data.success) {
        allChunks = data.documents || [];
        updateStats(data.stats);
        renderDocuments();
    } else {
        contentDiv.innerHTML = `<div class="state-container"><p>Could not load documents.</p></div>`;
        updateStats({}); // Clear stats on error
    }
}

function updateStats(stats = {}) {
    document.getElementById('totalDocuments').textContent = stats.total_documents ?? '-';
    document.getElementById('totalChunks').textContent = formatNumber(stats.total_chunks ?? 0);
    const totalSize = allChunks.reduce((sum, doc) => sum + (doc.page_content_length || 0), 0);
    document.getElementById('totalSize').textContent = formatBytes(totalSize);
    document.getElementById('lastUpdate').textContent = stats.last_update ?? '-';
}

function renderDocuments() {
    const container = document.getElementById('documentsContent');
    if (!container) return;

    const filter = document.getElementById('searchInput')?.value.toLowerCase() || '';
    
    const aggregatedDocs = allChunks.reduce((acc, doc) => {
        if (!acc[doc.source]) {
            acc[doc.source] = { source: doc.source, chunks: 0, totalSize: 0, lastUpdate: 0, chunksList: [] };
        }
        acc[doc.source].chunks++;
        acc[doc.source].totalSize += doc.page_content_length;
        acc[doc.source].lastUpdate = Math.max(acc[doc.source].lastUpdate, doc.when);
        acc[doc.source].chunksList.push(doc);
        return acc;
    }, {});
    
    let documentsToRender = Object.values(aggregatedDocs);

    if (filter) {
        documentsToRender = documentsToRender.filter(doc => doc.source.toLowerCase().includes(filter));
    }
    
    documentsToRender.sort((a, b) => b.lastUpdate - a.lastUpdate);

    if (documentsToRender.length === 0) {
        container.innerHTML = `<div class="state-container"><h3>No documents found</h3><p>${filter ? "Try adjusting your search filters." : "Start by uploading some documents to the Rabbit Hole."}</p></div>`;
        return;
    }
    
    container.innerHTML = documentsToRender.map(doc => {
        const fileExtension = doc.source.split('.').pop().toUpperCase();
        const isUrl = doc.source.startsWith('http');
        const iconType = isUrl ? 'URL' : (['PDF', 'TXT', 'DOCX'].includes(fileExtension) ? fileExtension : 'FILE');
        const escapedSource = escapeHtml(doc.source);

        return `
            <div class="document-row">
                <div class="col-icon">
                    <div class="file-icon" style="background-color: ${getColorForType(iconType)};">${iconType}</div>
                </div>
                <div class="col-main">
                    <span class="file-name" title="${escapedSource}">${escapedSource}</span>
                </div>
                <div class="col-size">${formatBytes(doc.totalSize)}</div>
                <div class="col-chunks">${doc.chunks}</div>
                <div class="col-date">${new Date(doc.lastUpdate * 1000).toLocaleString('en-US', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })}</div>
                <div class="col-actions">
                    <button class="action-menu-btn" onclick="toggleActionMenu(event)">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16"><path d="M9.5 13a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0m0-5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0m0-5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0"/></svg>
                    </button>
                    <div class="action-dropdown">
                        <button onclick="showPreview('${escapedSource}')">
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16"><path d="M10.5 8a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0"/><path d="M0 8s3-5.5 8-5.5S16 8 16 8s-3 5.5-8 5.5S0 8 0 8m1.173-1.173a14.3 14.3 0 0 1 1.66-2.043C4.12 4.068 5.89 3.5 8 3.5s3.879.568 5.168 1.284a14.3 14.3 0 0 1 1.66 2.043A13.3 13.3 0 0 1 16 8c0 .58-.04 1.15-.12 1.713a14.3 14.3 0 0 1-1.66 2.043C11.879 12.432 10.11 13 8 13s-3.879-.568-5.168-1.284A14.3 14.3 0 0 1 1.213 9.713A13.3 13.3 0 0 1 0 8c0-.58.04-1.15.12-1.713zM8 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8"/></svg>
                            View Preview
                        </button>
                        <button class="remove-btn" onclick="confirmRemoveDocument('${escapedSource}')">
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16"><path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5m2.5 0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5m3 .5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0z"/><path d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1h3.5a1 1 0 0 1 1 1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4zM4.5 3.5h7a.5.5 0 0 0 0-1h-7a.5.5 0 0 0 0 1"/></svg>
                            Remove
                        </button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}


// Action and Modal Functions
function toggleActionMenu(event) {
    event.stopPropagation();
    
    // --- JS FIX v3: DETACHED DROPDOWN ---
    const button = event.currentTarget;
    const existingDropdown = document.querySelector('.action-dropdown-clone');

    // If a dropdown exists and we clicked its button again, remove it.
    if (existingDropdown && existingDropdown.dataset.sourceButton === button.parentElement.parentElement.outerHTML) {
        existingDropdown.remove();
        return;
    }
    
    // Remove any other open dropdown
    if(existingDropdown) {
        existingDropdown.remove();
    }

    const templateDropdown = button.nextElementSibling;
    const newDropdown = templateDropdown.cloneNode(true);
    newDropdown.classList.add('action-dropdown-clone');
    // Store which button opened this dropdown to handle toggling
    newDropdown.dataset.sourceButton = button.parentElement.parentElement.outerHTML; 

    document.body.appendChild(newDropdown);

    const buttonRect = button.getBoundingClientRect();
    const dropdownRect = newDropdown.getBoundingClientRect();
    
    let top = buttonRect.bottom + 5;
    let left = buttonRect.left - dropdownRect.width + buttonRect.width;

    // Reposition if it goes off-screen
    if (top + dropdownRect.height > window.innerHeight) {
        top = buttonRect.top - dropdownRect.height - 5;
    }
    if (left < 0) {
        left = buttonRect.left;
    }

    newDropdown.style.top = `${top}px`;
    newDropdown.style.left = `${left}px`;
}

function showPreview(source) {
    document.querySelector('.action-dropdown-clone')?.remove();
    const docData = allChunks.filter(c => c.source === source);
    const previewContent = docData
        .sort((a,b) => a.chunk_index - b.chunk_index)
        .map(chunk => `--- CHUNK ${chunk.chunk_index != null ? chunk.chunk_index : 'N/A'} ---\n${chunk.preview || 'Preview not available.'}`)
        .join('\n\n');

    document.getElementById('previewModalTitle').innerText = `Preview: ${source}`;
    document.getElementById('previewModalBody').innerText = previewContent;
    openModal('previewModal');
}

function confirmRemoveDocument(source) {
    document.querySelector('.action-dropdown-clone')?.remove();
    openModal(
        'confirmModal',
        'Remove Document',
        `Are you sure you want to remove <strong>"${escapeHtml(source)}"</strong>? This action is irreversible.`,
        'remove',
        source
    );
}

function confirmClearAll() {
    document.querySelector('.action-dropdown-clone')?.remove();
    const docCount = new Set(allChunks.map(c => c.source)).size;
    openModal(
        'confirmModal',
        'Clear Rabbit Hole',
        `<strong>WARNING:</strong> You are about to remove all <strong>${docCount}</strong> documents from the Rabbit Hole. This action is irreversible.`,
        'clear'
    );
}

function openModal(modalId, title, body, actionType, source = null) {
    const modal = document.getElementById(modalId);
    if (modalId === 'confirmModal') {
        pendingAction = { type: actionType, source };
        modal.querySelector('.modal-title').textContent = title;
        modal.querySelector('.modal-body').innerHTML = body;
    }
    modal.classList.add('visible');
}

function closeModal(modalId) {
    document.getElementById(modalId)?.classList.remove('visible');
    if (modalId === 'confirmModal') pendingAction = null;
}

async function executeAction() {
    if (!pendingAction) return;
    const { type, source } = pendingAction;
    closeModal('confirmModal');
    
    let result;
    if (type === 'remove') {
        result = await fetchData('/custom/document/api/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source })
        });
    } else if (type === 'clear') {
        result = await fetchData('/custom/document/api/clear', { method: 'POST' });
    }

    if (result) {
        showNotification(result.message, result.success ? 'success' : 'error');
        if (result.success) {
            // Give a moment for the notification to appear before refreshing
            setTimeout(refreshDocuments, 300);
        }
    }
}

// Utility Functions
const formatNumber = (num = 0) => num.toLocaleString('en-US');
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

function showNotification(message, type = 'info') {
    const container = document.getElementById('notifications');
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.innerHTML = message;
    container.appendChild(notification);
    setTimeout(() => {
        notification.style.opacity = '0';
        notification.style.transform = 'translateX(100%)';
        setTimeout(() => notification.remove(), 500);
    }, 5000);
}

const debounce = (func, wait) => {
    let timeout;
    return (...args) => {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
};
