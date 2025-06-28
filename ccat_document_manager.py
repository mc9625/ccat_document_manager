"""
Document Manager Plugin for Cheshire Cat AI - VERSIONE FINALE CON UI NATIVA
File: ccat_document_manager.py

Manages visualization and removal of documents from the rabbit hole.
Compatible with Cheshire Cat AI v1.4.x+
"""

from cat.mad_hatter.decorators import tool, hook, plugin, endpoint
from cat.auth.permissions import check_permissions
from cat.log import log
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from fastapi.responses import HTMLResponse, Response
import json
import time
from datetime import datetime

# Plugin version
__version__ = "1.2.0"

# =============================================================================
# PLUGIN SETTINGS
# =============================================================================

class DocumentManagerSettings(BaseModel):
    """Settings for the Document Manager Plugin."""
    
    max_documents_per_page: int = Field(
        default=20,
        title="Documents per page",
        description="Maximum number of documents to show per page",
        ge=5,
        le=100
    )
    
    show_document_preview: bool = Field(
        default=True,
        title="Document preview", 
        description="Show preview of document content"
    )
    
    preview_length: int = Field(
        default=200,
        title="Preview length",
        description="Number of characters for document preview",
        ge=50,
        le=500
    )

@plugin
def settings_model():
    """Returns the settings model."""
    return DocumentManagerSettings

# =============================================================================
# ROBUST MEMORY ACCESS
# =============================================================================

def _enumerate_points(cat, limit: int = 1000):
    """Return up to <limit> points from declarative memory."""
    coll = cat.memory.vectors.declarative

    # Method 0: get_all_points() - IL METODO CHE FUNZIONA!
    if hasattr(coll, "get_all_points"):
        try:
            raw_result = coll.get_all_points()
            log.debug(f"Raw get_all_points result type: {type(raw_result)}")
            
            # Handle tuple format: (list_of_records, None)
            if isinstance(raw_result, tuple) and len(raw_result) >= 1:
                points_list = raw_result[0]
                if isinstance(points_list, list):
                    valid_points = [p for p in points_list if p is not None][:limit]
                    log.debug(f"Used get_all_points (tuple format): found {len(valid_points)} valid points")
                    return valid_points
            
            # Handle direct list format
            elif isinstance(raw_result, list):
                valid_points = [p for p in raw_result if p is not None][:limit]
                log.debug(f"Used get_all_points (list format): found {len(valid_points)} valid points")
                return valid_points
            
            else:
                log.debug(f"Unknown get_all_points format: {type(raw_result)}")
                return []
                
        except Exception as e:
            log.debug(f"get_all_points failed: {e}")

    # Fallback methods...
    if hasattr(coll, "scroll_points"):
        try:
            points, _next = coll.scroll_points(limit=limit)
            log.debug(f"Used scroll_points: found {len(points)} points")
            return points
        except Exception as e:
            log.debug(f"scroll_points failed: {e}")

    raise RuntimeError("No compatible vector-DB enumeration method found.")

def _search_points(cat, query: str, k: int = 50, threshold: float = 0.3):
    """Search for points using available search methods."""
    coll = cat.memory.vectors.declarative
    
    # Try different search methods
    search_methods = [
        ('search', lambda: coll.search(query, k=k, threshold=threshold)),
        ('query', lambda: coll.query(query, k=k, threshold=threshold)),
        ('similarity_search', lambda: coll.similarity_search(query, k=k)),
        ('search_points', lambda: coll.search_points(query, k=k, threshold=threshold))
    ]
    
    for method_name, method_call in search_methods:
        if hasattr(coll, method_name):
            try:
                results = method_call()
                log.debug(f"Used {method_name}: found {len(results)} results")
                return results
            except Exception as e:
                log.debug(f"{method_name} failed: {e}")
                continue
    
    # Fallback: get all points and filter manually
    log.debug("Fallback to manual filtering")
    try:
        all_points = _enumerate_points(cat, limit=1000)
        filtered_points = []
        
        for point in all_points:
            payload = getattr(point, 'payload', {})
            source = payload.get('source', '').lower()
            content = payload.get('page_content', '').lower()
            
            if query.lower() in source or query.lower() in content:
                filtered_points.append((point, 0.8))
        
        return filtered_points[:k]
    except Exception as e:
        log.error(f"Fallback search failed: {e}")
        return []

def get_document_metadata_robust(doc_point) -> Dict[str, Any]:
    """Extract readable metadata from a document point with robust handling."""
    
    log.debug(f"Point type: {type(doc_point)}")
    
    # Handle Record objects (most common format)
    if hasattr(doc_point, 'id') and hasattr(doc_point, 'payload'):
        point_id = str(doc_point.id)
        payload = doc_point.payload if isinstance(doc_point.payload, dict) else {}
        
        # Extract metadata from payload
        metadata = payload.get('metadata', {})
        page_content = payload.get('page_content', '')
        
        log.debug(f"Using Record format - ID: {point_id}")
    
    # Handle different point formats (fallback)
    elif hasattr(doc_point, 'payload'):
        payload = doc_point.payload
        metadata = payload.get('metadata', {}) if isinstance(payload, dict) else {}
        page_content = payload.get('page_content', '') if isinstance(payload, dict) else ''
        point_id = str(getattr(doc_point, 'id', 'unknown'))
    elif hasattr(doc_point, 'metadata'):
        metadata = doc_point.metadata
        page_content = getattr(doc_point, 'page_content', '')
        point_id = str(getattr(doc_point, 'id', 'unknown'))
    elif isinstance(doc_point, dict):
        metadata = doc_point.get('metadata', {})
        page_content = doc_point.get('page_content', '')
        point_id = str(doc_point.get('id', 'unknown'))
    else:
        log.debug(f"Unknown point format: {doc_point}")
        metadata = {}
        page_content = ''
        point_id = "unknown"
    
    # Extract source
    source_fields = [
        "source", "original_filename", "origin", "file_name", "filename", 
        "name", "document_name", "doc_name", "title", "path", "filepath"
    ]
    
    source = "Unknown"
    # First check in metadata
    for field in source_fields:
        if field in metadata and metadata[field]:
            source = str(metadata[field])
            log.debug(f"Found source in metadata field '{field}': {source}")
            break
    
    # If not found in metadata, check in payload directly
    if source == "Unknown" and 'payload' in locals():
        for field in source_fields:
            if field in payload and payload[field]:
                source = str(payload[field])
                log.debug(f"Found source in payload field '{field}': {source}")
                break
    
    # Extract timestamp
    when = time.time()
    when_fields = ["when", "timestamp", "created_at", "upload_time"]
    for field in when_fields:
        if field in metadata and metadata[field]:
            try:
                when = float(metadata[field])
                break
            except (ValueError, TypeError):
                continue
    
    info = {
        "id": point_id,
        "source": source,
        "when": when,
        "page_content_length": len(page_content),
        "chunk_index": metadata.get("chunk_index", 0),
        "total_chunks": metadata.get("total_chunks", 1),
        "raw_metadata_keys": list(metadata.keys()) if isinstance(metadata, dict) else []
    }
    
    # Convert timestamp to readable date
    try:
        info["upload_date"] = datetime.fromtimestamp(info["when"]).strftime("%d/%m/%Y %H:%M")
    except:
        info["upload_date"] = "Unknown date"
    
    return info

def format_document_list(documents: List[Dict], show_preview: bool = True, preview_length: int = 200) -> str:
    """Format document list for display."""
    if not documents:
        return "📄 No documents found in rabbit hole."
    
    output = f"📚 **Documents in Rabbit Hole** ({len(documents)} found)\n\n"
    
    # Group by source
    sources = {}
    for doc in documents:
        source = doc["source"]
        if source not in sources:
            sources[source] = []
        sources[source].append(doc)
    
    for source, source_docs in sources.items():
        output += f"📁 **{source}** ({len(source_docs)} chunks)\n"
        
        for i, doc in enumerate(source_docs[:10]):
            output += f"   └─ Chunk {doc['chunk_index']}/{doc['total_chunks']} "
            output += f"({doc['page_content_length']} chars) - {doc['upload_date']}\n"
            
            if show_preview and doc.get('preview'):
                output += f"      *{doc['preview']}...*\n"
        
        if len(source_docs) > 10:
            output += f"   └─ ... and {len(source_docs) - 10} more chunks\n"
        output += "\n"
    
    return output

def is_plugin_command(user_message: str) -> bool:
    """Check if user message is a plugin command."""
    plugin_commands = [
        "list_rabbit_hole_documents", "remove_document", "clear_rabbit_hole",
        "document_stats", "document_manager_help", "test_plugin_loaded",
        "debug_memory_access", "inspect_document_structure", "debug_document_payload"
    ]
    
    for cmd in plugin_commands:
        if cmd in user_message.lower():
            return True
    
    quick_triggers = [
        "list documents", "show documents", "document list", 
        "rabbit hole status", "memory status"
    ]
    
    for trigger in quick_triggers:
        if trigger in user_message.lower():
            return True
    
    return False

# =============================================================================
# WEB APP - STILE NATIVO CHESHIRE CAT
# =============================================================================

@endpoint.get("/document/style.css")
def get_css_file(stray=check_permissions("MEMORY", "READ")):
    """Serve the CSS file for the Document Manager."""
    
    css_content = """
/* Cheshire Cat Document Manager - Stile Nativo Corretto */

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', sans-serif;
    background-color: #1a1a1a;
    color: #e2e8f0;
    line-height: 1.5;
    min-height: 100vh;
    margin: 0;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 24px;
}

/* Header - stile Cheshire Cat */
.page-header {
    text-align: center;
    margin-bottom: 32px;
}

.page-title {
    font-size: 24px;
    font-weight: 600;
    color: #e2e8f0;
    margin-bottom: 8px;
}

.page-subtitle {
    color: #94a3b8;
    font-size: 14px;
}

/* Stats Grid - stile Cheshire Cat */
.stats-container {
    margin-bottom: 24px;
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 16px;
}

.stat-card {
    background: #2d3748;
    border: 1px solid #4a5568;
    border-radius: 8px;
    padding: 20px;
    text-align: center;
    transition: all 0.2s ease;
}

.stat-card:hover {
    background: #374151;
    transform: translateY(-1px);
}

/* Icone SVG flat */
.stat-icon {
    width: 40px;
    height: 40px;
    margin: 0 auto 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #374151;
    border-radius: 6px;
}

.stat-icon svg {
    width: 20px;
    height: 20px;
    fill: #94a3b8;
}

.stat-number {
    font-size: 20px;
    font-weight: 600;
    color: #e2e8f0;
    margin-bottom: 4px;
}

.stat-label {
    color: #94a3b8;
    font-size: 11px;
    text-transform: uppercase;
    font-weight: 500;
    letter-spacing: 0.5px;
}

/* Controls - stile Cheshire Cat */
.controls-card {
    background: #2d3748;
    border: 1px solid #4a5568;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 24px;
}

.search-container {
    margin-bottom: 16px;
}

.search-input {
    width: 100%;
    background: #1a202c;
    border: 1px solid #4a5568;
    border-radius: 6px;
    padding: 10px 12px;
    color: #e2e8f0;
    font-size: 14px;
    transition: border-color 0.2s ease;
}

.search-input:focus {
    outline: none;
    border-color: #10b981;
}

.search-input::placeholder {
    color: #718096;
}

.button-group {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
}

.btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: none;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
    text-decoration: none;
}

.btn-primary {
    background: #10b981;
    color: white;
}

.btn-primary:hover {
    background: #059669;
}

.btn-secondary {
    background: #4a5568;
    color: #e2e8f0;
    border: 1px solid #718096;
}

.btn-secondary:hover {
    background: #718096;
}

.btn-danger {
    background: #ef4444;
    color: white;
}

.btn-danger:hover {
    background: #dc2626;
}

.btn-small {
    padding: 6px 12px;
    font-size: 12px;
}

/* Documents Container - stile Cheshire Cat */
.documents-card {
    background: #2d3748;
    border: 1px solid #4a5568;
    border-radius: 8px;
    overflow: hidden;
}

.documents-header {
    background: #374151;
    padding: 16px 20px;
    border-bottom: 1px solid #4a5568;
}

.documents-title {
    font-size: 14px;
    font-weight: 600;
    color: #e2e8f0;
    display: flex;
    align-items: center;
    gap: 8px;
}

.documents-title-icon {
    width: 16px;
    height: 16px;
    fill: #94a3b8;
}

.documents-content {
    padding: 0;
}

/* Document Item - stile Cheshire Cat */
.document-item {
    padding: 20px;
    border-bottom: 1px solid #374151;
    transition: background-color 0.2s ease;
}

.document-item:last-child {
    border-bottom: none;
}

.document-item:hover {
    background: #374151;
}

.document-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
}

.document-title {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 14px;
    font-weight: 500;
    color: #e2e8f0;
}

.document-icon {
    width: 24px;
    height: 24px;
    background: #374151;
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
}

.document-icon svg {
    width: 12px;
    height: 12px;
    fill: #94a3b8;
}

.chunk-count {
    color: #94a3b8;
    font-size: 11px;
    font-weight: normal;
}

.document-actions {
    display: flex;
    gap: 8px;
}

/* Document Meta - stile Cheshire Cat */
.document-meta {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 16px;
}

.meta-item {
    background: #1a202c;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 10px 12px;
    border-left: 3px solid #10b981;
}

.meta-label {
    color: #94a3b8;
    font-size: 9px;
    text-transform: uppercase;
    font-weight: 500;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
}

.meta-value {
    color: #e2e8f0;
    font-size: 13px;
    font-weight: 500;
}

/* Document Preview - stile Cheshire Cat */
.document-preview {
    background: #1a202c;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 12px;
    border-left: 3px solid #10b981;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    font-size: 11px;
    line-height: 1.4;
    color: #a0aec0;
}

/* Loading and Empty States */
.loading, .empty-state {
    text-align: center;
    padding: 40px 20px;
    color: #94a3b8;
}

.empty-state-icon {
    width: 48px;
    height: 48px;
    margin: 0 auto 16px;
    opacity: 0.5;
}

.empty-state-icon svg {
    width: 48px;
    height: 48px;
    fill: #94a3b8;
}

.empty-state h3 {
    font-size: 16px;
    margin-bottom: 8px;
    color: #e2e8f0;
}

/* Notifications */
.notification {
    margin: 12px 0;
    padding: 12px 16px;
    border-radius: 6px;
    font-size: 13px;
    line-height: 1.4;
}

.notification.success {
    background: #065f46;
    color: #a7f3d0;
    border: 1px solid #10b981;
}

.notification.error {
    background: #7f1d1d;
    color: #fca5a5;
    border: 1px solid #ef4444;
}

.notification.info {
    background: #1e3a8a;
    color: #93c5fd;
    border: 1px solid #3b82f6;
}

/* Modal - stile Cheshire Cat */
.modal {
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0, 0, 0, 0.75);
    backdrop-filter: blur(4px);
    z-index: 1000;
}

.modal-content {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: #2d3748;
    border: 1px solid #4a5568;
    border-radius: 8px;
    padding: 24px;
    max-width: 400px;
    width: 90%;
    box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
}

.modal-header {
    font-size: 16px;
    font-weight: 600;
    color: #e2e8f0;
    margin-bottom: 12px;
}

.modal-body {
    color: #94a3b8;
    margin-bottom: 20px;
    line-height: 1.5;
    font-size: 14px;
}

.modal-actions {
    display: flex;
    gap: 12px;
    justify-content: flex-end;
}

/* Responsive */
@media (max-width: 768px) {
    .container {
        padding: 16px;
    }
    
    .stats-grid {
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 12px;
    }
    
    .stat-card {
        padding: 16px;
    }
    
    .button-group {
        flex-direction: column;
    }
    
    .btn {
        justify-content: center;
    }
    
    .document-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 12px;
    }
    
    .document-actions {
        width: 100%;
    }
    
    .btn-small {
        flex: 1;
        justify-content: center;
    }
    
    .document-meta {
        grid-template-columns: 1fr;
    }
}
    """
    
    return Response(content=css_content, media_type="text/css")

@endpoint.get("/document/script.js")
def get_js_file(stray=check_permissions("MEMORY", "READ")):
    """Serve the JavaScript file for the Document Manager."""
    
    js_content = """
// Cheshire Cat Document Manager - JavaScript

// Global state
let currentDocuments = [];
let currentStats = {};
let pendingAction = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

function initializeApp() {
    refreshDocuments();
    setupEventListeners();
}

function setupEventListeners() {
    // Search input
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(filterDocuments, 300));
    }
    
    // Modal close events
    const modal = document.getElementById('confirmModal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) closeModal();
        });
    }
    
    // Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') closeModal();
    });
}

// API Functions
async function fetchDocuments(filter = '') {
    try {
        const response = await fetch(`/custom/document/api/documents?filter=${encodeURIComponent(filter)}`);
        if (!response.ok) throw new Error('Failed to fetch documents');
        return await response.json();
    } catch (error) {
        console.error('Error fetching documents:', error);
        showNotification('Error loading documents: ' + error.message, 'error');
        return { documents: [], stats: {} };
    }
}

async function fetchStats() {
    try {
        const response = await fetch('/custom/document/api/stats');
        if (!response.ok) throw new Error('Failed to fetch stats');
        return await response.json();
    } catch (error) {
        console.error('Error fetching stats:', error);
        return {};
    }
}

async function removeDocument(source) {
    try {
        const response = await fetch('/custom/document/api/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source: source })
        });
        
        if (!response.ok) throw new Error('Failed to remove document');
        const result = await response.json();
        showNotification(result.message, result.success ? 'success' : 'error');
        
        if (result.success) {
            refreshDocuments();
        }
        
        return result;
    } catch (error) {
        console.error('Error removing document:', error);
        showNotification('Error removing document: ' + error.message, 'error');
        return { success: false };
    }
}

async function clearAllDocuments() {
    try {
        const response = await fetch('/custom/document/api/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) throw new Error('Failed to clear documents');
        const result = await response.json();
        showNotification(result.message, result.success ? 'success' : 'error');
        
        if (result.success) {
            refreshDocuments();
        }
        
        return result;
    } catch (error) {
        console.error('Error clearing documents:', error);
        showNotification('Error clearing documents: ' + error.message, 'error');
        return { success: false };
    }
}

// UI Functions
async function refreshDocuments() {
    const filter = document.getElementById('searchInput')?.value || '';
    const data = await fetchDocuments(filter);
    
    currentDocuments = data.documents;
    currentStats = data.stats;
    
    updateStats();
    renderDocuments();
}

function updateStats() {
    const elements = {
        totalDocuments: document.getElementById('totalDocuments'),
        totalChunks: document.getElementById('totalChunks'),
        totalCharacters: document.getElementById('totalCharacters'),
        lastUpdate: document.getElementById('lastUpdate')
    };
    
    if (elements.totalDocuments) elements.totalDocuments.textContent = currentStats.total_documents || '0';
    if (elements.totalChunks) elements.totalChunks.textContent = formatNumber(currentStats.total_chunks || 0);
    if (elements.totalCharacters) elements.totalCharacters.textContent = formatNumber(currentStats.total_characters || 0);
    if (elements.lastUpdate) elements.lastUpdate.textContent = currentStats.last_update || 'Never';
}

function renderDocuments() {
    const container = document.getElementById('documentsContent');
    if (!container) return;
    
    if (currentDocuments.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M14 2H6C4.89543 2 4 2.89543 4 4V20C4 21.1046 4.89543 22 6 22H18C19.1046 22 20 21.1046 20 20V8L14 2ZM16 18H8V16H16V18ZM16 14H8V12H16V14ZM13 9V3.5L18.5 9H13Z"/>
                    </svg>
                </div>
                <h3>No documents found</h3>
                <p>Upload some documents to get started!</p>
            </div>
        `;
        return;
    }
    
    const groupedDocuments = groupDocumentsBySource(currentDocuments);
    
    container.innerHTML = Object.entries(groupedDocuments).map(([source, docs]) => `
        <div class="document-item">
            <div class="document-header">
                <div class="document-title">
                    <div class="document-icon">
                        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M14 2H6C4.89543 2 4 2.89543 4 4V20C4 21.1046 4.89543 22 6 22H18C19.1046 22 20 21.1046 20 20V8L14 2ZM16 18H8V16H16V18ZM16 14H8V12H16V14ZM13 9V3.5L18.5 9H13Z"/>
                        </svg>
                    </div>
                    <div>
                        <div>${escapeHtml(source)}</div>
                        <div class="chunk-count">(${docs.length} chunks)</div>
                    </div>
                </div>
                <div class="document-actions">
                    <button class="btn btn-danger btn-small" onclick="confirmRemoveDocument('${escapeHtml(source)}')">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M3 6H5H21M8 6V4C8 3.46957 8.21071 2.96086 8.58579 2.58579C8.96086 2.21071 9.46957 2 10 2H14C14.5304 2 15.0391 2.21071 15.4142 2.58579C15.7893 2.96086 16 3.46957 16 4V6M19 6V20C19 20.5304 18.7893 21.0391 18.4142 21.4142C18.0391 21.7893 17.5304 22 17 22H7C6.46957 22 5.96086 21.7893 5.58579 21.4142C5.21071 21.0391 5 20.5304 5 20V6H19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        Remove
                    </button>
                </div>
            </div>
            
            <div class="document-meta">
                <div class="meta-item">
                    <div class="meta-label">Total Chunks</div>
                    <div class="meta-value">${docs.length}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Total Characters</div>
                    <div class="meta-value">${formatNumber(docs.reduce((sum, doc) => sum + doc.page_content_length, 0))}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Upload Date</div>
                    <div class="meta-value">${docs[0].upload_date}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Average Chunk Size</div>
                    <div class="meta-value">${Math.round(docs.reduce((sum, doc) => sum + doc.page_content_length, 0) / docs.length)} chars</div>
                </div>
            </div>
            
            ${docs[0].preview ? `
                <div class="document-preview">
                    <strong>Preview:</strong><br>
                    ${escapeHtml(docs[0].preview)}...
                </div>
            ` : ''}
        </div>
    `).join('');
}

function filterDocuments() {
    refreshDocuments();
}

async function showStats() {
    const stats = await fetchStats();
    showNotification(`
        <strong>Detailed Statistics</strong><br>
        Documents: ${stats.total_documents || 0}<br>
        Chunks: ${formatNumber(stats.total_chunks || 0)}<br>
        Characters: ${formatNumber(stats.total_characters || 0)}<br>
        Memory Usage: ${stats.memory_usage || 'Unknown'}<br>
        Last Update: ${stats.last_update || 'Never'}
    `, 'info');
}

// Confirmation Functions
function confirmRemoveDocument(source) {
    pendingAction = { type: 'remove', source: source };
    document.getElementById('modalTitle').textContent = 'Remove Document';
    document.getElementById('modalBody').innerHTML = `
        Are you sure you want to remove the document <strong>"${escapeHtml(source)}"</strong>?<br><br>
        This action cannot be undone.
    `;
    document.getElementById('confirmButton').textContent = 'Remove';
    document.getElementById('confirmModal').style.display = 'block';
}

function confirmClearAll() {
    pendingAction = { type: 'clear' };
    document.getElementById('modalTitle').textContent = 'Clear All Documents';
    document.getElementById('modalBody').innerHTML = `
        <strong>WARNING:</strong> This will remove <strong>ALL</strong> documents from the rabbit hole.<br><br>
        This action cannot be undone. Are you absolutely sure?
    `;
    document.getElementById('confirmButton').textContent = 'Clear All';
    document.getElementById('confirmModal').style.display = 'block';
}

async function executeAction() {
    if (!pendingAction) return;
    
    closeModal();
    
    if (pendingAction.type === 'remove') {
        await removeDocument(pendingAction.source);
    } else if (pendingAction.type === 'clear') {
        await clearAllDocuments();
    }
    
    pendingAction = null;
}

function closeModal() {
    document.getElementById('confirmModal').style.display = 'none';
    pendingAction = null;
}

// Utility Functions
function groupDocumentsBySource(documents) {
    return documents.reduce((groups, doc) => {
        const source = doc.source;
        if (!groups[source]) {
            groups[source] = [];
        }
        groups[source].push(doc);
        return groups;
    }, {});
}

function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    } else if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}

function showNotification(message, type = 'info') {
    const notifications = document.getElementById('notifications');
    if (!notifications) return;
    
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.innerHTML = message;
    
    notifications.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 5000);
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}
    """
    
    return Response(content=js_content, media_type="application/javascript")

@endpoint.get("/document")
def document_manager_web_app(stray=check_permissions("MEMORY", "READ")):
    """Serve the Document Manager Web Application."""
    
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Document Manager - Cheshire Cat AI</title>
    <style>
/* Cheshire Cat Document Manager - Stile Nativo Corretto */

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', sans-serif;
    background-color: #1a1a1a;
    color: #e2e8f0;
    line-height: 1.5;
    min-height: 100vh;
    margin: 0;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 24px;
}

/* Header - stile Cheshire Cat */
.page-header {
    text-align: center;
    margin-bottom: 32px;
}

.page-title {
    font-size: 24px;
    font-weight: 600;
    color: #e2e8f0;
    margin-bottom: 8px;
}

.page-subtitle {
    color: #94a3b8;
    font-size: 14px;
}

/* Stats Grid - stile Cheshire Cat */
.stats-container {
    margin-bottom: 24px;
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 16px;
}

.stat-card {
    background: #2d3748;
    border: 1px solid #4a5568;
    border-radius: 8px;
    padding: 20px;
    text-align: center;
    transition: all 0.2s ease;
}

.stat-card:hover {
    background: #374151;
    transform: translateY(-1px);
}

/* Icone SVG flat */
.stat-icon {
    width: 40px;
    height: 40px;
    margin: 0 auto 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #374151;
    border-radius: 6px;
}

.stat-icon svg {
    width: 20px;
    height: 20px;
    fill: #94a3b8;
}

.stat-number {
    font-size: 20px;
    font-weight: 600;
    color: #e2e8f0;
    margin-bottom: 4px;
}

.stat-label {
    color: #94a3b8;
    font-size: 11px;
    text-transform: uppercase;
    font-weight: 500;
    letter-spacing: 0.5px;
}

/* Controls - stile Cheshire Cat */
.controls-card {
    background: #2d3748;
    border: 1px solid #4a5568;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 24px;
}

.search-container {
    margin-bottom: 16px;
}

.search-input {
    width: 100%;
    background: #1a202c;
    border: 1px solid #4a5568;
    border-radius: 6px;
    padding: 10px 12px;
    color: #e2e8f0;
    font-size: 14px;
    transition: border-color 0.2s ease;
}

.search-input:focus {
    outline: none;
    border-color: #10b981;
}

.search-input::placeholder {
    color: #718096;
}

.button-group {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
}

.btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: none;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
    text-decoration: none;
}

.btn-primary {
    background: #10b981;
    color: white;
}

.btn-primary:hover {
    background: #059669;
}

.btn-secondary {
    background: #4a5568;
    color: #e2e8f0;
    border: 1px solid #718096;
}

.btn-secondary:hover {
    background: #718096;
}

.btn-danger {
    background: #ef4444;
    color: white;
}

.btn-danger:hover {
    background: #dc2626;
}

.btn-small {
    padding: 6px 12px;
    font-size: 12px;
}

/* Documents Container - stile Cheshire Cat */
.documents-card {
    background: #2d3748;
    border: 1px solid #4a5568;
    border-radius: 8px;
    overflow: hidden;
}

.documents-header {
    background: #374151;
    padding: 16px 20px;
    border-bottom: 1px solid #4a5568;
}

.documents-title {
    font-size: 14px;
    font-weight: 600;
    color: #e2e8f0;
    display: flex;
    align-items: center;
    gap: 8px;
}

.documents-title-icon {
    width: 16px;
    height: 16px;
    fill: #94a3b8;
}

.documents-content {
    padding: 0;
}

/* Document Item - stile Cheshire Cat */
.document-item {
    padding: 20px;
    border-bottom: 1px solid #374151;
    transition: background-color 0.2s ease;
}

.document-item:last-child {
    border-bottom: none;
}

.document-item:hover {
    background: #374151;
}

.document-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
}

.document-title {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 14px;
    font-weight: 500;
    color: #e2e8f0;
}

.document-icon {
    width: 24px;
    height: 24px;
    background: #374151;
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
}

.document-icon svg {
    width: 12px;
    height: 12px;
    fill: #94a3b8;
}

.chunk-count {
    color: #94a3b8;
    font-size: 11px;
    font-weight: normal;
}

.document-actions {
    display: flex;
    gap: 8px;
}

/* Document Meta - stile Cheshire Cat */
.document-meta {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 16px;
}

.meta-item {
    background: #1a202c;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 10px 12px;
    border-left: 3px solid #10b981;
}

.meta-label {
    color: #94a3b8;
    font-size: 9px;
    text-transform: uppercase;
    font-weight: 500;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
}

.meta-value {
    color: #e2e8f0;
    font-size: 13px;
    font-weight: 500;
}

/* Document Preview - stile Cheshire Cat */
.document-preview {
    background: #1a202c;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 12px;
    border-left: 3px solid #10b981;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    font-size: 11px;
    line-height: 1.4;
    color: #a0aec0;
}

/* Loading and Empty States */
.loading, .empty-state {
    text-align: center;
    padding: 40px 20px;
    color: #94a3b8;
}

.empty-state-icon {
    width: 48px;
    height: 48px;
    margin: 0 auto 16px;
    opacity: 0.5;
}

.empty-state-icon svg {
    width: 48px;
    height: 48px;
    fill: #94a3b8;
}

.empty-state h3 {
    font-size: 16px;
    margin-bottom: 8px;
    color: #e2e8f0;
}

/* Notifications */
.notification {
    margin: 12px 0;
    padding: 12px 16px;
    border-radius: 6px;
    font-size: 13px;
    line-height: 1.4;
}

.notification.success {
    background: #065f46;
    color: #a7f3d0;
    border: 1px solid #10b981;
}

.notification.error {
    background: #7f1d1d;
    color: #fca5a5;
    border: 1px solid #ef4444;
}

.notification.info {
    background: #1e3a8a;
    color: #93c5fd;
    border: 1px solid #3b82f6;
}

/* Modal - stile Cheshire Cat */
.modal {
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0, 0, 0, 0.75);
    backdrop-filter: blur(4px);
    z-index: 1000;
}

.modal-content {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    background: #2d3748;
    border: 1px solid #4a5568;
    border-radius: 8px;
    padding: 24px;
    max-width: 400px;
    width: 90%;
    box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
}

.modal-header {
    font-size: 16px;
    font-weight: 600;
    color: #e2e8f0;
    margin-bottom: 12px;
}

.modal-body {
    color: #94a3b8;
    margin-bottom: 20px;
    line-height: 1.5;
    font-size: 14px;
}

.modal-actions {
    display: flex;
    gap: 12px;
    justify-content: flex-end;
}

/* Responsive */
@media (max-width: 768px) {
    .container {
        padding: 16px;
    }
    
    .stats-grid {
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 12px;
    }
    
    .stat-card {
        padding: 16px;
    }
    
    .button-group {
        flex-direction: column;
    }
    
    .btn {
        justify-content: center;
    }
    
    .document-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 12px;
    }
    
    .document-actions {
        width: 100%;
    }
    
    .btn-small {
        flex: 1;
        justify-content: center;
    }
    
    .document-meta {
        grid-template-columns: 1fr;
    }
}
    </style>
</head>
<body>
    <div class="container">
        <!-- Page Header -->
        <div class="page-header">
            <h1 class="page-title">Document Manager</h1>
            <p class="page-subtitle">Manage your Cheshire Cat's document memory</p>
        </div>
        
        <!-- Statistics -->
        <div class="stats-container">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon">
                        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z"/>
                        </svg>
                    </div>
                    <div class="stat-number" id="totalDocuments">-</div>
                    <div class="stat-label">Documents</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">
                        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M11 19H6.931L9.5 16.431C9.5 15.5 8.5 15 8.5 15C8.5 15 7.931 15.5 7.431 16L4.5 19.069V4C4.5 3.448 4.948 3 5.5 3H18.5C19.052 3 19.5 3.448 19.5 4V11M16 8H8M8 12H13M16 16L22 22M16 22L22 16"/>
                        </svg>
                    </div>
                    <div class="stat-number" id="totalChunks">-</div>
                    <div class="stat-label">Chunks</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">
                        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M14 2H6C4.89543 2 4 2.89543 4 4V20C4 21.1046 4.89543 22 6 22H18C19.1046 22 20 21.1046 20 20V8L14 2ZM16 18H8V16H16V18ZM16 14H8V12H16V14ZM13 9V3.5L18.5 9H13Z"/>
                        </svg>
                    </div>
                    <div class="stat-number" id="totalCharacters">-</div>
                    <div class="stat-label">Characters</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">
                        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M8 2V5M16 2V5M3.5 9.09H20.5M21 8.5V17C21 18.105 20.105 19 19 19H5C3.895 19 3 18.105 3 17V8.5C3 7.395 3.895 6.5 5 6.5H19C20.105 6.5 21 7.395 21 8.5Z"/>
                        </svg>
                    </div>
                    <div class="stat-number" id="lastUpdate">-</div>
                    <div class="stat-label">Last Update</div>
                </div>
            </div>
        </div>
        
        <!-- Controls -->
        <div class="controls-card">
            <div class="search-container">
                <input type="text" class="search-input" id="searchInput" placeholder="Search documents by name or content...">
            </div>
            <div class="button-group">
                <button class="btn btn-primary" onclick="refreshDocuments()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M4 4V9H4.58152M4.58152 9C5.24618 7.35652 6.43597 5.98273 7.96411 5.11877C9.49225 4.25481 11.2681 3.94716 13.033 4.2512C14.7979 4.55523 16.4084 5.45947 17.6152 6.8091C18.822 8.15874 19.5617 9.8872 19.7280 11.7118M4.58152 9H9M20 20V15H19.4185M19.4185 15C18.7538 16.6435 17.564 18.0173 16.0359 18.8812C14.5078 19.7452 12.7319 20.0528 10.967 19.7488C9.20207 19.4448 7.59159 18.5405 6.38482 17.1909C5.17805 15.8413 4.43833 14.1128 4.27203 12.2882M19.4185 15H15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    Refresh
                </button>
                <button class="btn btn-secondary" onclick="showStats()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M3 13L9 7L13 11L21 3M8 21L16 13L21 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    Detailed Stats
                </button>
                <button class="btn btn-danger" onclick="confirmClearAll()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M3 6H5H21M8 6V4C8 3.46957 8.21071 2.96086 8.58579 2.58579C8.96086 2.21071 9.46957 2 10 2H14C14.5304 2 15.0391 2.21071 15.4142 2.58579C15.7893 2.96086 16 3.46957 16 4V6M19 6V20C19 20.5304 18.7893 21.0391 18.4142 21.4142C18.0391 21.7893 17.5304 22 17 22H7C6.46957 22 5.96086 21.7893 5.58579 21.4142C5.21071 21.0391 5 20.5304 5 20V6H19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    Clear All
                </button>
            </div>
        </div>
        
        <!-- Notifications -->
        <div id="notifications"></div>
        
        <!-- Documents -->
        <div class="documents-card">
            <div class="documents-header">
                <div class="documents-title">
                    <svg class="documents-title-icon" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z"/>
                    </svg>
                    Documents in Rabbit Hole
                </div>
            </div>
            <div class="documents-content" id="documentsContent">
                <div class="loading">
                    <div>Loading documents...</div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Confirmation Modal -->
    <div id="confirmModal" class="modal">
        <div class="modal-content">
            <div class="modal-header" id="modalTitle">Confirm Action</div>
            <div class="modal-body" id="modalBody">Are you sure?</div>
            <div class="modal-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button class="btn btn-danger" onclick="executeAction()" id="confirmButton">Confirm</button>
            </div>
        </div>
    </div>
    
    <script>
// Cheshire Cat Document Manager - JavaScript

// Global state
let currentDocuments = [];
let currentStats = {};
let pendingAction = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

function initializeApp() {
    refreshDocuments();
    setupEventListeners();
}

function setupEventListeners() {
    // Search input
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(filterDocuments, 300));
    }
    
    // Modal close events
    const modal = document.getElementById('confirmModal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) closeModal();
        });
    }
    
    // Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') closeModal();
    });
}

// API Functions
async function fetchDocuments(filter = '') {
    try {
        const response = await fetch(`/custom/document/api/documents?filter=${encodeURIComponent(filter)}`);
        if (!response.ok) throw new Error('Failed to fetch documents');
        return await response.json();
    } catch (error) {
        console.error('Error fetching documents:', error);
        showNotification('Error loading documents: ' + error.message, 'error');
        return { documents: [], stats: {} };
    }
}

async function fetchStats() {
    try {
        const response = await fetch('/custom/document/api/stats');
        if (!response.ok) throw new Error('Failed to fetch stats');
        return await response.json();
    } catch (error) {
        console.error('Error fetching stats:', error);
        return {};
    }
}

async function removeDocument(source) {
    try {
        const response = await fetch('/custom/document/api/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source: source })
        });
        
        if (!response.ok) throw new Error('Failed to remove document');
        const result = await response.json();
        showNotification(result.message, result.success ? 'success' : 'error');
        
        if (result.success) {
            refreshDocuments();
        }
        
        return result;
    } catch (error) {
        console.error('Error removing document:', error);
        showNotification('Error removing document: ' + error.message, 'error');
        return { success: false };
    }
}

async function clearAllDocuments() {
    try {
        const response = await fetch('/custom/document/api/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) throw new Error('Failed to clear documents');
        const result = await response.json();
        showNotification(result.message, result.success ? 'success' : 'error');
        
        if (result.success) {
            refreshDocuments();
        }
        
        return result;
    } catch (error) {
        console.error('Error clearing documents:', error);
        showNotification('Error clearing documents: ' + error.message, 'error');
        return { success: false };
    }
}

// UI Functions
async function refreshDocuments() {
    const filter = document.getElementById('searchInput')?.value || '';
    const data = await fetchDocuments(filter);
    
    currentDocuments = data.documents;
    currentStats = data.stats;
    
    updateStats();
    renderDocuments();
}

function updateStats() {
    const elements = {
        totalDocuments: document.getElementById('totalDocuments'),
        totalChunks: document.getElementById('totalChunks'),
        totalCharacters: document.getElementById('totalCharacters'),
        lastUpdate: document.getElementById('lastUpdate')
    };
    
    if (elements.totalDocuments) elements.totalDocuments.textContent = currentStats.total_documents || '0';
    if (elements.totalChunks) elements.totalChunks.textContent = formatNumber(currentStats.total_chunks || 0);
    if (elements.totalCharacters) elements.totalCharacters.textContent = formatNumber(currentStats.total_characters || 0);
    if (elements.lastUpdate) elements.lastUpdate.textContent = currentStats.last_update || 'Never';
}

function renderDocuments() {
    const container = document.getElementById('documentsContent');
    if (!container) return;
    
    if (currentDocuments.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M14 2H6C4.89543 2 4 2.89543 4 4V20C4 21.1046 4.89543 22 6 22H18C19.1046 22 20 21.1046 20 20V8L14 2ZM16 18H8V16H16V18ZM16 14H8V12H16V14ZM13 9V3.5L18.5 9H13Z"/>
                    </svg>
                </div>
                <h3>No documents found</h3>
                <p>Upload some documents to get started!</p>
            </div>
        `;
        return;
    }
    
    const groupedDocuments = groupDocumentsBySource(currentDocuments);
    
    container.innerHTML = Object.entries(groupedDocuments).map(([source, docs]) => `
        <div class="document-item">
            <div class="document-header">
                <div class="document-title">
                    <div class="document-icon">
                        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M14 2H6C4.89543 2 4 2.89543 4 4V20C4 21.1046 4.89543 22 6 22H18C19.1046 22 20 21.1046 20 20V8L14 2ZM16 18H8V16H16V18ZM16 14H8V12H16V14ZM13 9V3.5L18.5 9H13Z"/>
                        </svg>
                    </div>
                    <div>
                        <div>${escapeHtml(source)}</div>
                        <div class="chunk-count">(${docs.length} chunks)</div>
                    </div>
                </div>
                <div class="document-actions">
                    <button class="btn btn-danger btn-small" onclick="confirmRemoveDocument('${escapeHtml(source)}')">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M3 6H5H21M8 6V4C8 3.46957 8.21071 2.96086 8.58579 2.58579C8.96086 2.21071 9.46957 2 10 2H14C14.5304 2 15.0391 2.21071 15.4142 2.58579C15.7893 2.96086 16 3.46957 16 4V6M19 6V20C19 20.5304 18.7893 21.0391 18.4142 21.4142C18.0391 21.7893 17.5304 22 17 22H7C6.46957 22 5.96086 21.7893 5.58579 21.4142C5.21071 21.0391 5 20.5304 5 20V6H19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        Remove
                    </button>
                </div>
            </div>
            
            <div class="document-meta">
                <div class="meta-item">
                    <div class="meta-label">Total Chunks</div>
                    <div class="meta-value">${docs.length}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Total Characters</div>
                    <div class="meta-value">${formatNumber(docs.reduce((sum, doc) => sum + doc.page_content_length, 0))}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Upload Date</div>
                    <div class="meta-value">${docs[0].upload_date}</div>
                </div>
                <div class="meta-item">
                    <div class="meta-label">Average Chunk Size</div>
                    <div class="meta-value">${Math.round(docs.reduce((sum, doc) => sum + doc.page_content_length, 0) / docs.length)} chars</div>
                </div>
            </div>
            
            ${docs[0].preview ? `
                <div class="document-preview">
                    <strong>Preview:</strong><br>
                    ${escapeHtml(docs[0].preview)}...
                </div>
            ` : ''}
        </div>
    `).join('');
}

function filterDocuments() {
    refreshDocuments();
}

async function showStats() {
    const stats = await fetchStats();
    showNotification(`
        <strong>Detailed Statistics</strong><br>
        Documents: ${stats.total_documents || 0}<br>
        Chunks: ${formatNumber(stats.total_chunks || 0)}<br>
        Characters: ${formatNumber(stats.total_characters || 0)}<br>
        Memory Usage: ${stats.memory_usage || 'Unknown'}<br>
        Last Update: ${stats.last_update || 'Never'}
    `, 'info');
}

// Confirmation Functions
function confirmRemoveDocument(source) {
    pendingAction = { type: 'remove', source: source };
    document.getElementById('modalTitle').textContent = 'Remove Document';
    document.getElementById('modalBody').innerHTML = `
        Are you sure you want to remove the document <strong>"${escapeHtml(source)}"</strong>?<br><br>
        This action cannot be undone.
    `;
    document.getElementById('confirmButton').textContent = 'Remove';
    document.getElementById('confirmModal').style.display = 'block';
}

function confirmClearAll() {
    pendingAction = { type: 'clear' };
    document.getElementById('modalTitle').textContent = 'Clear All Documents';
    document.getElementById('modalBody').innerHTML = `
        <strong>WARNING:</strong> This will remove <strong>ALL</strong> documents from the rabbit hole.<br><br>
        This action cannot be undone. Are you absolutely sure?
    `;
    document.getElementById('confirmButton').textContent = 'Clear All';
    document.getElementById('confirmModal').style.display = 'block';
}

async function executeAction() {
    if (!pendingAction) return;
    
    closeModal();
    
    if (pendingAction.type === 'remove') {
        await removeDocument(pendingAction.source);
    } else if (pendingAction.type === 'clear') {
        await clearAllDocuments();
    }
    
    pendingAction = null;
}

function closeModal() {
    document.getElementById('confirmModal').style.display = 'none';
    pendingAction = null;
}

// Utility Functions
function groupDocumentsBySource(documents) {
    return documents.reduce((groups, doc) => {
        const source = doc.source;
        if (!groups[source]) {
            groups[source] = [];
        }
        groups[source].push(doc);
        return groups;
    }, {});
}

function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    } else if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}

function showNotification(message, type = 'info') {
    const notifications = document.getElementById('notifications');
    if (!notifications) return;
    
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.innerHTML = message;
    
    notifications.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 5000);
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}
    </script>
</body>
</html>
    """
    
    return HTMLResponse(content=html_content, status_code=200)

# =============================================================================
# API ENDPOINTS
# =============================================================================

@endpoint.get("/document/api/documents")
def get_documents_api(filter: str = "", stray=check_permissions("MEMORY", "READ")):
    """API endpoint to get documents list."""
    
    try:
        # Get plugin settings
        settings = stray.mad_hatter.get_plugin().load_settings()
        max_docs = settings.get("max_documents_per_page", 100)
        show_preview = settings.get("show_document_preview", True)
        preview_length = settings.get("preview_length", 200)
        
        # Get documents using plugin functions
        if filter and filter.strip():
            search_results = _search_points(stray, filter, k=max_docs, threshold=0.3)
            documents = []
            
            for result in search_results:
                if isinstance(result, tuple):
                    doc_point, score = result
                else:
                    doc_point, score = result, 0.8
                    
                doc_info = get_document_metadata_robust(doc_point)
                doc_info["relevance_score"] = round(score, 3)
                
                if show_preview:
                    content = ""
                    if hasattr(doc_point, 'payload') and isinstance(doc_point.payload, dict):
                        content = doc_point.payload.get("page_content", "")
                    doc_info["preview"] = content[:preview_length] if content else ""
                
                documents.append(doc_info)
        else:
            # Get all documents
            all_points = _enumerate_points(stray, limit=max_docs)
            documents = []
            
            for point in all_points:
                doc_info = get_document_metadata_robust(point)
                
                if show_preview:
                    content = ""
                    if hasattr(point, 'payload') and isinstance(point.payload, dict):
                        content = point.payload.get("page_content", "")
                    doc_info["preview"] = content[:preview_length] if content else ""
                
                documents.append(doc_info)
        
        # Calculate stats
        sources = {}
        total_characters = 0
        upload_dates = []
        
        for doc in documents:
            source = doc["source"]
            if source not in sources:
                sources[source] = []
            sources[source].append(doc)
            total_characters += doc["page_content_length"]
            upload_dates.append(doc["when"])
        
        stats = {
            "total_documents": len(sources),
            "total_chunks": len(documents),
            "total_characters": total_characters,
            "last_update": max(upload_dates) if upload_dates else None
        }
        
        if stats["last_update"]:
            stats["last_update"] = datetime.fromtimestamp(stats["last_update"]).strftime('%d/%m/%Y %H:%M')
        
        return {
            "success": True,
            "documents": documents,
            "stats": stats
        }
        
    except Exception as e:
        log.error(f"Error in get_documents_api: {e}")
        return {
            "success": False,
            "error": str(e),
            "documents": [],
            "stats": {}
        }

@endpoint.get("/document/api/stats")
def get_stats_api(stray=check_permissions("MEMORY", "READ")):
    """API endpoint to get detailed statistics."""
    
    try:
        all_points = _enumerate_points(stray, limit=1000)
        
        stats = {
            "total_documents": 0,
            "total_chunks": len(all_points),
            "total_characters": 0,
            "sources": {},
            "upload_dates": []
        }
        
        for point in all_points:
            doc_info = get_document_metadata_robust(point)
            source = doc_info["source"]
            
            if source not in stats["sources"]:
                stats["sources"][source] = {
                    "chunks": 0,
                    "characters": 0,
                    "upload_date": doc_info["when"]
                }
            
            stats["sources"][source]["chunks"] += 1
            stats["sources"][source]["characters"] += doc_info["page_content_length"]
            stats["total_characters"] += doc_info["page_content_length"]
            stats["upload_dates"].append(doc_info["when"])
        
        stats["total_documents"] = len(stats["sources"])
        
        # Calculate memory usage estimate
        memory_usage_mb = (stats["total_characters"] * 2) / (1024 * 1024)
        stats["memory_usage"] = f"{memory_usage_mb:.1f} MB"
        
        # Last update
        if stats["upload_dates"]:
            stats["last_update"] = datetime.fromtimestamp(max(stats["upload_dates"])).strftime('%d/%m/%Y %H:%M')
        else:
            stats["last_update"] = "Never"
        
        return {
            "success": True,
            **stats
        }
        
    except Exception as e:
        log.error(f"Error in get_stats_api: {e}")
        return {
            "success": False,
            "error": str(e)
        }

@endpoint.post("/document/api/remove")
def remove_document_api(request: dict, stray=check_permissions("MEMORY", "DELETE")):
    """API endpoint to remove a document."""
    
    try:
        source = request.get("source")
        if not source:
            return {
                "success": False,
                "message": "Source parameter is required"
            }
        
        memory = stray.memory.vectors.declarative
        
        # Search for the document
        search_results = _search_points(stray, source, k=50, threshold=0.1)
        matching_docs = []
        
        for result in search_results:
            if isinstance(result, tuple):
                doc_point, score = result
            else:
                doc_point, score = result, 0.8
                
            doc_info = get_document_metadata_robust(doc_point)
            doc_source = doc_info["source"]
            
            if source.lower() == doc_source.lower():
                matching_docs.append((doc_point, doc_source))
        
        if not matching_docs:
            return {
                "success": False,
                "message": f"Document '{source}' not found"
            }
        
        # Remove using metadata filter
        memory.delete_points_by_metadata_filter({
            "source": source
        })
        
        return {
            "success": True,
            "message": f"Document '{source}' removed successfully ({len(matching_docs)} chunks deleted)"
        }
        
    except Exception as e:
        log.error(f"Error in remove_document_api: {e}")
        return {
            "success": False,
            "message": f"Error removing document: {str(e)}"
        }

@endpoint.post("/document/api/clear")
def clear_all_documents_api(stray=check_permissions("MEMORY", "DELETE")):
    """API endpoint to clear all documents."""
    
    try:
        memory = stray.memory.vectors.declarative
        
        # Count documents before deletion
        all_points = _enumerate_points(stray, limit=10000)
        count_before = len(all_points)
        
        # Delete all documents
        memory.delete_points_by_metadata_filter({})
        
        return {
            "success": True,
            "message": f"All documents cleared successfully! ({count_before} chunks deleted)"
        }
        
    except Exception as e:
        log.error(f"Error in clear_all_documents_api: {e}")
        return {
            "success": False,
            "message": f"Error clearing documents: {str(e)}"
        }

# =============================================================================
# TOOLS (VERSIONE COMPATTA)
# =============================================================================

@tool(return_direct=True)
def test_plugin_loaded(tool_input, cat):
    """Simple test to verify the plugin is loaded and working.
    Input: any test message.
    """
    return f"✅ Document Manager Plugin v{__version__} is loaded and working! Input was: {tool_input}"

@tool(return_direct=True)
def list_rabbit_hole_documents(query_filter, cat):
    """List documents uploaded to the rabbit hole.
    Input: optional filter to search for specific documents (filename, content, etc.)
    If empty, shows all documents.
    """
    
    # Get plugin settings
    settings = cat.mad_hatter.get_plugin().load_settings()
    max_docs = settings.get("max_documents_per_page", 20)
    show_preview = settings.get("show_document_preview", True)
    preview_length = settings.get("preview_length", 200)
    
    try:
        if query_filter and query_filter.strip():
            search_results = _search_points(cat, query_filter, k=max_docs, threshold=0.3)
            
            if not search_results:
                return f"🔍 No documents found for '{query_filter}'"
            
            documents = []
            for result in search_results:
                if isinstance(result, tuple):
                    doc_point, score = result
                else:
                    doc_point, score = result, 0.8
                    
                doc_info = get_document_metadata_robust(doc_point)
                doc_info["relevance_score"] = round(score, 3)
                
                if show_preview:
                    content = ""
                    if hasattr(doc_point, 'payload') and isinstance(doc_point.payload, dict):
                        content = doc_point.payload.get("page_content", "")
                    doc_info["preview"] = content[:preview_length] if content else ""
                
                documents.append(doc_info)
            
            output = f"🔍 **Search results for '{query_filter}'**\n\n"
            output += format_document_list(documents, show_preview, preview_length)
            
        else:
            try:
                all_points = _enumerate_points(cat, limit=max_docs)
                
                if not all_points:
                    return "📄 No documents found in rabbit hole. Try uploading some documents first!"
                
                documents = []
                for point in all_points:
                    doc_info = get_document_metadata_robust(point)
                    
                    if show_preview:
                        content = ""
                        if hasattr(point, 'payload') and isinstance(point.payload, dict):
                            content = point.payload.get("page_content", "")
                        doc_info["preview"] = content[:preview_length] if content else ""
                    
                    documents.append(doc_info)
                
                # Remove duplicates based on source and chunk_index
                seen_keys = set()
                unique_documents = []
                for doc in documents:
                    doc_key = f"{doc['source']}_{doc['chunk_index']}"
                    if doc_key not in seen_keys:
                        seen_keys.add(doc_key)
                        unique_documents.append(doc)
                
                # Sort by upload date (most recent first)
                unique_documents.sort(key=lambda x: x["when"], reverse=True)
                
                output = format_document_list(unique_documents[:max_docs], show_preview, preview_length)
                
            except Exception as e:
                log.error(f"Error in robust document enumeration: {e}")
                return f"❌ Error accessing documents: {str(e)}"
        
        # Add management information
        output += "\n💡 **Available commands:**\n"
        output += "- `remove_document <filename>` - Remove specific document\n"
        output += "- `clear_rabbit_hole CONFIRM` - Empty the entire rabbit hole\n"
        output += "- `document_stats` - Detailed statistics\n"
        output += "- Open web interface: `/custom/document`\n"
        
        return output
        
    except Exception as e:
        log.error(f"Error in list_rabbit_hole_documents: {e}")
        return f"❌ Error accessing memory: {str(e)}"

@tool(return_direct=True)  
def remove_document(document_source, cat):
    """Remove a specific document from the rabbit hole.
    Input: filename/source of the document to remove.
    """
    
    if not document_source or not document_source.strip():
        return "❌ Please specify the document name to remove."
    
    document_source = document_source.strip()
    
    try:
        memory = cat.memory.vectors.declarative
        
        # Search for the document using robust search
        search_results = _search_points(cat, document_source, k=50, threshold=0.1)
        matching_docs = []
        
        for result in search_results:
            if isinstance(result, tuple):
                doc_point, score = result
            else:
                doc_point, score = result, 0.8
                
            doc_info = get_document_metadata_robust(doc_point)
            source = doc_info["source"]
            
            if document_source.lower() in source.lower():
                matching_docs.append((doc_point, source))
        
        if not matching_docs:
            return f"❌ Document '{document_source}' not found in rabbit hole."
        
        # Group by exact source
        sources_found = {}
        for doc_point, source in matching_docs:
            if source not in sources_found:
                sources_found[source] = []
            sources_found[source].append(doc_point)
        
        # If multiple sources, ask for clarification
        if len(sources_found) > 1:
            output = f"🤔 Found multiple documents similar to '{document_source}':\n\n"
            for i, source in enumerate(sources_found.keys(), 1):
                chunk_count = len(sources_found[source])
                output += f"{i}. {source} ({chunk_count} chunks)\n"
            
            output += f"\nPlease specify the exact document name to remove."
            return output
        
        # Remove the document
        source_to_remove = list(sources_found.keys())[0]
        chunks_to_remove = sources_found[source_to_remove]
        
        try:
            # Remove using metadata filter
            memory.delete_points_by_metadata_filter({
                "source": source_to_remove
            })
            
            cat.send_notification(f"🗑️ Document removed: {source_to_remove}")
            return f"✅ Document '{source_to_remove}' successfully removed from rabbit hole ({len(chunks_to_remove)} chunks deleted)."
            
        except Exception as e:
            log.error(f"Error removing document: {e}")
            return f"❌ Error during removal of document '{source_to_remove}': {str(e)}"
            
    except Exception as e:
        log.error(f"Error in remove_document: {e}")
        return f"❌ Error during removal: {str(e)}"

@tool(return_direct=True)
def clear_rabbit_hole(confirmation, cat):
    """Completely empty the rabbit hole (WARNING: irreversible operation!).
    Input: type 'CONFIRM' to confirm the operation.
    """
    
    if confirmation != "CONFIRM":
        return """⚠️ **WARNING**: This operation will delete ALL documents from the rabbit hole.
        
To confirm, execute: `clear_rabbit_hole CONFIRM`

❌ Operation NOT confirmed."""
    
    try:
        memory = cat.memory.vectors.declarative
        
        # Count documents before deletion
        try:
            all_points = _enumerate_points(cat, limit=10000)
            count_before = len(all_points)
        except:
            count_before = "unknown"
        
        # Delete all documents
        memory.delete_points_by_metadata_filter({})
        
        # Verify deletion
        try:
            all_points_after = _enumerate_points(cat, limit=100)
            count_after = len(all_points_after)
        except:
            count_after = 0
        
        cat.send_notification("🧹 Rabbit hole completely emptied")
        
        return f"""✅ **Rabbit hole successfully emptied!**

📊 **Statistics:**
- Documents before: {count_before}
- Documents after: {count_after}
- Operation completed: {datetime.now().strftime('%d/%m/%Y %H:%M')}

💡 You can now upload new documents to the rabbit hole."""
        
    except Exception as e:
        log.error(f"Error in clear_rabbit_hole: {e}")
        return f"❌ Error during emptying: {str(e)}"

@tool(return_direct=True)
def document_stats(detail_level, cat):
    """Show detailed statistics about documents in the rabbit hole.
    Input: 'basic' for basic statistics, 'detailed' for in-depth analysis.
    """
    
    detail_level = detail_level.lower() if detail_level else "basic"
    
    try:
        stats = {
            "total_documents": 0,
            "total_chunks": 0,
            "sources": {},
            "upload_dates": [],
            "total_characters": 0
        }
        
        try:
            all_points = _enumerate_points(cat, limit=1000)
            
            for point in all_points:
                doc_info = get_document_metadata_robust(point)
                source = doc_info["source"]
                
                content_length = doc_info["page_content_length"]
                when = doc_info["when"]
                
                if source not in stats["sources"]:
                    stats["sources"][source] = {
                        "chunks": 0,
                        "characters": 0,
                        "upload_date": when
                    }
                
                stats["sources"][source]["chunks"] += 1
                stats["sources"][source]["characters"] += content_length
                stats["total_chunks"] += 1
                stats["total_characters"] += content_length
                stats["upload_dates"].append(when)
            
            stats["total_documents"] = len(stats["sources"])
            
        except Exception as e:
            log.warning(f"Error in document enumeration: {e}")
            stats["total_chunks"] = "Not available"
        
        # Format output
        output = "📊 **Rabbit Hole Statistics**\n\n"
        
        # Basic statistics
        output += f"📁 **Total documents:** {stats['total_documents']}\n"
        output += f"🧩 **Total chunks:** {stats['total_chunks']}\n"
        output += f"📝 **Total characters:** {stats['total_characters']:,}\n"
        
        if stats["upload_dates"]:
            valid_dates = [d for d in stats["upload_dates"] if isinstance(d, (int, float))]
            if valid_dates:
                latest_upload = max(valid_dates)
                oldest_upload = min(valid_dates)
                output += f"📅 **Latest upload:** {datetime.fromtimestamp(latest_upload).strftime('%d/%m/%Y %H:%M')}\n"
                output += f"📅 **First upload:** {datetime.fromtimestamp(oldest_upload).strftime('%d/%m/%Y %H:%M')}\n"
        
        output += "\n"
        
        # Document details (if requested)
        if detail_level == "detailed" and stats["sources"]:
            output += "📋 **Details per document:**\n\n"
            
            sorted_sources = sorted(
                stats["sources"].items(), 
                key=lambda x: x[1]["chunks"], 
                reverse=True
            )
            
            for source, info in sorted_sources[:10]:
                chunks = info["chunks"]
                chars = info["characters"]
                avg_chunk_size = chars // chunks if chunks > 0 else 0
                upload_date = datetime.fromtimestamp(info["upload_date"]).strftime('%d/%m/%Y')
                
                output += f"📄 **{source}**\n"
                output += f"   └─ {chunks} chunks, {chars:,} characters\n"
                output += f"   └─ Average chunk size: {avg_chunk_size} characters\n"
                output += f"   └─ Uploaded: {upload_date}\n\n"
            
            if len(stats["sources"]) > 10:
                output += f"... and {len(stats['sources']) - 10} more documents\n\n"
        
        # Recommendations
        output += "💡 **Available actions:**\n"
        output += "- 🌐 Open web interface: `/custom/document`\n"
        output += "- 📋 List documents: `list_rabbit_hole_documents`\n"
        output += "- 🗑️ Remove document: `remove_document <filename>`\n"
        
        if isinstance(stats["total_chunks"], int) and stats["total_chunks"] > 1000:
            output += "- ⚠️ Consider removing old documents to improve performance\n"
        elif stats["total_documents"] == 0:
            output += "- 📤 Rabbit hole is empty. Upload some documents to get started!\n"
        
        return output
        
    except Exception as e:
        log.error(f"Error in document_stats: {e}")
        return f"❌ Error calculating statistics: {str(e)}"

# =============================================================================
# HOOKS
# =============================================================================

@hook(priority=100)
def agent_prompt_prefix(prefix, cat):
    """Override system prompt for plugin commands with maximum priority."""
    
    user_message = cat.working_memory.user_message_json.text.lower()
    
    if is_plugin_command(user_message):
        log.info(f"✅ MAXIMUM PRIORITY PROMPT OVERRIDE for: {user_message}")
        
        return (
            "You are the **Document Manager Assistant**.\n"
            "Respond in clear, professional English only.\n"
            "If a tool was called, present its results directly without elaboration.\n"
            "Do not use historical language, elaborate prose, or personal commentary.\n"
            "Focus only on the document management task requested."
        )
    
    return prefix

@hook(priority=10)
def agent_fast_reply(fast_reply, cat):
    """Fast reply for plugin commands - complete LLM bypass."""
    
    msg = cat.working_memory.user_message_json.get("text", "").strip()
    if not msg:
        return fast_reply
    
    msg_lower = msg.lower()
    
    if msg_lower.startswith("test_plugin_loaded"):
        parts = msg.split(maxsplit=1)
        test_input = parts[1] if len(parts) > 1 else ""
        fast_reply["output"] = test_plugin_loaded(test_input, cat)
        log.info(f"🚀 FAST REPLY: test_plugin_loaded")
        return fast_reply
    
    # Quick phrase commands
    quick_commands = {
        "list documents": lambda: list_rabbit_hole_documents("", cat),
        "show documents": lambda: list_rabbit_hole_documents("", cat),
        "document list": lambda: list_rabbit_hole_documents("", cat)
    }
    
    for trigger, func in quick_commands.items():
        if trigger in msg_lower:
            fast_reply["output"] = func()
            log.info(f"🚀 FAST REPLY: {trigger}")
            return fast_reply
    
    return fast_reply

@hook
def after_cat_bootstrap(cat):
    """Plugin initialization."""
    log.info("=== Document Manager Plugin Loading ===")
    log.info(f"Document Manager Plugin v{__version__} loaded successfully")
    
    # Test memory access
    try:
        memory = cat.memory.vectors.declarative
        log.info(f"Memory access test successful: {type(memory)}")
    except Exception as e:
        log.error(f"Memory access test failed: {e}")
    
    # Load settings
    try:
        settings = cat.mad_hatter.get_plugin().load_settings()
        log.info(f"Settings loaded: {len(settings) if settings else 0} items")
        
        if not settings:
            default_settings = DocumentManagerSettings()
            cat.mad_hatter.get_plugin().save_settings(default_settings.dict())
            log.info("Document Manager: default configuration applied")
    except Exception as e:
        log.error(f"Settings configuration failed: {e}")
    
    log.info("=== Document Manager Plugin Ready ===")

# Log plugin registration
log.info(f"Document Manager Plugin v{__version__}: Tools and hooks being registered...")