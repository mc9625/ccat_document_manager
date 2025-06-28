"""
Document Manager Plugin for Cheshire Cat AI – VERSIONE FINALE CON UI NATIVA
File: ccat_document_manager.py

Gestisce in modo sicuro la visualizzazione e la rimozione dei documenti
(chunks) memorizzati nella Rabbit Hole. Compatibile con Cheshire Cat AI ≥ v1.4.x

Ultimo aggiornamento: 28 Giugno 2025 — detection dei file caricati migliorata
e nuovo tool `list_uploaded_files`.
"""

from __future__ import annotations

import re
from pathlib import Path
from unicodedata import normalize
import json
import os
import time
import inspect

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from cat.auth.permissions import check_permissions
from cat.log import log
from cat.mad_hatter.decorators import endpoint, hook, plugin, tool
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------- #
# PLUGIN INFO
# ---------------------------------------------------------------------------- #

__version__ = "1.3.0"

# ---------------------------------------------------------------------------- #
# SETTINGS MODEL
# ---------------------------------------------------------------------------- #


class DocumentManagerSettings(BaseModel):
    """Configurable options exposed in the Admin UI."""

    max_documents_per_page: int = Field(
        20,
        ge=5,
        le=100,
        title="Documents per page",
        description="Maximum number of *files* shown by default in CLI tools",
    )
    show_document_preview: bool = Field(
        True,
        title="Document preview",
        description="Show a text preview for each chunk/file when available",
    )
    preview_length: int = Field(
        200,
        ge=50,
        le=500,
        title="Preview length",
        description="Characters included in each preview snippet",
    )


@plugin
def settings_model():  # 🐈‍⬛ Mad-Hatter hook
    """Return the settings schema for the plugin."""
    return DocumentManagerSettings


# ---------------------------------------------------------------------------- #
# LOW-LEVEL MEMORY UTILITIES
# ---------------------------------------------------------------------------- #


def _enumerate_points(cat, limit: int | None = 1000):
    """
    Return up to <limit> points (chunks) from declarative memory.
    If *limit* is **None**, no slicing is applied – the caller must handle it.
    """
    coll = cat.memory.vectors.declarative

    # Preferred: get_all_points ------------------------------------------------
    if hasattr(coll, "get_all_points"):
        try:
            raw = coll.get_all_points()
            points = raw[0] if isinstance(raw, tuple) else raw
            if isinstance(points, list):
                pts = [p for p in points if p is not None]
                return pts if limit is None else pts[:limit]
        except Exception as e:  # noqa: BLE001
            log.debug(f"get_all_points failed: {e}")

    # Fallback: scroll_points --------------------------------------------------
    if hasattr(coll, "scroll_points"):
        try:
            pts, _ = coll.scroll_points(limit=limit or 10_000)
            return pts if limit is None else pts[:limit]
        except Exception as e:  # noqa: BLE001
            log.debug(f"scroll_points failed: {e}")

    raise RuntimeError("No compatible vector-DB enumeration method found.")


def _search_points(cat, query: str, k: int = 50, threshold: float = 0.3):
    """
    Robust search: tenta vari backend; se il risultato di un metodo è *vuoto*
    passa al successivo, e in ultima istanza usa il filtro substring.
    """
    coll = cat.memory.vectors.declarative
    methods = [
        ("search", lambda: coll.search(query, k=k, threshold=threshold)),
        ("query", lambda: coll.query(query, k=k, threshold=threshold)),
        ("similarity_search", lambda: coll.similarity_search(query, k=k)),
        ("search_points", lambda: coll.search_points(query, k=k, threshold=threshold)),
    ]

    for name, fn in methods:
        if hasattr(coll, name):
            try:
                res = fn()
                if res:                       # ✅ usa solo se NON vuoto
                    log.debug(f"Used {name}: {len(res)} results")
                    return res
                log.debug(f"{name} returned 0 results, trying next")
            except Exception as e:            # metodo fallito ⇒ continua
                log.debug(f"{name} failed: {e}")

    # ---- Fallback substring --------------------------------------------------
    log.debug("Fallback to manual substring filter")
    hits = []
    q = query.lower()
    for p in _enumerate_points(cat, limit=10000):
        pl = getattr(p, "payload", {})
        if not isinstance(pl, dict):
            continue
        if q in pl.get("source", "").lower() or q in pl.get("page_content", "").lower():
            hits.append((p, 0.8))
    return hits[:k]


# ---------------------------------------------------------------------------- #
# POINT → UNIFORM METADATA
# ---------------------------------------------------------------------------- #


def get_document_metadata_robust(doc_point) -> Dict[str, Any]:
    """Extract uniform metadata from heterogeneous record formats."""
    if hasattr(doc_point, "id") and hasattr(doc_point, "payload"):
        payload = doc_point.payload or {}
    elif isinstance(doc_point, dict):
        payload = doc_point
    else:
        payload = {}

    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    page_content = payload.get("page_content", "")
    point_id = str(getattr(doc_point, "id", metadata.get("id", "unknown")))

    # Filename / source detection ---------------------------------------------
    src_fields = [
        "source",
        "original_filename",
        "file_name",
        "filename",
        "name",
        "title",
        "path",
        "filepath",
    ]
    source = next((metadata.get(f) for f in src_fields if metadata.get(f)), None)
    if not source:
        source = next((payload.get(f) for f in src_fields if payload.get(f)), "Unknown")

    # Timestamp extraction -----------------------------------------------------
    when_fields = ["when", "timestamp", "created_at", "upload_time"]
    ts = next(
        (
            float(metadata[f])
            for f in when_fields
            if metadata.get(f) not in (None, "")
        ),
        time.time(),
    )

    return {
        "id": point_id,
        "source": str(source),
        "when": ts,
        "upload_date": datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M"),
        "page_content_length": len(page_content),
        "chunk_index": metadata.get("chunk_index", 0),
        "total_chunks": metadata.get("total_chunks", 1),
    }


# ---------------------------------------------------------------------------- #
# HIGH-LEVEL HELPERS
# ---------------------------------------------------------------------------- #


def _list_unique_documents(cat, filter_text: str | None = None) -> List[Dict]:
    """Aggregate chunks → files; optionally filter by substring in filename."""
    docs: Dict[str, Dict] = {}
    for pt in _enumerate_points(cat, limit=None):
        meta = get_document_metadata_robust(pt)
        src = meta["source"]
        if filter_text and filter_text.lower() not in src.lower():
            continue
        d = docs.setdefault(src, {"chunks": 0, "when": meta["when"]})
        d["chunks"] += 1
        d["when"] = max(d["when"], meta["when"])
    return sorted(
        ({"source": s, **v} for s, v in docs.items()),
        key=lambda x: x["when"],
        reverse=True,
    )


def format_document_list(
    documents: List[Dict], show_preview: bool = True, preview_length: int = 200
) -> str:
    """Nicely format a list of chunk-level documents."""
    if not documents:
        return "📄 No documents found in Rabbit Hole."

    out = f"📚 **Documents in Rabbit Hole** ({len(documents)} found)\n\n"
    by_src: Dict[str, List[Dict]] = {}
    for d in documents:
        by_src.setdefault(d["source"], []).append(d)

    for src, rows in by_src.items():
        out += f"📁 **{src}** ({len(rows)} chunks)\n"
        for r in rows[:10]:
            out += (
                f"   └─ Chunk {r['chunk_index']}/{r['total_chunks']} "
                f"({r['page_content_length']} chars) – {r['upload_date']}\n"
            )
            if show_preview and r.get("preview"):
                out += f"      *{r['preview']}…*\n"
        if len(rows) > 10:
            out += f"   └─ …and {len(rows) - 10} more chunks\n"
        out += "\n"
    return out


def _read_static_file(filename: str) -> str:
    """Load a static asset shipped with the plugin."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, filename), encoding="utf-8") as fp:
            return fp.read()
    except Exception as e:  # noqa: BLE001
        log.error(f"Error reading '{filename}': {e}")
        return f"/* Error loading {filename}: {e} */"

def _normalize(txt: str) -> str:
    """Ascii-only, lowercase, no extension, no fancy quotes."""
    # normalizza unicode → NFKD, elimina segni diacritici
    txt = normalize("NFKD", txt).encode("ascii", "ignore").decode()
    # remove quotes, spaces at ends
    txt = txt.strip().strip("\"'“”‘’")
    # togli estensione se presente
    txt = str(Path(txt).with_suffix(""))
    return txt.lower()

def _delete_points_by_source(cat, filename: str) -> int:
    """
    Cancella tutti i chunk il cui metadata contiene la stringa *filename*
    (case-insensitive, estensione opzionale).
    """
    query_norm = _normalize(filename)

    # 1) raccogli tutti i punti
    matches = []
    for p in _enumerate_points(cat, limit=None):
        meta = getattr(p, "payload", {}).get("metadata", {}) or {}
        fields = [
            meta.get("source"),
            meta.get("file_name"),
            meta.get("filename"),
            meta.get("name"),
            meta.get("title"),
            meta.get("path"),
            meta.get("filepath"),
        ]
        for f in filter(None, fields):
            if query_norm in _normalize(str(f)):
                matches.append(p)
                break

    if not matches:
        return 0

    # 2) estrai gli ID
    ids = [getattr(p, "id") for p in matches if getattr(p, "id", None)]
    if not ids:
        return 0

    # 3) cancella – prova a capire la firma in modo dinamico
    coll = cat.memory.vectors.declarative
    try:
        if hasattr(coll, "delete_points"):
            sig = inspect.signature(coll.delete_points).parameters
            if "ids" in sig:
                coll.delete_points(ids=ids)           # Qdrant
            elif "point_ids" in sig:
                coll.delete_points(point_ids=ids)     # Milvus
            elif len(sig) == 1:
                coll.delete_points(ids)               # posizionale
            else:
                raise TypeError("Unknown delete_points signature")
        elif hasattr(coll, "delete_points_by_ids"):
            coll.delete_points_by_ids(ids)            # altro alias
        elif hasattr(coll, "delete"):
            coll.delete(ids)                          # some libs
        else:
            # fallback: elimina uno per volta
            for pid in ids:
                coll.delete_point(pid)
    except Exception as e:
        log.error(f"delete_points failed: {e}")
        raise


# ---------------------------------------------------------------------------- #
# WEB ASSETS ENDPOINTS
# ---------------------------------------------------------------------------- #


@endpoint.get("/document/style.css")
def css_file(_: str = check_permissions("MEMORY", "READ")):
    return Response(_read_static_file("document_manager.css"), media_type="text/css")


@endpoint.get("/document/script.js")
def js_file(_: str = check_permissions("MEMORY", "READ")):
    return Response(
        _read_static_file("document_manager.js"), media_type="application/javascript"
    )


@endpoint.get("/document")
def html_app(_: str = check_permissions("MEMORY", "READ")):
    return HTMLResponse(_read_static_file("document_manager.html"))


# ---------------------------------------------------------------------------- #
# API ENDPOINTS
# ---------------------------------------------------------------------------- #


@endpoint.get("/document/api/documents")
def api_documents(
    filter: str = "",  # noqa: A002
    stray: str = check_permissions("MEMORY", "READ"),
):
    """Return raw list + stats for programmatic use."""
    try:
        settings = stray.mad_hatter.get_plugin().load_settings()
        max_docs = settings.get("max_documents_per_page", 100)
        show_prev = settings.get("show_document_preview", True)
        prev_len = settings.get("preview_length", 200)

        if filter.strip():
            sr = _search_points(stray, filter, k=max_docs, threshold=0.3)
            points = [t[0] if isinstance(t, tuple) else t for t in sr]
        else:
            points = _enumerate_points(stray, limit=max_docs)

        docs = []
        for p in points:
            info = get_document_metadata_robust(p)
            if show_prev and hasattr(p, "payload") and isinstance(p.payload, dict):
                info["preview"] = p.payload.get("page_content", "")[:prev_len]
            docs.append(info)

        total_chars = sum(d["page_content_length"] for d in docs)
        last_ts = max((d["when"] for d in docs), default=None)
        return {
            "success": True,
            "documents": docs,
            "stats": {
                "total_documents": len({d['source'] for d in docs}),
                "total_chunks": len(docs),
                "total_characters": total_chars,
                "last_update": datetime.fromtimestamp(last_ts).isoformat()
                if last_ts
                else None,
            },
        }
    except Exception as e:  # noqa: BLE001
        log.error(f"api_documents error: {e}")
        return {"success": False, "error": str(e)}


@endpoint.get("/document/api/stats")
def api_stats(stray: str = check_permissions("MEMORY", "READ")):
    """Detailed aggregate statistics."""
    try:
        pts = _enumerate_points(stray, limit=1000)
        stats = {
            "total_documents": 0,
            "total_chunks": len(pts),
            "total_characters": 0,
            "sources": {},
            "upload_dates": [],
        }
        for p in pts:
            info = get_document_metadata_robust(p)
            src = info["source"]
            stats["sources"].setdefault(
                src, {"chunks": 0, "characters": 0, "upload_date": info["when"]}
            )
            stats["sources"][src]["chunks"] += 1
            stats["sources"][src]["characters"] += info["page_content_length"]
            stats["sources"][src]["upload_date"] = max(
                stats["sources"][src]["upload_date"], info["when"]
            )
            stats["total_characters"] += info["page_content_length"]
            stats["upload_dates"].append(info["when"])

        stats["total_documents"] = len(stats["sources"])
        stats["memory_usage"] = f"{(stats['total_characters'] * 2)/(1024*1024):.1f} MB"
        stats["last_update"] = (
            datetime.fromtimestamp(max(stats["upload_dates"])).strftime(
                "%d/%m/%Y %H:%M"
            )
            if stats["upload_dates"]
            else "Never"
        )
        return {"success": True, **stats}
    except Exception as e:  # noqa: BLE001
        log.error(f"api_stats error: {e}")
        return {"success": False, "error": str(e)}


@endpoint.post("/document/api/remove")
def api_remove(req: dict, stray: str = check_permissions("MEMORY", "DELETE")):
    filename = (req or {}).get("source", "").strip()
    if not filename:
        return {"success": False, "message": "Source parameter is required"}

    try:
        removed = _delete_points_by_source(stray, filename)
        if removed == 0:
            return {"success": False, "message": f"Document '{filename}' not found"}
        return {
            "success": True,
            "message": f"Document '{filename}' removed ({removed} chunks)",
        }
    except Exception as e:
        log.error(f"api_remove error: {e}")
        return {"success": False, "message": str(e)}


@endpoint.post("/document/api/clear")
def api_clear_all(stray: str = check_permissions("MEMORY", "DELETE")):
    """Delete every chunk in the Rabbit Hole."""
    try:
        memory = stray.memory.vectors.declarative
        count_before = len(_enumerate_points(stray, limit=10000))
        memory.delete_points_by_metadata_filter({})
        return {
            "success": True,
            "message": f"All documents cleared ({count_before} chunks deleted)",
        }
    except Exception as e:  # noqa: BLE001
        log.error(f"api_clear_all error: {e}")
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------- #
# CLI TOOLS
# ---------------------------------------------------------------------------- #


@tool(return_direct=True)
def test_plugin_loaded(tool_input, cat):
    """Quick sanity check."""
    return (
        f"✅ Document Manager Plugin v{__version__} is loaded and working! "
        f"Input was: {tool_input}"
    )


@tool(return_direct=True)
def list_uploaded_files(filter_text, cat):
    """List all uploaded files (optionally filtered by substring)."""
    filter_text = (filter_text or "").strip()      # ← normalizza
    docs = _list_unique_documents(cat, filter_text or None)
    if not docs:
        return "📂 No matching documents."
    out = f"📄 **{len(docs)} document(s) in Rabbit Hole**\n\n"
    for d in docs:
        date = datetime.fromtimestamp(d["when"]).strftime("%d/%m/%Y %H:%M")
        out += f"• **{d['source']}** – {d['chunks']} chunks – {date}\n"
    return out


@tool(return_direct=True)
def list_rabbit_hole_documents(query_filter, cat):
    """
    Legacy chunk-level listing kept for backward compatibility.

        list_rabbit_hole_documents [substring]
    """
    cfg = cat.mad_hatter.get_plugin().load_settings()
    max_docs = cfg.get("max_documents_per_page", 20)
    show_prev = cfg.get("show_document_preview", True)
    prev_len = cfg.get("preview_length", 200)
    query_filter = (query_filter or "").strip()

    try:
        if query_filter:
            pts = [
                t[0] if isinstance(t, tuple) else t
                for t in _search_points(cat, query_filter, k=max_docs, threshold=0.3)
            ]
            if not pts:
                return f"🔍 No documents found for '{query_filter}'."
        else:
            # Take every chunk, dedup later
            pts = _enumerate_points(cat, limit=None)

        docs: List[Dict] = []
        for p in pts:
            info = get_document_metadata_robust(p)
            if show_prev and hasattr(p, "payload") and isinstance(p.payload, dict):
                info["preview"] = p.payload.get("page_content", "")[:prev_len]
            docs.append(info)

        # deduplicate on (source, chunk_index) ---------------------------------
        seen = set()
        unique = []
        for d in docs:
            key = (d["source"], d["chunk_index"])
            if key not in seen:
                seen.add(key)
                unique.append(d)

        unique.sort(key=lambda x: x["when"], reverse=True)
        header = (
            f"🔍 **Search results for '{query_filter}'**\n\n"
            if query_filter
            else ""
        )
        out = header + format_document_list(unique[:max_docs], show_prev, prev_len)
        out += (
            "\n💡 **Available commands:**\n"
            "- `list_uploaded_files` – list sources only\n"
            "- `remove_document <filename>` – delete\n"
            "- `clear_rabbit_hole CONFIRM` – wipe all\n"
            "- `document_stats` – statistics\n"
            "- Open web UI: `/custom/document`\n"
        )
        return out
    except Exception as e:  # noqa: BLE001
        log.error(f"list_rabbit_hole_documents error: {e}")
        return f"❌ Error: {e}"


@tool(return_direct=True)
def remove_document(document_source, cat):
    """Remove every chunk belonging to the specified filename."""
    if not document_source or not document_source.strip():
        return "❌ Please specify the document name to remove."

    filename = document_source.strip()
    try:
        removed = _delete_points_by_source(cat, filename)
        if removed == 0:
            return f"❌ Document '{filename}' not found."
        cat.send_notification(f"🗑️ Document removed: {filename}")
        return f"✅ Removed {removed} chunks of '{filename}'."
    except Exception as e:  # noqa: BLE001
        log.error(f"remove_document error: {e}")
        return f"❌ Error: {e}"


@tool(return_direct=True)
def clear_rabbit_hole(confirmation, cat):
    """
    Delete **ALL** chunks. Usage: clear_rabbit_hole CONFIRM
    """
    if confirmation != "CONFIRM":
        return (
            "⚠️ **WARNING**: This will delete *all* documents!\n\n"
            "To confirm, use: `clear_rabbit_hole CONFIRM`"
        )

    try:
        memory = cat.memory.vectors.declarative
        count_before = len(_enumerate_points(cat, limit=10000))
        memory.delete_points_by_metadata_filter({})
        cat.send_notification("🧹 Rabbit Hole completely emptied.")
        return (
            f"✅ Rabbit Hole emptied ({count_before} chunks deleted) – "
            f"{datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
    except Exception as e:  # noqa: BLE001
        log.error(f"clear_rabbit_hole error: {e}")
        return f"❌ Error: {e}"


@tool(return_direct=True)
def document_stats(detail_level, cat):
    """
    Statistics summary:

        document_stats [basic|detailed]
    """
    detail_level = (detail_level or "basic").lower()

    try:
        pts = _enumerate_points(cat, limit=1000)
        stats = {
            "total_documents": 0,
            "total_chunks": len(pts),
            "total_characters": 0,
            "sources": {},
            "upload_dates": [],
        }
        for p in pts:
            info = get_document_metadata_robust(p)
            src = info["source"]
            src_info = stats["sources"].setdefault(
                src, {"chunks": 0, "characters": 0, "upload_date": info["when"]}
            )
            src_info["chunks"] += 1
            src_info["characters"] += info["page_content_length"]
            src_info["upload_date"] = max(src_info["upload_date"], info["when"])
            stats["total_characters"] += info["page_content_length"]
            stats["upload_dates"].append(info["when"])

        stats["total_documents"] = len(stats["sources"])

        out = "📊 **Rabbit Hole Statistics**\n\n"
        out += f"📁 **Total documents:** {stats['total_documents']}\n"
        out += f"🧩 **Total chunks:** {stats['total_chunks']}\n"
        out += f"📝 **Total characters:** {stats['total_characters']:,}\n"
        if stats["upload_dates"]:
            out += (
                f"📅 **Latest upload:** "
                f"{datetime.fromtimestamp(max(stats['upload_dates'])).strftime('%d/%m/%Y %H:%M')}\n"
            )
            out += (
                f"📅 **First upload:** "
                f"{datetime.fromtimestamp(min(stats['upload_dates'])).strftime('%d/%m/%Y %H:%M')}\n"
            )
        out += "\n"

        if detail_level == "detailed" and stats["sources"]:
            out += "📋 **Details per document:**\n\n"
            for src, info in sorted(
                stats["sources"].items(), key=lambda x: x[1]["chunks"], reverse=True
            )[:10]:
                avg = info["characters"] // info["chunks"]
                upload = datetime.fromtimestamp(info["upload_date"]).strftime(
                    "%d/%m/%Y"
                )
                out += (
                    f"📄 **{src}**\n"
                    f"   └─ {info['chunks']} chunks, {info['characters']:,} chars\n"
                    f"   └─ Avg chunk size: {avg} chars\n"
                    f"   └─ Uploaded: {upload}\n\n"
                )
            if len(stats["sources"]) > 10:
                out += f"...and {len(stats['sources']) - 10} more documents\n\n"

        out += (
            "💡 **Available actions:**\n"
            "- list_uploaded_files\n"
            "- remove_document <filename>\n"
            "- clear_rabbit_hole CONFIRM\n"
        )
        return out
    except Exception as e:  # noqa: BLE001
        log.error(f"document_stats error: {e}")
        return f"❌ Error: {e}"


# ---------------------------------------------------------------------------- #
# HOOKS & BOOTSTRAP
# ---------------------------------------------------------------------------- #


def _is_plugin_command(msg: str) -> bool:
    cmds = {
        "list_rabbit_hole_documents",
        "list_uploaded_files",
        "remove_document",
        "clear_rabbit_hole",
        "document_stats",
        "test_plugin_loaded",
    }
    quick = {
        "list documents",
        "show documents",
        "document list",
        "rabbit hole status",
        "memory status",
    }
    lower = msg.lower()
    return any(c in lower for c in cmds | quick)


@hook(priority=100)
def agent_prompt_prefix(prefix, cat):
    if _is_plugin_command(cat.working_memory.user_message_json.text):
        return (
            "You are the **Document Manager Assistant**.\n"
            "Answer concisely in professional English, outputting any tool "
            "result verbatim."
        )
    return prefix


@hook(priority=10)
def agent_fast_reply(fast_reply, cat):
    msg = cat.working_memory.user_message_json.get("text", "")
    if not msg:
        return fast_reply
    l = msg.lower()

    if l.startswith("test_plugin_loaded"):
        fast_reply["output"] = test_plugin_loaded(
            " ".join(msg.split()[1:]), cat
        )
        return fast_reply

    quick_map = {
        "list documents": lambda: list_uploaded_files("", cat),
        "show documents": lambda: list_uploaded_files("", cat),
        "document list": lambda: list_uploaded_files("", cat),
    }
    for trig, fn in quick_map.items():
        if trig in l:
            fast_reply["output"] = fn()
            return fast_reply

    return fast_reply


@hook
def after_cat_bootstrap(cat):
    log.info(f"📚 Document Manager Plugin v{__version__} initialised.")
    try:
        _ = cat.memory.vectors.declarative
        log.info("Memory access OK.")
    except Exception as e:  # noqa: BLE001
        log.error(f"Memory access failed: {e}")

    try:
        pl = cat.mad_hatter.get_plugin()
        if not pl.load_settings():
            pl.save_settings(DocumentManagerSettings().dict())
            log.info("Default settings written.")
    except Exception as e:  # noqa: BLE001
        log.error(f"Settings init error: {e}")
