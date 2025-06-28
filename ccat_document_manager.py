"""
Document Manager Plugin for Cheshire Cat AI - VERSIONE CORRETTA
File: ccat_document_manager.py

Manages visualization and removal of documents from the rabbit hole.
Compatible with Cheshire Cat AI v1.4.x+
"""

from cat.mad_hatter.decorators import tool, hook, plugin
from cat.log import log
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import json
import time
from datetime import datetime

# Plugin version
__version__ = "1.1.1"

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
# ROBUST MEMORY ACCESS (VERSIONE MIGLIORATA)
# =============================================================================

def _enumerate_points(cat, limit: int = 1000):
    """
    Return up to <limit> points from declarative memory, trying every known
    API variant so the plugin works across core versions.
    """
    coll = cat.memory.vectors.declarative

    # Method 0: get_all_points() - IL METODO CHE FUNZIONA!
    if hasattr(coll, "get_all_points"):
        try:
            raw_result = coll.get_all_points()
            log.debug(f"Raw get_all_points result type: {type(raw_result)}")
            
            # Handle tuple format: (list_of_records, None)
            if isinstance(raw_result, tuple) and len(raw_result) >= 1:
                points_list = raw_result[0]  # First element is the list of documents
                if isinstance(points_list, list):
                    # Filter out None items and limit results
                    valid_points = [p for p in points_list if p is not None][:limit]
                    log.debug(f"Used get_all_points (tuple format): found {len(valid_points)} valid points from tuple")
                    return valid_points
            
            # Handle direct list format
            elif isinstance(raw_result, list):
                valid_points = [p for p in raw_result if p is not None][:limit]
                log.debug(f"Used get_all_points (list format): found {len(valid_points)} valid points")
                return valid_points
            
            # Handle other formats
            else:
                log.debug(f"Unknown get_all_points format: {type(raw_result)}")
                return []
                
        except Exception as e:
            log.debug(f"get_all_points failed: {e}")

    # Method 1: scroll_points(limit=…) - Preferred method
    if hasattr(coll, "scroll_points"):
        try:
            points, _next = coll.scroll_points(limit=limit)
            log.debug(f"Used scroll_points: found {len(points)} points")
            return points
        except Exception as e:
            log.debug(f"scroll_points failed: {e}")

    # Method 2: list_ids + get_points(ids=…)
    if hasattr(coll, "list_ids") and hasattr(coll, "get_points"):
        try:
            ids = coll.list_ids(limit=limit)
            points = coll.get_points(ids=ids)
            log.debug(f"Used list_ids+get_points: found {len(points)} points")
            return points
        except Exception as e:
            log.debug(f"list_ids+get_points failed: {e}")

    # Method 3: get_points(limit=…) or get_points()
    if hasattr(coll, "get_points"):
        try:
            points = coll.get_points(limit=limit)  # new signature
            log.debug(f"Used get_points(limit): found {len(points)} points")
            return points
        except TypeError:
            try:
                points = coll.get_points()[:limit]  # zero-arg signature
                log.debug(f"Used get_points(): found {len(points)} points")
                return points
            except Exception as e:
                log.debug(f"get_points failed: {e}")

    raise RuntimeError("No compatible vector-DB enumeration method found.")

def _search_points(cat, query: str, k: int = 50, threshold: float = 0.3):
    """
    Search for points using available search methods.
    """
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
            # Check if query matches source or content
            payload = getattr(point, 'payload', {})
            source = payload.get('source', '').lower()
            content = payload.get('page_content', '').lower()
            
            if query.lower() in source or query.lower() in content:
                filtered_points.append((point, 0.8))  # Mock score
        
        return filtered_points[:k]
    except Exception as e:
        log.error(f"Fallback search failed: {e}")
        return []

def get_document_metadata_robust(doc_point) -> Dict[str, Any]:
    """Extract readable metadata from a document point with robust handling."""
    
    # Debug: log the structure of the point
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
    
    # Extract source with ALL possible field names
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
    
    # Extract timestamp - try multiple formats
    when = time.time()  # default to now
    when_fields = ["when", "timestamp", "created_at", "upload_time"]
    for field in when_fields:
        if field in metadata and metadata[field]:
            try:
                when = float(metadata[field])
                break
            except (ValueError, TypeError):
                continue
    
    # Log all metadata keys for debugging
    log.debug(f"All metadata keys: {list(metadata.keys()) if isinstance(metadata, dict) else 'not a dict'}")
    
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

# =============================================================================
# UTILITY FUNCTIONS (AGGIORNATE)
# =============================================================================

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
        "list_rabbit_hole_documents",
        "remove_document", 
        "clear_rabbit_hole",
        "document_stats",
        "document_manager_help",
        "test_plugin_loaded",
        "debug_memory_access",
        "inspect_document_structure",
        "debug_document_payload"  # Aggiunto alla lista
    ]
    
    # Check for direct tool calls
    for cmd in plugin_commands:
        if cmd in user_message.lower():
            return True
    
    # Check for quick commands
    quick_triggers = [
        "list documents",
        "show documents",
        "document list", 
        "rabbit hole status",
        "memory status"
    ]
    
    for trigger in quick_triggers:
        if trigger in user_message.lower():
            return True
    
    return False

# =============================================================================
# TOOLS (CORRETTI)
# =============================================================================

@tool(return_direct=True)
def test_plugin_loaded(tool_input, cat):
    """Simple test to verify the plugin is loaded and working.
    Input: any test message.
    """
    return f"✅ Document Manager Plugin v{__version__} is loaded and working! Input was: {tool_input}"

@tool(return_direct=True)
def debug_document_payload(tool_input, cat):
    """Debug tool to examine document payload structure in detail.
    Input: any test message.
    """
    
    output = "🔧 **Document Payload Debug Report**\n\n"
    
    try:
        # Get first few documents
        points = _enumerate_points(cat, limit=3)
        
        if not points:
            return "❌ No documents found in memory."
        
        output += f"Found {len(points)} documents. Analyzing first 3:\n\n"
        
        for i, point in enumerate(points):
            output += f"📄 **Document {i+1}:**\n"
            output += f"   Type: {type(point)}\n"
            
            if hasattr(point, 'id'):
                output += f"   ID: {point.id}\n"
            
            if hasattr(point, 'payload'):
                payload = point.payload
                output += f"   Payload type: {type(payload)}\n"
                
                if isinstance(payload, dict):
                    output += f"   Payload keys: {list(payload.keys())}\n"
                    
                    # Show important fields
                    for key, value in payload.items():
                        if key == 'page_content':
                            preview = value[:100] if isinstance(value, str) and len(value) > 100 else value
                            output += f"      {key}: '{preview}{'...' if isinstance(value, str) and len(value) > 100 else ''}'\n"
                        elif key == 'metadata' and isinstance(value, dict):
                            output += f"      metadata: {value}\n"
                        else:
                            output += f"      {key}: {value} (type: {type(value).__name__})\n"
                else:
                    output += f"   Payload: {payload}\n"
            
            output += "\n"
        
        return output
        
    except Exception as e:
        return f"❌ **Debug Error:** {e}"

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
        # Use robust enumeration method
        if query_filter and query_filter.strip():
            # If there's a filter, use search
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
            # List all documents using robust enumeration
            try:
                all_points = _enumerate_points(cat, limit=max_docs)
                
                if not all_points:
                    return "📄 No documents found in rabbit hole. Try uploading some documents first!"
                
                documents = []
                for point in all_points:
                    doc_info = get_document_metadata_robust(point)
                    
                    if show_preview:
                        # Handle Record format for content
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
                return f"❌ Error accessing documents: {str(e)}\n\nTry: `inspect_document_structure` to diagnose the issue."
        
        # Add management information
        output += "\n💡 **Available commands:**\n"
        output += "- `remove_document <filename>` - Remove specific document\n"
        output += "- `clear_rabbit_hole CONFIRM` - Empty the entire rabbit hole\n"
        output += "- `document_stats` - Detailed statistics\n"
        output += "- `debug_memory_access` - Debug memory access methods\n"
        output += "- `inspect_document_structure` - Inspect document structure\n"
        
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
                
            # Get source from robust method
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
        # Basic statistics
        stats = {
            "total_documents": 0,
            "total_chunks": 0,
            "sources": {},
            "upload_dates": [],
            "total_characters": 0
        }
        
        try:
            # Get all points using robust method
            all_points = _enumerate_points(cat, limit=1000)
            
            for point in all_points:
                doc_info = get_document_metadata_robust(point)
                source = doc_info["source"]
                
                # Get content length
                content_length = doc_info["page_content_length"]
                when = doc_info["when"]
                
                # Count sources
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
            # Filter out non-numeric dates before min/max
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
            
            # Sort by number of chunks (descending)
            sorted_sources = sorted(
                stats["sources"].items(), 
                key=lambda x: x[1]["chunks"], 
                reverse=True
            )
            
            for source, info in sorted_sources[:10]:  # Top 10
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
        output += "💡 **Recommendations:**\n"
        # Fix type comparison error
        if isinstance(stats["total_chunks"], int) and stats["total_chunks"] > 1000:
            output += "- Consider removing old documents to improve performance\n"
        elif stats["total_documents"] == 0:
            output += "- Rabbit hole is empty. Upload some documents to get started!\n"
        
        return output
        
    except Exception as e:
        log.error(f"Error in document_stats: {e}")
        return f"❌ Error calculating statistics: {str(e)}"

@tool(return_direct=True)
def debug_memory_access(test_input, cat):
    """Debug tool to test different memory access methods.
    Input: any test message.
    """
    
    output = "🔧 **Memory Access Debug Report**\n\n"
    
    try:
        memory = cat.memory.vectors.declarative
        output += f"✅ Memory object available: {type(memory)}\n\n"
        
        # Test Method 0: get_all_points - THE ONE THAT WORKS!
        if hasattr(memory, "get_all_points"):
            try:
                points = memory.get_all_points()
                if isinstance(points, tuple):
                    points_list = points[0] if len(points) > 0 else []
                    output += f"✅ get_all_points: SUCCESS - Found {len(points_list)} points (tuple format)\n"
                else:
                    output += f"✅ get_all_points: SUCCESS - Found {len(points)} points\n"
            except Exception as e:
                output += f"❌ get_all_points: FAILED - {e}\n"
        else:
            output += f"❌ get_all_points: NOT AVAILABLE\n"
        
        # Test search methods
        search_methods = ['search', 'query', 'similarity_search', 'search_points']
        for method_name in search_methods:
            if hasattr(memory, method_name):
                try:
                    method = getattr(memory, method_name)
                    results = method("test", k=5, threshold=0.5) if 'threshold' in str(method) else method("test", k=5)
                    output += f"✅ {method_name}: SUCCESS - Found {len(results)} results\n"
                except Exception as e:
                    output += f"❌ {method_name}: FAILED - {e}\n"
            else:
                output += f"❌ {method_name}: NOT AVAILABLE\n"
        
        # Test count method
        if hasattr(memory, "count"):
            try:
                count = memory.count()
                output += f"✅ count: SUCCESS - Total documents: {count}\n"
            except Exception as e:
                output += f"❌ count: FAILED - {e}\n"
        
        # Available methods
        output += f"\n📋 **Available methods on memory object:**\n"
        available_methods = [method for method in dir(memory) if not method.startswith('_')]
        for method in sorted(available_methods)[:20]:  # Show first 20
            output += f"- {method}\n"
        
        if len(available_methods) > 20:
            output += f"... and {len(available_methods) - 20} more methods\n"
        
        return output
        
    except Exception as e:
        return f"❌ **Critical Error:** Cannot access memory object - {e}"

@tool(return_direct=True)
def inspect_document_structure(test_input, cat):
    """Inspect the exact structure of document metadata in memory.
    Input: any test message.
    """
    
    output = "🔍 **Document Structure Analysis**\n\n"
    
    try:
        memory = cat.memory.vectors.declarative
        raw_points = memory.get_all_points()
        
        output += f"Raw result type: {type(raw_points)}\n"
        output += f"Raw result length: {len(raw_points) if hasattr(raw_points, '__len__') else 'no length'}\n\n"
        
        if not raw_points:
            return "❌ No documents found in memory."
        
        # Show raw structure first
        output += "📋 **Raw Structure Analysis:**\n"
        if isinstance(raw_points, tuple):
            for i, item in enumerate(raw_points):
                output += f"   Tuple item {i}: Type={type(item)}, Value preview={str(item)[:100]}...\n"
        else:
            for i, item in enumerate(raw_points[:5]):  # Show first 5 raw items
                output += f"   Item {i}: Type={type(item)}, Value preview={str(item)[:100]}...\n"
        
        output += "\n"
        
        # Now try to extract proper points using our robust method
        try:
            valid_points = _enumerate_points(cat, limit=5)
            output += f"✅ **After processing:** Found {len(valid_points)} valid points\n\n"
            
            # Analyze first few valid points in detail
            for i, point in enumerate(valid_points[:2]):  # Show first 2 valid points
                output += f"📄 **Valid Document Point {i+1}:**\n"
                output += f"   Type: {type(point)}\n"
                
                # List all attributes
                attrs = [attr for attr in dir(point) if not attr.startswith('_')]
                output += f"   Attributes: {attrs}\n"
                
                # Check for ID
                if hasattr(point, 'id'):
                    output += f"   ID: {point.id}\n"
                
                # Check for payload
                if hasattr(point, 'payload'):
                    payload = point.payload
                    output += f"   Payload type: {type(payload)}\n"
                    if isinstance(payload, dict):
                        output += f"   Payload keys: {list(payload.keys())}\n"
                        
                        # Show important key values
                        for key, value in payload.items():
                            if isinstance(value, str):
                                preview = value[:100] if len(value) > 100 else value
                                output += f"      {key}: '{preview}{'...' if len(value) > 100 else ''}'\n"
                            else:
                                output += f"      {key}: {value} (type: {type(value).__name__})\n"
                    else:
                        output += f"   Payload: {payload}\n"
                
                output += "\n"
                
        except Exception as e:
            output += f"❌ Error processing points: {e}\n"
        
        return output
        
    except Exception as e:
        return f"❌ **Analysis Error:** {e}"

@tool(return_direct=True)
def document_manager_help(topic, cat):
    """Guide to using the Document Manager Plugin.
    Input: 'commands' for command list, 'examples' for examples, 'api' for API info.
    """
    
    topic = topic.lower() if topic else "general"
    
    if topic == "commands":
        return """🛠️ **Document Manager Commands**

**Viewing:**
- `list_rabbit_hole_documents` - List all documents
- `list_rabbit_hole_documents <filter>` - Search specific documents
- `document_stats basic` - Basic statistics
- `document_stats detailed` - Detailed statistics

**Management:**
- `remove_document <filename>` - Remove specific document
- `clear_rabbit_hole CONFIRM` - Empty rabbit hole (irreversible!)

**Testing:**
- `test_plugin_loaded <message>` - Test plugin status
- `debug_document_payload <message>` - Debug document structure
- `debug_memory_access <message>` - Debug memory access methods
- `inspect_document_structure <message>` - Inspect document structure

**Help:**
- `document_manager_help examples` - Usage examples
- `document_manager_help prompts` - Prompt system information"""

    elif topic == "examples":
        return """💡 **Document Manager Usage Examples**

**View all documents:**
```
list_rabbit_hole_documents
```

**Search for specific documents:**
```
list_rabbit_hole_documents user manual
list_rabbit_hole_documents report 2024
```

**Remove a document:**
```
remove_document user_manual.pdf
```

**Quick statistics:**
```
document_stats basic
```

**Completely empty rabbit hole:**
```
clear_rabbit_hole CONFIRM
```

**Debug commands:**
```
debug_document_payload test
debug_memory_access test
inspect_document_structure test
```

**Quick commands:**
- "list documents" → Automatic list
- "rabbit hole status" → Quick statistics"""

    else:
        return """📚 **Document Manager Plugin v1.1.1**

This plugin allows you to manage documents uploaded to the Cheshire Cat's rabbit hole.

**Main features:**
✅ View all uploaded documents
✅ Search for specific documents
✅ Remove individual or all documents
✅ Detailed memory statistics
✅ Robust memory access across different Cat versions
✅ **Automatic prompt switching** for consistent responses

**🎭 Smart Prompt System:**
The plugin automatically uses standardized English prompts for document commands, ensuring professional responses regardless of your custom Cat personality.

**Getting started:**
- `document_manager_help commands` - Command list
- `list_rabbit_hole_documents` - View current documents
- `document_stats basic` - Quick statistics
- `test_plugin_loaded test` - Test plugin status

**Configuration:**
The plugin auto-configures itself. For advanced modifications, access plugin settings."""

# =============================================================================
# HOOKS (CORRETTI)
# =============================================================================

@hook(priority=100)  # Maximum priority to override any other prompt
def agent_prompt_prefix(prefix, cat):
    """Completely override system prompt for plugin commands with maximum priority."""
    
    user_message = cat.working_memory.user_message_json.text.lower()
    
    # Check if this is a plugin command
    if is_plugin_command(user_message):
        log.info(f"✅ MAXIMUM PRIORITY PROMPT OVERRIDE for: {user_message}")
        
        # Return a completely new prompt that ignores any custom personality
        return (
            "You are the **Document Manager Assistant**.\n"
            "Respond in clear, professional English only.\n"
            "If a tool was called, present its results directly without elaboration.\n"
            "Do not use historical language, elaborate prose, or personal commentary.\n"
            "Focus only on the document management task requested."
        )
    
    # For non-plugin commands, use original prompt
    return prefix

@hook(priority=10)
def agent_fast_reply(fast_reply, cat):
    """Fast reply for plugin commands - complete LLM bypass."""
    
    msg = cat.working_memory.user_message_json.get("text", "").strip()
    if not msg:
        return fast_reply
    
    msg_lower = msg.lower()
    
    # Only handle commands that work reliably in fast_reply
    
    if msg_lower.startswith("test_plugin_loaded"):
        parts = msg.split(maxsplit=1)
        test_input = parts[1] if len(parts) > 1 else ""
        fast_reply["output"] = test_plugin_loaded(test_input, cat)
        log.info(f"🚀 FAST REPLY: test_plugin_loaded")
        return fast_reply
    
    if msg_lower.startswith("debug_document_payload"):
        parts = msg.split(maxsplit=1)
        test_input = parts[1] if len(parts) > 1 else ""
        fast_reply["output"] = debug_document_payload(test_input, cat)
        log.info(f"🚀 FAST REPLY: debug_document_payload")
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