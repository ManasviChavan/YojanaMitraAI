from __future__ import annotations

from typing import Any

from services.retrieval import active_backend, index_documents, retrieve_chunks
from services.sources import retrieve_official_sources


def retrieve_evidence(query: str, limit: int = 6) -> dict[str, Any]:
    """Run live approved-source retrieval, index only successful fetches, then search the FAISS corpus."""
    documents, failures = retrieve_official_sources(query)
    indexed_count = index_documents(documents) if documents else 0
    evidence = retrieve_chunks(query, k=limit)
    fresh_urls = {document["url"] for document in documents}

    citations = []
    seen = set()
    for chunk in evidence:
        chunk["retrieved_this_search"] = chunk["url"] in fresh_urls
        key = (chunk.get("url"), chunk.get("page"))
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "id": f"S{len(citations) + 1}",
            "title": chunk.get("title") or chunk.get("source_name") or "Official government page",
            "source_name": chunk.get("source_name") or "Official government source",
            "url": chunk["url"],
            "page": chunk.get("page"),
            "publication_date": chunk.get("publication_date"),
            "retrieved_at": chunk.get("retrieved_at"),
            "retrieved_this_search": chunk["url"] in fresh_urls,
            "document_truncated": chunk.get("document_truncated", False),
            "pages_total": chunk.get("pages_total"),
        })
    citation_ids = {(c["url"], c.get("page")): c["id"] for c in citations}
    for chunk in evidence:
        chunk["citation_id"] = citation_ids[(chunk.get("url"), chunk.get("page"))]
    return {
        "evidence": evidence,
        "citations": citations,
        "failures": failures,
        "indexed_count": indexed_count,
        "live_pages_retrieved": len(documents),
        "vector_backend": active_backend(),
    }
