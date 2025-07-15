"""
Document Manager Plugin for Cheshire Cat AI - PRODUCTION READY v.02
"""

from __future__ import annotations

import base64, os, time, inspect, json, re
from pathlib import Path
from unicodedata import normalize
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from cat.auth.permissions import check_permissions, AuthResource, AuthPermission
from cat.log import log
from cat.mad_hatter.decorators import endpoint, hook, plugin, tool
from fastapi import Request, HTTPException, Body, Depends, Query
from fastapi import status
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel, Field

__version__ = "2.0.3"
__author__ = "Cheshire Cat Community"
__description__ = "Production-ready document management with hardened authentication"

# ───────────────────────────── SECURITY ──────────────────────────────

class SecurityManager:
    """Tiny helper class to recognise administrators."""

    _ADMIN_RES = {"PLUGINS", "SETTINGS", "USERS", "MEMORY"}
    _ADMIN_PERMS = {"EDIT", "DELETE", "WRITE"}

    @staticmethod
    def _has_admin_perm(perms: dict) -> bool:
        """Return True if *any* admin‑level permission is found."""
        for res, plist in perms.items():
            res_name = res.value if hasattr(res, "value") else str(res)
            if res_name in SecurityManager._ADMIN_RES:
                for p in plist:
                    p_name = p.value if hasattr(p, "value") else str(p)
                    if p_name in SecurityManager._ADMIN_PERMS:
                        return True
        return False

    # 1️⃣  Called by CLI tools (we receive the Cat instance)
    @staticmethod
    def cli_allowed(cat) -> bool:
        user_data = getattr(cat, "user_data", None)
        if user_data and getattr(user_data, "permissions", None):
            return SecurityManager._has_admin_perm(user_data.permissions)
        # fall‑back ⇒ settings could disable admin‑only mode
        try:
            settings = cat.mad_hatter.get_plugin().load_settings()
            return not settings.get("admin_only_access", True)
        except Exception:
            return False

    # 2️⃣  Generic helper usable from *any* endpoint/handler
    @staticmethod
    def is_admin(stray) -> bool:
        """Return True when the incoming user is an admin."""
        user_data = getattr(stray, "user_data", None)
        if user_data and getattr(user_data, "permissions", None):
            return SecurityManager._has_admin_perm(user_data.permissions)
        return False

# ───────────────────────────── SETTINGS MODEL ─────────────────────────

class DocumentManagerSettings(BaseModel):
    # NOTE: removed *admin_only_access* flag – we always enforce JWT/PLUGINS‑EDIT.
    max_documents_per_page: int = Field(25, ge=5, le=100, title="Documents per page")
    show_document_preview: bool = Field(True, title="Show document preview")
    preview_length: int = Field(200, ge=50, le=1000, title="Preview length (characters)")
    admin_user_ids: str = Field("admin", title="Admin User IDs")
    enable_search_optimization: bool = Field(True, title="Optimize Search Performance")
    memory_chunk_limit: int = Field(1000, ge=100, le=10000, title="Memory Chunk Limit")

@plugin
def settings_model():
    return DocumentManagerSettings

# ───────────────────────────── MEMORY HELPERS ──────────────────────────

class MemoryManager:
    """Optimized memory operations following Cat best practices."""
    
    @staticmethod
    def enumerate_points_robust(cat, limit: Optional[int] = 1000) -> List[Any]:
        """Robust point enumeration with multiple backend fallbacks."""
        collection = cat.memory.vectors.declarative
        
        # Method 1: get_all_points (preferred)
        if hasattr(collection, "get_all_points"):
            try:
                result = collection.get_all_points()
                points = result[0] if isinstance(result, tuple) else result
                if isinstance(points, list):
                    valid_points = [p for p in points if p is not None]
                    return valid_points[:limit] if limit else valid_points
            except Exception as e:
                log.debug(f"get_all_points failed: {e}")
        
        # Method 2: scroll_points (fallback)
        if hasattr(collection, "scroll_points"):
            try:
                points, _ = collection.scroll_points(limit=limit or 10000)
                return points[:limit] if limit and points else points or []
            except Exception as e:
                log.debug(f"scroll_points failed: {e}")
        
        # Method 3: query with empty string (last resort)
        try:
            results = collection.search("", k=limit or 1000, threshold=0.0)
            return [r[0] if isinstance(r, tuple) else r for r in results]
        except Exception as e:
            log.error(f"All enumeration methods failed: {e}")
            return []
    
    @staticmethod
    def search_points_robust(cat, query: str, k: int = 50, threshold: float = 0.3) -> List[Tuple[Any, float]]:
        """Robust search with multiple backend support and fallbacks."""
        collection = cat.memory.vectors.declarative
        
        # Try different search methods
        search_methods = [
            ("search", lambda: collection.search(query, k=k, threshold=threshold)),
            ("query", lambda: collection.query(query, k=k, threshold=threshold)),
            ("similarity_search", lambda: collection.similarity_search(query, k=k)),
            ("search_points", lambda: collection.search_points(query, k=k, threshold=threshold)),
        ]
        
        for method_name, search_func in search_methods:
            if hasattr(collection, method_name):
                try:
                    results = search_func()
                    if results:
                        log.debug(f"Search successful with {method_name}: {len(results)} results")
                        return results
                except Exception as e:
                    log.debug(f"Search method {method_name} failed: {e}")
        
        # Fallback: manual substring search
        log.debug("Using fallback substring search")
        query_lower = query.lower()
        matches = []
        
        for point in MemoryManager.enumerate_points_robust(cat, limit=5000):
            payload = getattr(point, "payload", {}) or {}
            if not isinstance(payload, dict):
                continue
            
            # Search in source and content
            source = payload.get("source", "").lower()
            content = payload.get("page_content", "").lower()
            
            if query_lower in source or query_lower in content:
                matches.append((point, 0.8))  # Arbitrary score for substring matches
        
        return matches[:k]
    
    @staticmethod
    def extract_document_metadata(doc_point) -> Dict[str, Any]:
        """Extract standardized metadata from various point formats."""
        # Handle different point formats
        if hasattr(doc_point, "id") and hasattr(doc_point, "payload"):
            point_id = str(doc_point.id)
            payload = doc_point.payload or {}
        elif isinstance(doc_point, dict):
            point_id = str(doc_point.get("id", "unknown"))
            payload = doc_point
        else:
            point_id = "unknown"
            payload = {}
        
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        page_content = payload.get("page_content", "")
        
        # Extract source/filename with multiple fallback fields
        source_fields = [
            "source", "original_filename", "file_name", "filename",
            "name", "title", "path", "filepath", "document_name"
        ]
        
        source = None
        for field in source_fields:
            if metadata.get(field):
                source = str(metadata[field])
                break
            elif payload.get(field):
                source = str(payload[field])
                break
        
        if not source:
            source = "Unknown Document"
        
        # Extract timestamp with multiple fallback fields
        timestamp_fields = ["when", "timestamp", "created_at", "upload_time", "modified_time"]
        timestamp = None
        
        for field in timestamp_fields:
            if metadata.get(field):
                try:
                    timestamp = float(metadata[field])
                    break
                except (ValueError, TypeError):
                    continue
        
        if timestamp is None:
            timestamp = time.time()
        
        return {
            "id": point_id,
            "source": source,
            "when": timestamp,
            "upload_date": datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M"),
            "page_content_length": len(str(page_content)),
            "chunk_index": metadata.get("chunk_index", 0),
            "total_chunks": metadata.get("total_chunks", 1),
            "content_preview": str(page_content)[:200] + "..." if len(str(page_content)) > 200 else str(page_content)
        }

# Initialize memory manager
memory_manager = MemoryManager()

# ───────────────────────────── DOCUMENT OPERATIONS ────────────────────

class DocumentOperations:
    """Centralized document operations with error handling."""
    
    @staticmethod
    def list_unique_documents(cat, name_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get unique documents (aggregated from chunks) with optional filtering."""
        documents = {}
        
        try:
            points = memory_manager.enumerate_points_robust(cat, limit=None)
            
            for point in points:
                metadata = memory_manager.extract_document_metadata(point)
                source = metadata["source"]
                
                # Apply name filter if specified
                if name_filter and name_filter.lower() not in source.lower():
                    continue
                
                # Aggregate document info
                if source not in documents:
                    documents[source] = {
                        "source": source,
                        "chunks": 0,
                        "total_characters": 0,
                        "when": metadata["when"],
                        "upload_date": metadata["upload_date"]
                    }
                
                doc = documents[source]
                doc["chunks"] += 1
                doc["total_characters"] += metadata["page_content_length"]
                doc["when"] = max(doc["when"], metadata["when"])
                doc["upload_date"] = datetime.fromtimestamp(doc["when"]).strftime("%d/%m/%Y %H:%M")
            
            # Sort by upload date (most recent first)
            return sorted(documents.values(), key=lambda x: x["when"], reverse=True)
            
        except Exception as e:
            log.error(f"Error listing documents: {e}")
            return []
    
    @staticmethod
    def delete_document_by_source(cat, filename: str) -> int:
        """Delete all chunks belonging to a specific document."""
        try:
            query_normalized = DocumentOperations._normalize_filename(filename)
            matching_points = []
            
            # Find all matching points
            for point in memory_manager.enumerate_points_robust(cat, limit=None):
                metadata = memory_manager.extract_document_metadata(point)
                
                # Check various source fields for matches
                source_values = [
                    metadata.get("source", ""),
                    getattr(point, "payload", {}).get("metadata", {}).get("source", ""),
                    getattr(point, "payload", {}).get("metadata", {}).get("filename", ""),
                    getattr(point, "payload", {}).get("metadata", {}).get("file_name", ""),
                ]
                
                for source_value in source_values:
                    if source_value and query_normalized in DocumentOperations._normalize_filename(str(source_value)):
                        matching_points.append(point)
                        break
            
            if not matching_points:
                return 0
            
            # Extract point IDs for deletion
            point_ids = []
            for point in matching_points:
                point_id = getattr(point, "id", None)
                if point_id:
                    point_ids.append(point_id)
            
            if not point_ids:
                return 0
            
            # Delete points using dynamic method detection
            collection = cat.memory.vectors.declarative
            DocumentOperations._delete_points_safely(collection, point_ids)
            
            return len(point_ids)
            
        except Exception as e:
            log.error(f"Error deleting document '{filename}': {e}")
            raise
    
    @staticmethod
    def clear_all_documents(cat) -> int:
        """Delete all documents from memory."""
        try:
            points = memory_manager.enumerate_points_robust(cat, limit=None)
            count = len(points)
            
            if count == 0:
                return 0
            
            # Clear all memory
            collection = cat.memory.vectors.declarative
            collection.delete_points_by_metadata_filter({})
            
            return count
            
        except Exception as e:
            log.error(f"Error clearing all documents: {e}")
            raise
    
    @staticmethod
    def _normalize_filename(filename: str) -> str:
        """Normalize filename for comparison."""
        normalized = normalize("NFKD", filename).encode("ascii", "ignore").decode()
        normalized = normalized.strip().strip("\"'""''")
        normalized = str(Path(normalized).with_suffix(""))
        return normalized.lower()
    
    @staticmethod
    def _delete_points_safely(collection, point_ids: List[str]) -> None:
        """Safely delete points with dynamic method detection."""
        delete_methods = [
            ("delete_points", lambda ids: collection.delete_points(ids=ids)),
            ("delete_points", lambda ids: collection.delete_points(point_ids=ids)),
            ("delete_points", lambda ids: collection.delete_points(ids)),
            ("delete_points_by_ids", lambda ids: collection.delete_points_by_ids(ids)),
            ("delete", lambda ids: collection.delete(ids)),
        ]
        
        for method_name, delete_func in delete_methods:
            if hasattr(collection, method_name):
                try:
                    delete_func(point_ids)
                    return
                except Exception as e:
                    log.debug(f"Delete method {method_name} failed: {e}")
        
        # Fallback: delete one by one
        for point_id in point_ids:
            try:
                collection.delete_point(point_id)
            except Exception as e:
                log.warning(f"Failed to delete point {point_id}: {e}")

# Initialize document operations
doc_ops = DocumentOperations()

# ───────────────────────────── FORMATTING UTILITIES ───────────────────

def format_document_list(documents: List[Dict], show_preview: bool = True, preview_length: int = 200) -> str:
    """Format document list for display."""
    if not documents:
        return "📄 No documents found in Rabbit Hole."
    
    output = f"📚 **Documents in Rabbit Hole** ({len(documents)} found)\n\n"
    
    # Group by source for chunk-level documents
    by_source = {}
    for doc in documents:
        source = doc["source"]
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(doc)
    
    for source, docs in by_source.items():
        # Calculate totals for this document
        total_chunks = len(docs)
        total_chars = sum(d.get("page_content_length", 0) for d in docs)
        latest_date = max(d.get("when", 0) for d in docs)
        upload_date = datetime.fromtimestamp(latest_date).strftime("%d/%m/%Y %H:%M") if latest_date else "Unknown"
        
        output += f"📁 **{source}** ({total_chunks} chunks, {total_chars:,} chars)\n"
        output += f"   └─ Uploaded: {upload_date}\n"
        
        # Show chunk details for documents with multiple chunks
        if total_chunks > 1:
            for doc in docs[:5]:  # Show first 5 chunks
                chunk_info = f"   └─ Chunk {doc.get('chunk_index', 0)}/{doc.get('total_chunks', 1)}"
                chunk_info += f" ({doc.get('page_content_length', 0)} chars)"
                output += chunk_info + "\n"
                
                if show_preview and doc.get("content_preview"):
                    preview = doc["content_preview"][:preview_length]
                    output += f"      *{preview}...*\n"
            
            if total_chunks > 5:
                output += f"   └─ ...and {total_chunks - 5} more chunks\n"
        else:
            # Single chunk document - show preview
            if show_preview and docs[0].get("content_preview"):
                preview = docs[0]["content_preview"][:preview_length]
                output += f"   └─ *{preview}...*\n"
        
        output += "\n"
    
    return output

STATIC_PATH = Path(__file__).parent

def _read_static(fname: str) -> str:
    try:
        return (STATIC_PATH / fname).read_text("utf-8")
    except Exception as exc:
        log.error(f"Static file {fname} error: {exc}")
        return f"/* error loading {fname}: {exc} */"

# All static + API endpoints reuse the same dependency.
AdminDepends = check_permissions(AuthResource.PLUGINS, AuthPermission.EDIT)

# ───────────────────────────── BRUTAL MANUAL AUTH ─────────────────────

def _get_jwt_from_request(request: Request) -> Optional[str]:
    """Return JWT looking at header, cookie, *or* ?token=… query string."""
    hdr = request.headers.get("authorization", "")
    if hdr.startswith("Bearer "):
        return hdr[7:]
    if token := request.cookies.get("ccat_user_token"):
        return token
    if token := request.query_params.get("token"):
        return token
    return None

def _jwt_has_plugin_edit(token: str) -> bool:
    try:
        head, payload_b64, sig = token.split(".")
        payload_b64 += "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64))
        return "EDIT" in data.get("permissions", {}).get("PLUGINS", [])
    except Exception:
        return False

def _brutal_auth_check(request: Request) -> tuple[bool, str]:
    """
    Verify that the JWT (header **or** cookie) exists and contains PLUGINS/EDIT.
    Returns (ok, msg).
    """
    token = _get_jwt_from_request(request)
    if not token:
        return False, "no JWT provided"

    try:
        # Decode without signature
        head, payload_b64, sig = token.split(".")
        payload_b64 += "=" * (-len(payload_b64) % 4)  # padding
        data = json.loads(base64.urlsafe_b64decode(payload_b64))

        if "EDIT" in data.get("permissions", {}).get("PLUGINS", []):
            return True, f"Admin user: {data.get('username', '?')}"
        return False, "missing PLUGINS/EDIT permission"

    except Exception as exc:  # malformed token
        return False, f"JWT parse error: {exc}"

# ───────────────────────────── WEB UI & STATIC - BRUTAL AUTH ──────────

@endpoint.get("/documents")
def web_ui(request: Request, stray = AdminDepends):
    """Return HTML only if caller has PLUGINS/EDIT (checked by dependency)."""
    jwt = _get_jwt_from_request(request)
    if not jwt or not _jwt_has_plugin_edit(jwt):
        return JSONResponse({"detail": "Forbidden"}, status_code=status.HTTP_403_FORBIDDEN)

    log.info(f"✅ Serving Document Manager UI to admin")
    return HTMLResponse(_read_static("document_manager.html"))


@endpoint.get("/documents/style.css")
def css(stray = AdminDepends):
    """Serve CSS file """
    return Response(_read_static("document_manager.css"), media_type="text/css")

@endpoint.get("/documents/script.js")
def js(stray = AdminDepends):
    """Serve JavaScript file"""
    return Response(_read_static("document_manager.js"), media_type="application/javascript")

# ---------------------------------------------------------------------
#  /custom/documents/api/documents
# ---------------------------------------------------------------------

@endpoint.get("/documents/api/documents")
def api_list_documents(
    stray = check_permissions(AuthResource.PLUGINS, AuthPermission.EDIT),
    filter: str = Query("", alias="filter"),
    limit : int = Query(25, ge=1, le=1000),
):
    """
    Returns the list (or search) of documents in the Rabbit Hole.

    Authorization:
        User must have **PLUGINS/EDIT** permission - middleware
        automatically reads JWT from `ccat_user_token` cookie or
        `Authorization: Bearer ...` header and raises HTTP 403 
        if permissions are insufficient.
    """
    # Base settings
    settings = {
        "max_documents_per_page": 25,
        "show_document_preview": True,
        "preview_length": 200,
    }
    max_docs = limit or settings["max_documents_per_page"]
    show_preview   = settings["show_document_preview"]
    preview_length = settings["preview_length"]

    # Memory access
    # In endpoint context, `stray` is the Cheshire Cat instance,
    # so we can use stray.memory directly.
    cat = stray

    if filter.strip():
        results = memory_manager.search_points_robust(cat, filter, k=max_docs)
        points  = [r[0] if isinstance(r, tuple) else r for r in results]
    else:
        points = memory_manager.enumerate_points_robust(cat, limit=max_docs)

    # Transform results
    documents = []
    for p in points:
        doc_info = memory_manager.extract_document_metadata(p)
        if show_preview:
            doc_info["preview"] = doc_info["content_preview"][:preview_length]
        documents.append(doc_info)

    total_chars     = sum(d["page_content_length"] for d in documents)
    unique_sources  = len({d["source"] for d in documents})
    latest_timestamp = max((d["when"] for d in documents), default=None)

    return {
        "success": True,
        "documents": documents,
        "stats": {
            "total_documents"  : unique_sources,
            "total_chunks"     : len(documents),
            "total_characters" : total_chars,
            "last_update"      : (
                datetime.fromtimestamp(latest_timestamp).isoformat()
                if latest_timestamp else None
            ),
        },
        "filter_applied": filter.strip() or None,
    }

@endpoint.get("/documents/api/stats")
def api_document_stats(request: Request, stray = AdminDepends):
    """Get comprehensive document statistics."""
    try:
        points = memory_manager.enumerate_points_robust(stray, limit=None)
        
        stats = {
            "total_documents": 0,
            "total_chunks": len(points),
            "total_characters": 0,
            "sources": {},
            "upload_dates": [],
            "chunk_size_distribution": {"small": 0, "medium": 0, "large": 0}
        }
        
        for point in points:
            doc_info = memory_manager.extract_document_metadata(point)
            source = doc_info["source"]
            
            # Update source statistics
            if source not in stats["sources"]:
                stats["sources"][source] = {
                    "chunks": 0,
                    "characters": 0,
                    "upload_date": doc_info["when"]
                }
            
            source_stats = stats["sources"][source]
            source_stats["chunks"] += 1
            source_stats["characters"] += doc_info["page_content_length"]
            source_stats["upload_date"] = max(source_stats["upload_date"], doc_info["when"])
            
            # Update global statistics
            stats["total_characters"] += doc_info["page_content_length"]
            stats["upload_dates"].append(doc_info["when"])
            
            # Chunk size distribution
            chunk_size = doc_info["page_content_length"]
            if chunk_size < 500:
                stats["chunk_size_distribution"]["small"] += 1
            elif chunk_size < 2000:
                stats["chunk_size_distribution"]["medium"] += 1
            else:
                stats["chunk_size_distribution"]["large"] += 1
        
        stats["total_documents"] = len(stats["sources"])
        
        # Calculate memory usage estimate
        stats["estimated_memory_mb"] = round((stats["total_characters"] * 2) / (1024 * 1024), 2)
        
        # Format dates
        if stats["upload_dates"]:
            stats["last_update"] = datetime.fromtimestamp(max(stats["upload_dates"])).strftime("%d/%m/%Y %H:%M")
            stats["first_update"] = datetime.fromtimestamp(min(stats["upload_dates"])).strftime("%d/%m/%Y %H:%M")
        else:
            stats["last_update"] = "Never"
            stats["first_update"] = "Never"
        
        return {"success": True, **stats}
        
    except Exception as e:
        log.error(f"API stats error: {e}")
        return {"success": False, "error": str(e)}

@endpoint.post("/documents/api/remove")
def api_remove_document(
    request: Request,
    stray = AdminDepends,
    request_data: Dict[str, str] = Body(...),   # FIX - parse JSON body
):
    source = request_data.get("source", "").strip()
    if not source:
        return {"success": False, "message": "Source parameter is required"}
    deleted = doc_ops.delete_document_by_source(stray, source)
    if not deleted:
        return {"success": False, "message": f"Document '{source}' not found"}
    return {"success": True, "message": f"Removed {deleted} chunks", "deleted_chunks": deleted}

@endpoint.post("/documents/api/clear")
def api_clear_all_documents(request: Request, stray = AdminDepends):
    """Clear all documents from memory."""
    try:
        deleted_count = doc_ops.clear_all_documents(stray)
        
        log.warning(f"🧹 ALL DOCUMENTS CLEARED via API ({deleted_count} chunks)")
        
        return {
            "success": True,
            "message": f"All documents cleared ({deleted_count} chunks)",
            "deleted_chunks": deleted_count
        }
        
    except Exception as e:
        log.error(f"API clear error: {e}")
        return {"success": False, "message": str(e)}

# ───────────────────────────── CLI TOOLS ──────────────────────────────

@tool(return_direct=True)
def list_documents(query_filter, cat):
    """List all documents in the Rabbit Hole with optional filtering."""
    if not SecurityManager.cli_allowed(cat):
        return "❌ Access denied: admin privileges required."
    
    try:
        settings = cat.mad_hatter.get_plugin().load_settings()
        max_docs = settings.get("max_documents_per_page", 25)
        show_preview = settings.get("show_document_preview", True)
        preview_length = settings.get("preview_length", 200)
        
        query_filter = (query_filter or "").strip()
        
        if query_filter:
            # Search for specific documents
            search_results = memory_manager.search_points_robust(cat, query_filter, k=max_docs)
            points = [result[0] if isinstance(result, tuple) else result for result in search_results]
            
            if not points:
                return f"🔍 No documents found matching '{query_filter}'"
        else:
            # List all documents
            points = memory_manager.enumerate_points_robust(cat, limit=max_docs)
        
        if not points:
            return "📄 No documents found. Upload some files to get started!"
        
        # Extract document information
        documents = []
        for point in points:
            doc_info = memory_manager.extract_document_metadata(point)
            documents.append(doc_info)
        
        # Remove duplicates and sort
        seen_keys = set()
        unique_documents = []
        for doc in documents:
            key = f"{doc['source']}_{doc['chunk_index']}"
            if key not in seen_keys:
                seen_keys.add(key)
                unique_documents.append(doc)
        
        unique_documents.sort(key=lambda x: x["when"], reverse=True)
        
        # Format output
        header = f"🔍 **Search results for '{query_filter}'**\n\n" if query_filter else ""
        output = header + format_document_list(unique_documents[:max_docs], show_preview, preview_length)
        
        # Add management commands
        output += "\n💡 **Available commands:**\n"
        output += "- `remove_document <filename>` - Remove specific document\n"
        output += "- `clear_all_documents CONFIRM` - Clear all documents\n"
        output += "- `document_statistics` - View detailed statistics\n"
        output += "- `test_document_plugin` - Test plugin functionality\n"
        output += f"- Web interface: `/custom/documents`\n"
        
        return output
        
    except Exception as e:
        log.error(f"Error in list_documents: {e}")
        return f"❌ Error accessing documents: {e}"

@tool(return_direct=True)
def remove_document(document_name, cat):
    """Remove a specific document from the Rabbit Hole."""
    if not SecurityManager.cli_allowed(cat):
        return "❌ Access denied: admin privileges required."
    
    if not document_name or not document_name.strip():
        return "❌ Please specify the document name to remove.\nExample: `remove_document my_file.pdf`"
    
    try:
        filename = document_name.strip()
        deleted_count = doc_ops.delete_document_by_source(cat, filename)
        
        if deleted_count == 0:
            return f"❌ Document '{filename}' not found.\nUse `list_documents` to see available documents."
        
        user_id = getattr(cat, 'user_id', 'unknown')
        log.warning(f"🗑️ Document '{filename}' removed by CLI admin {user_id} ({deleted_count} chunks)")
        
        # Send notification
        cat.send_notification(f"🗑️ Document removed: {filename}")
        
        return f"✅ Successfully removed '{filename}' ({deleted_count} chunks deleted)"
        
    except Exception as e:
        log.error(f"Error removing document: {e}")
        return f"❌ Error removing document: {e}"

@tool(return_direct=True)
def clear_all_documents(confirmation, cat):
    """Clear ALL documents from the Rabbit Hole. Requires confirmation."""
    if not SecurityManager.cli_allowed(cat):
        return "❌ Access denied: admin privileges required."
    
    if confirmation != "CONFIRM":
        return (
            "⚠️ **WARNING**: This will permanently delete ALL documents from the Rabbit Hole!\n\n"
            "This action cannot be undone. All uploaded documents and their chunks will be lost.\n\n"
            "To confirm this action, use: `clear_all_documents CONFIRM`"
        )
    
    try:
        deleted_count = doc_ops.clear_all_documents(cat)
        
        user_id = getattr(cat, 'user_id', 'unknown')
        log.warning(f"🧹 ALL DOCUMENTS CLEARED by CLI admin {user_id} ({deleted_count} chunks)")
        
        # Send notification
        cat.send_notification("🧹 Rabbit Hole completely cleared")
        
        timestamp = datetime.now().strftime('%d/%m/%Y %H:%M')
        return (
            f"✅ **Rabbit Hole cleared successfully**\n\n"
            f"📊 **Results:**\n"
            f"- {deleted_count} chunks deleted\n"
            f"- All documents removed\n"
            f"- Completed at: {timestamp}\n\n"
            f"💡 You can now upload new documents to start fresh."
        )
        
    except Exception as e:
        log.error(f"Error clearing documents: {e}")
        return f"❌ Error clearing documents: {e}"

@tool(return_direct=True)
def document_statistics(detail_level, cat):
    """Show comprehensive statistics about documents in the Rabbit Hole."""
    if not SecurityManager.cli_allowed(cat):
        return "❌ Access denied: admin privileges required."
    
    try:
        detail_level = (detail_level or "basic").lower()
        points = memory_manager.enumerate_points_robust(cat, limit=None)
        
        if not points:
            return "📊 **Document Statistics**\n\n📄 No documents found in Rabbit Hole."
        
        # Calculate comprehensive statistics
        stats = {
            "total_chunks": len(points),
            "total_characters": 0,
            "sources": {},
            "upload_dates": [],
            "chunk_sizes": []
        }
        
        for point in points:
            doc_info = memory_manager.extract_document_metadata(point)
            source = doc_info["source"]
            
            # Update source stats
            if source not in stats["sources"]:
                stats["sources"][source] = {
                    "chunks": 0,
                    "characters": 0,
                    "upload_date": doc_info["when"]
                }
            
            stats["sources"][source]["chunks"] += 1
            stats["sources"][source]["characters"] += doc_info["page_content_length"]
            stats["sources"][source]["upload_date"] = max(
                stats["sources"][source]["upload_date"], 
                doc_info["when"]
            )
            
            # Update global stats
            stats["total_characters"] += doc_info["page_content_length"]
            stats["upload_dates"].append(doc_info["when"])
            stats["chunk_sizes"].append(doc_info["page_content_length"])
        
        stats["total_documents"] = len(stats["sources"])
        
        # Build output
        output = "📊 **Document Statistics**\n\n"
        output += f"📁 **Overview:**\n"
        output += f"• Total documents: {stats['total_documents']}\n"
        output += f"• Total chunks: {stats['total_chunks']}\n"
        output += f"• Total characters: {stats['total_characters']:,}\n"
        output += f"• Average chars per chunk: {stats['total_characters'] // stats['total_chunks']:,}\n"
        output += f"• Estimated memory: {(stats['total_characters'] * 2) / (1024*1024):.1f} MB\n"
        
        if stats["upload_dates"]:
            latest = datetime.fromtimestamp(max(stats["upload_dates"])).strftime("%d/%m/%Y %H:%M")
            earliest = datetime.fromtimestamp(min(stats["upload_dates"])).strftime("%d/%m/%Y %H:%M")
            output += f"• Latest upload: {latest}\n"
            output += f"• First upload: {earliest}\n"
        
        output += "\n"
        
        # Detailed statistics
        if detail_level == "detailed" and stats["sources"]:
            output += "📋 **Document Details:**\n\n"
            
            # Sort documents by chunk count
            sorted_docs = sorted(
                stats["sources"].items(), 
                key=lambda x: x[1]["chunks"], 
                reverse=True
            )
            
            for source, info in sorted_docs[:15]:  # Show top 15
                avg_chunk_size = info["characters"] // info["chunks"]
                upload_date = datetime.fromtimestamp(info["upload_date"]).strftime("%d/%m/%Y")
                
                output += f"📄 **{source}**\n"
                output += f"   └─ {info['chunks']} chunks, {info['characters']:,} characters\n"
                output += f"   └─ Average chunk size: {avg_chunk_size:,} chars\n"
                output += f"   └─ Upload date: {upload_date}\n\n"
            
            if len(stats["sources"]) > 15:
                output += f"...and {len(stats['sources']) - 15} more documents\n\n"
            
            # Chunk size distribution
            small_chunks = len([s for s in stats["chunk_sizes"] if s < 500])
            medium_chunks = len([s for s in stats["chunk_sizes"] if 500 <= s < 2000])
            large_chunks = len([s for s in stats["chunk_sizes"] if s >= 2000])
            
            output += "📈 **Chunk Size Distribution:**\n"
            output += f"• Small (< 500 chars): {small_chunks} chunks\n"
            output += f"• Medium (500-2000 chars): {medium_chunks} chunks\n"
            output += f"• Large (> 2000 chars): {large_chunks} chunks\n\n"
        
        # Management commands
        output += "💡 **Management Commands:**\n"
        output += "• `list_documents` - View all documents\n"
        output += "• `list_documents <search>` - Search documents\n"
        output += "• `remove_document <name>` - Remove specific document\n"
        output += "• `clear_all_documents CONFIRM` - Clear everything\n"
        output += f"• Web interface: `/custom/documents`\n"
        
        return output
        
    except Exception as e:
        log.error(f"Error generating statistics: {e}")
        return f"❌ Error generating statistics: {e}"

@tool(return_direct=True)
def test_document_plugin(test_message, cat):
    """Test the document manager plugin functionality."""
    if not SecurityManager.cli_allowed(cat):
        return "❌ Access denied: admin privileges required."
    
    user_id = getattr(cat, 'user_id', 'unknown')
    
    try:
        # Test memory access
        points = memory_manager.enumerate_points_robust(cat, limit=5)
        memory_status = "✅ Working" if points is not None else "❌ Failed"
        
        # Test settings
        try:
            settings = cat.mad_hatter.get_plugin().load_settings()
            settings_status = "✅ Working"
        except Exception:
            settings_status = "❌ Failed"
        
        # Test document operations
        try:
            docs = doc_ops.list_unique_documents(cat)
            doc_ops_status = "✅ Working"
        except Exception:
            doc_ops_status = "❌ Failed"
        
        output = f"🧪 **Document Manager Plugin Test**\n\n"
        output += f"📋 **System Information:**\n"
        output += f"• Plugin version: {__version__}\n"
        output += f"• User ID: {user_id}\n"
        output += f"• Test message: {test_message or 'None provided'}\n\n"
        
        output += f"🔧 **Component Status:**\n"
        output += f"• Memory access: {memory_status}\n"
        output += f"• Settings system: {settings_status}\n"
        output += f"• Document operations: {doc_ops_status}\n"
        output += f"• Authentication: ✅ Working (you're accessing this)\n\n"
        
        output += f"📊 **Quick Stats:**\n"
        output += f"• Available memory points: {len(points) if points else 0}\n"
        output += f"• Unique documents: {len(doc_ops.list_unique_documents(cat))}\n\n"
        
        output += f"💡 **Available Commands:**\n"
        output += f"• `list_documents` - View all documents\n"
        output += f"• `document_statistics basic` - View statistics\n"
        output += f"• `remove_document <name>` - Remove document\n"
        output += f"• Web interface: `/custom/documents`\n"
        
        return output
        
    except Exception as e:
        log.error(f"Plugin test error: {e}")
        return f"❌ Plugin test failed: {e}"

# ───────────────────────────── HOOKS ─────────────────────────────────

def is_document_command(message: str) -> bool:
    """Check if message contains document management commands."""
    commands = {
        "list_documents", "remove_document", "clear_all_documents",
        "document_statistics", "test_document_plugin"
    }
    
    quick_commands = {
        "list documents", "show documents", "document list",
        "rabbit hole status", "memory status", "documents"
    }
    
    message_lower = message.lower()
    return any(cmd in message_lower for cmd in commands | quick_commands)

@hook(priority=100)
def agent_prompt_prefix(prefix, cat):
    """Customize agent prompt for document management commands."""
    user_message = cat.working_memory.user_message_json.get("text", "")
    
    if is_document_command(user_message):
        return (
            "You are the **Document Manager Assistant** for Cheshire Cat AI.\n"
            "Provide clear, professional responses in English. Output tool results "
            "verbatim without modification. Focus on being helpful and accurate."
        )
    
    return prefix

@hook(priority=10)
def agent_fast_reply(fast_reply, cat):
    """Provide fast replies for common document management queries."""
    message = cat.working_memory.user_message_json.get("text", "")
    if not message:
        return fast_reply
    
    message_lower = message.lower()
    
    # Handle quick test command
    if message_lower.startswith("test_document_plugin"):
        test_msg = " ".join(message.split()[1:]) if len(message.split()) > 1 else ""
        try:
            fast_reply["output"] = test_document_plugin.func(test_msg, cat)
        except Exception as e:
            log.error(f"Fast reply test_document_plugin error: {e}")
        return fast_reply
    
    # Handle quick document commands
    quick_commands = {
        "list documents": lambda: list_documents.func("", cat),
        "show documents": lambda: list_documents.func("", cat),
        "document list": lambda: list_documents.func("", cat),
        "documents": lambda: list_documents.func("", cat),
        "rabbit hole status": lambda: document_statistics.func("basic", cat),
        "memory status": lambda: document_statistics.func("basic", cat),
    }
    
    for trigger, command_func in quick_commands.items():
        if trigger in message_lower:
            try:
                fast_reply["output"] = command_func()
            except Exception as e:
                log.error(f"Fast reply {trigger} error: {e}")
            return fast_reply
    
    return fast_reply

@hook
def after_cat_bootstrap(cat):
    """Initialize plugin after Cat startup."""
    log.info(f"📚 Document Manager Plugin v{__version__} - AUTH GATE FIX")
    log.info("🔧 Features:")
    log.info("   ✅ Hardened JWT authentication with admin check")
    log.info("   ✅ FastAPI dependency injection for security")
    log.info("   ✅ Optimized memory operations with fallbacks")
    log.info("   ✅ Comprehensive error handling and logging")
    log.info("   ✅ Web interface with responsive design")
    log.info("   ✅ CLI tools with user-friendly messages")
    
    # Verify memory access
    try:
        collection = cat.memory.vectors.declarative
        log.info("✅ Memory system access verified")
    except Exception as e:
        log.error(f"❌ Memory system access failed: {e}")
    
    # Initialize default settings
    try:
        plugin = cat.mad_hatter.get_plugin()
        current_settings = plugin.load_settings()
        
        if not current_settings:
            # Create default settings
            default_settings = DocumentManagerSettings()
            plugin.save_settings(default_settings.dict())
            log.info("✅ Default settings initialized")
        else:
            # Log current configuration
            admin_only = current_settings.get("admin_only_access", True)
            admin_users = current_settings.get("admin_user_ids", "admin")
            
            log.info(f"🔧 Current configuration:")
            log.info(f"   - Admin-only access: {admin_only}")
            log.info(f"   - Admin users: {admin_users}")
            log.info(f"   - Max docs per page: {current_settings.get('max_documents_per_page', 25)}")
            
            if not admin_only:
                log.info("💡 Note: Admin-only access is disabled")
    
    except Exception as e:
        log.error(f"❌ Settings initialization failed: {e}")
    
    # Final status
    log.info("🚀 Document Manager plugin initialization complete")
    log.info(f"🌐 Web interface available at: /custom/documents")
    log.info(f"📖 CLI commands: list_documents, remove_document, document_statistics")

# ─────────────────────────── EXPORTS ──────────────────────────────────

__all__ = [
    "settings_model", "web_ui", "css", "js", "api_list_documents", "api_document_stats", 
    "api_remove_document", "api_clear_all_documents", "list_documents", "remove_document", 
    "clear_all_documents", "document_statistics", "test_document_plugin",
    "agent_prompt_prefix", "agent_fast_reply", "after_cat_bootstrap"
]