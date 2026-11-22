from __future__ import annotations

from typing import Any

from config.settings import GEMINI_API_KEY
from services.gemini_service import GeminiService, GeminiServiceError
from services.rag_service import retrieve_evidence


def normalize_for_search(question: str, language: str) -> str:
    if language == "English" or not GEMINI_API_KEY:
        return question
    return GeminiService().translate_for_search(question, language)


def combine_retrievals(results: list[dict[str, Any]]) -> dict[str, Any]:
    evidence: list[dict] = []
    failures: list[str] = []
    indexed_count = 0
    live_count = 0
    by_key: dict[tuple[str, int | None], dict] = {}
    for result in results:
        failures.extend(result.get("failures", []))
        indexed_count += int(result.get("indexed_count", 0))
        live_count += int(result.get("live_pages_retrieved", 0))
        for chunk in result.get("evidence", []):
            key = (chunk.get("url", ""), chunk.get("page"))
            if key not in by_key:
                by_key[key] = {**chunk, "queries": []}
                evidence.append(by_key[key])
            elif chunk.get("score", 0) > by_key[key].get("score", 0):
                retrieved_this_search = by_key[key].get("retrieved_this_search", False) or chunk.get("retrieved_this_search", False)
                by_key[key].update({k: v for k, v in chunk.items() if k != "citation_id"})
                by_key[key]["retrieved_this_search"] = retrieved_this_search
            elif chunk.get("retrieved_this_search"):
                by_key[key]["retrieved_this_search"] = True
            query = result.get("query")
            if query and query not in by_key[key]["queries"]:
                by_key[key]["queries"].append(query)

    citations = []
    ids_by_key = {}
    for chunk in evidence:
        key = (chunk.get("url", ""), chunk.get("page"))
        citation = {
            "id": f"S{len(citations) + 1}",
            "title": chunk.get("title") or chunk.get("source_name") or "Official government page",
            "source_name": chunk.get("source_name") or "Official government source",
            "url": chunk["url"],
            "page": chunk.get("page"),
            "publication_date": chunk.get("publication_date"),
            "retrieved_at": chunk.get("retrieved_at"),
            "retrieved_this_search": chunk.get("retrieved_this_search", False),
            "document_truncated": chunk.get("document_truncated", False),
            "pages_total": chunk.get("pages_total"),
        }
        citations.append(citation)
        ids_by_key[key] = citation["id"]
    for chunk in evidence:
        chunk["citation_id"] = ids_by_key[(chunk.get("url", ""), chunk.get("page"))]
    backends = {item.get("vector_backend") for item in results if item.get("vector_backend")}
    return {
        "evidence": evidence,
        "citations": citations,
        "failures": list(dict.fromkeys(failures)),
        "indexed_count": indexed_count,
        "live_pages_retrieved": live_count,
        "vector_backend": ", ".join(sorted(backends)) or "unavailable",
    }


def retrieve_for_queries(queries: list[str], language: str = "English") -> dict[str, Any]:
    results = []
    for question in queries:
        search_query = normalize_for_search(question, language)
        result = retrieve_evidence(search_query)
        result["query"] = search_query
        results.append(result)
    return combine_retrievals(results)


def assess_scheme_eligibility(scheme: str, profile: dict, language: str) -> dict[str, Any]:
    query = f"{scheme} official eligibility criteria conditions documents"
    retrieval = retrieve_for_queries([query], "English")
    if not retrieval["evidence"]:
        assessment = {
            "result": "More information required", "satisfied": [], "not_satisfied": [], "missing": [],
            "explanation": "No relevant official eligibility evidence was retrieved. Check the linked government portal or try another scheme name.",
            "source_ids": [],
        }
        return {"assessment": assessment, "retrieval": retrieval}
    if not GEMINI_API_KEY:
        raise GeminiServiceError("Official eligibility sources were retrieved, but no automated assessment was made. Add GEMINI_API_KEY to compare the criteria with your details.")
    assessment = GeminiService().assess_eligibility(scheme, profile, retrieval["evidence"], retrieval["citations"], language)
    return {"assessment": assessment, "retrieval": retrieval}


def compare_schemes(schemes: list[str], language: str) -> dict[str, Any]:
    queries = [f"{scheme} official scheme benefits eligibility required documents application process" for scheme in schemes]
    retrieval = retrieve_for_queries(queries, language)
    if not retrieval["evidence"]:
        return {"comparison": {"summary": "No relevant official evidence was retrieved for the selected schemes.", "schemes": []}, "retrieval": retrieval}
    if not GEMINI_API_KEY:
        return {"comparison": {
            "summary": "Official source excerpts are available below. Add GEMINI_API_KEY to generate a structured comparison; no scheme facts have been inferred here.",
            "schemes": [],
        }, "retrieval": retrieval}
    comparison = GeminiService().compare_schemes(schemes, retrieval["evidence"], retrieval["citations"], language)
    return {"comparison": comparison, "retrieval": retrieval}


def run_scheme_agent(request: str, language: str) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        raise GeminiServiceError("Add GEMINI_API_KEY to run the multi-step scheme research agent.")
    service = GeminiService()
    plan = service.plan_agent_search(request, language)
    retrieval = retrieve_for_queries(plan["search_queries"], language)
    if not retrieval["evidence"]:
        return {"plan": plan, "retrieval": retrieval, "answer": "No relevant evidence was retrieved from the approved official sources. The agent did not make a scheme recommendation."}
    answer = service.agent_answer(request, language, retrieval["evidence"], retrieval["citations"])
    return {"plan": plan, "retrieval": retrieval, "answer": answer}
