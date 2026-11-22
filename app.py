from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

# Streamlit Community Cloud stores credentials in st.secrets rather than .env.
# Populate environment values before importing config.settings, which reads them.
try:
    for _secret_name in (
        "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FALLBACK_MODEL",
        "GEMINI_EMBEDDING_MODEL", "EMBEDDING_PROVIDER",
    ):
        _secret_value = st.secrets.get(_secret_name)
        if _secret_value is not None and not os.environ.get(_secret_name):
            os.environ[_secret_name] = str(_secret_value)
except Exception:
    # Local runs without Streamlit secrets continue to use the project's .env.
    pass

from config.settings import GEMINI_API_KEY
from database import dashboard_store
from services.feature_service import assess_scheme_eligibility, compare_schemes, normalize_for_search, run_scheme_agent
from services.gemini_service import GeminiService, GeminiServiceError
from services.languages import AUTO_DETECT, LANGUAGE_OPTIONS, LANGUAGES, language_code, normalize_detected_language
from services.rag_service import retrieve_evidence
from services.retrieval import RetrievalError, active_backend
from services.embedding_service import EmbeddingError
from services.voice_service import speech_controls_html

SERVICE_ERRORS = (GeminiServiceError, RetrievalError, EmbeddingError)


def voice_response_language(detected: str, selected: str) -> str:
    """Resolve speech language locally so hot reloads do not depend on stale imports."""
    return normalize_detected_language(detected) or (selected if selected in LANGUAGES else "English")


st.set_page_config(page_title="YojanaMitra AI", page_icon="🇮🇳", layout="wide")


def initialize_state() -> None:
    st.session_state.setdefault("guest_id", str(uuid.uuid4()))
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("last_answer", None)
    st.session_state.setdefault("transcript", None)
    st.session_state.setdefault("profile", {})
    guest = st.session_state.guest_id
    preferred = dashboard_store.get_preferred_language(guest)
    if "language" not in st.session_state:
        st.session_state.language = preferred if preferred in LANGUAGES else "English"


def show_sources(citations: list[dict], key: str) -> None:
    if not citations:
        st.info("No source citations were returned for this result.")
        return
    with st.expander(f"Official sources ({len(citations)})", expanded=True):
        for i, source in enumerate(citations):
            title = source.get("title") or source.get("source_name") or "Official source"
            st.markdown(f"**[{title}]({source['url']})**")
            st.caption(f"URL: {source['url']} · Retrieved: {source.get('retrieved_at') or 'date unavailable'}"
                       + (f" · PDF page {source['page']}" if source.get("page") else ""))
            if source.get("publication_date"):
                st.caption(f"Published: {source['publication_date']}")
            if st.button("Save source", key=f"{key}_save_{i}"):
                dashboard_store.save_scheme(st.session_state.guest_id, title, source["url"], source.get("retrieved_at") or datetime.now().astimezone().isoformat())
                st.success("Saved to your dashboard.")
            if i + 1 < len(citations):
                st.divider()


def answer_question(question: str, language: str) -> None:
    try:
        with st.spinner("Checking approved government sources…"):
            search_question = normalize_for_search(question, language)
            retrieval = retrieve_evidence(search_question)
    except SERVICE_ERRORS as exc:
        st.error(f"The search could not be completed: {exc}")
        return
    dashboard_store.save_search(st.session_state.guest_id, question, language)
    if not retrieval.get("evidence"):
        text = "I could not retrieve relevant evidence from the approved government sources, so I cannot provide a factual answer. Try a different scheme name or check the source registry."
        st.session_state.messages.extend([
            {"role": "user", "content": question},
            {"role": "assistant", "content": text, "citations": []},
        ])
        st.session_state.last_answer = {"text": text, "citations": []}
        return
    try:
        with st.spinner("Preparing an answer from retrieved evidence…"):
            answer = GeminiService().reply_with_evidence(question, language, st.session_state.messages[-8:], retrieval["evidence"], retrieval["citations"])
    except GeminiServiceError as exc:
        answer = evidence_excerpt_fallback(str(exc), retrieval["evidence"])
    st.session_state.messages.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer, "citations": retrieval["citations"]},
    ])
    st.session_state.last_answer = {"text": answer, "citations": retrieval["citations"]}


def evidence_excerpt_fallback(error: str, evidence: list[dict]) -> str:
    """Use only verbatim retrieved passages when both model attempts are unavailable."""
    parts = [f"AI answer generation is temporarily unavailable: {error}", "", "Verified source excerpts (original wording):"]
    used = set()
    for chunk in evidence:
        citation_id = chunk.get("citation_id")
        excerpt = " ".join(str(chunk.get("text", "")).split())
        if not excerpt or citation_id in used:
            continue
        used.add(citation_id)
        parts.append(f"\n[{citation_id}] {excerpt[:900]}")
        if len(used) == 3:
            break
    if not used:
        parts.append("No excerpt text was available from the retrieved pages.")
    return "\n".join(parts)


def language_picker() -> str:
    current = st.session_state.language
    selection = st.selectbox("Answer language", LANGUAGE_OPTIONS, index=LANGUAGE_OPTIONS.index(current) if current in LANGUAGE_OPTIONS else 1, key="language_picker")
    if selection != current:
        st.session_state.language = selection
        dashboard_store.set_preferred_language(st.session_state.guest_id, selection)
    return selection


def queue_voice_question() -> None:
    """Submit the reviewed transcript and synchronize answer/TTS language first."""
    question = st.session_state.get("transcript_editor", "").strip()
    if not question:
        return
    response_language = voice_response_language(
        st.session_state.get("detected_audio_language", ""), st.session_state.language
    )
    if response_language != st.session_state.language:
        st.session_state.language = response_language
        # Button callbacks run before the next script pass, so this widget state
        # can be safely updated before language_picker() renders again.
        st.session_state.language_picker = response_language
        dashboard_store.set_preferred_language(st.session_state.guest_id, response_language)
    st.session_state.pending_voice_question = question
    st.session_state.transcript = None


def assistant_page() -> None:
    st.title("Ask about government schemes")
    st.write("Get answers grounded in retrieved public government pages. Every factual response includes the source URL and retrieval date.")
    language = language_picker()
    if not GEMINI_API_KEY:
        st.warning("Gemini is not configured. Retrieval and source links work, but generated answers and audio transcription need GEMINI_API_KEY in .env.")
    for index, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant" and message.get("citations"):
                show_sources(message["citations"], f"chat_{index}")
    audio = st.audio_input("Record a question", sample_rate=16000, key="question_audio")
    if audio and st.button("Transcribe recording", key="transcribe_audio", disabled=not bool(GEMINI_API_KEY)):
        try:
            with st.spinner("Transcribing audio…"):
                result = GeminiService().transcribe_audio(audio.getvalue(), audio.type or "audio/wav", language)
            st.session_state.transcript = result["transcript"]
            st.session_state.detected_audio_language = result.get("language", "")
        except SERVICE_ERRORS as exc:
            st.error(str(exc))
    if st.session_state.transcript:
        detected = st.session_state.get("detected_audio_language", "")
        st.text_area("Review or edit the transcript", value=st.session_state.transcript, key="transcript_editor")
        if detected:
            st.caption(f"Detected speech language: {detected}")
        st.button("Ask using this transcript", type="primary", on_click=queue_voice_question)
    pending_voice_question = st.session_state.pop("pending_voice_question", None)
    if pending_voice_question:
        answer_question(pending_voice_question, language)
        st.rerun()
    question = st.chat_input("Ask about a scheme, benefit, or application")
    if question:
        answer_question(question, language)
        st.rerun()
    if st.session_state.last_answer:
        st.subheader("Listen to the latest answer")
        tts_language = language
        if language == AUTO_DETECT:
            tts_language = normalize_detected_language(st.session_state.get("detected_audio_language", "")) or "English"
        components.html(speech_controls_html(st.session_state.last_answer["text"], language_code(tts_language)), height=100)


def eligibility_page() -> None:
    st.title("Eligibility checker")
    st.write("Compare the details you choose to provide with eligibility conditions found in approved official sources. This is preliminary guidance, not an application decision.")
    with st.form("eligibility_form"):
        scheme = st.text_input("Scheme name", placeholder="For example, PM-KISAN")
        c1, c2 = st.columns(2)
        state = c1.text_input("State or union territory")
        district = c2.text_input("District (optional)")
        c3, c4 = st.columns(2)
        age = c3.number_input("Age (optional)", min_value=0, max_value=120, value=None, step=1)
        income = c4.number_input("Annual household income in ₹ (optional)", min_value=0, value=None, step=1000)
        occupation = st.text_input("Occupation or applicant category (optional)")
        details = st.text_area("Other relevant details (optional)", placeholder="Only include information relevant to the scheme.")
        st.caption("Do not enter Aadhaar numbers, bank details, passwords, or OTPs.")
        submitted = st.form_submit_button("Check against official criteria", type="primary")
    if submitted:
        if not scheme.strip():
            st.error("Enter a scheme name.")
            return
        profile = {key: value for key, value in {
            "state": state.strip(), "district": district.strip(), "age": age,
            "annual_household_income_inr": income, "occupation": occupation.strip(), "other_details": details.strip(),
        }.items() if value not in (None, "")}
        try:
            with st.spinner("Retrieving official eligibility details…"):
                result = assess_scheme_eligibility(scheme.strip(), profile, st.session_state.language)
            assessment = result["assessment"]
            st.subheader(assessment["result"])
            st.write(assessment["explanation"])
            for label, field in (("Conditions that appear to match", "satisfied"), ("Conditions that appear not to match", "not_satisfied"), ("Information still needed", "missing")):
                values = assessment.get(field, [])
                if values:
                    st.markdown(f"**{label}**")
                    for value in values:
                        st.markdown(f"- {value}")
            show_sources(result["retrieval"].get("citations", []), "eligibility")
            if st.checkbox("Save this assessment to my dashboard", key="save_assessment_choice"):
                dashboard_store.save_assessment(st.session_state.guest_id, scheme.strip(), {"assessment": assessment, "profile": profile, "citations": result["retrieval"].get("citations", [])})
                st.success("Assessment saved for this guest dashboard.")
        except SERVICE_ERRORS as exc:
            st.error(str(exc))
            # Preserve the actual retrieved citations even when Gemini assessment is unavailable.
            try:
                from services.feature_service import retrieve_for_queries
                retrieval = retrieve_for_queries([f"{scheme.strip()} official eligibility criteria conditions documents"], "English")
                show_sources(retrieval.get("citations", []), "eligibility_unassessed")
            except Exception as retrieval_exc:
                st.caption(f"Could not display source details: {retrieval_exc}")


def comparison_page() -> None:
    st.title("Compare schemes")
    st.write("Compare benefits, eligibility, documents, and application steps using official evidence retrieved for each scheme.")
    names = st.text_area("Enter two or three scheme names, one per line", placeholder="PM-KISAN\nPM Fasal Bima Yojana", height=110)
    if st.button("Retrieve and compare", type="primary"):
        schemes = list(dict.fromkeys(line.strip() for line in names.splitlines() if line.strip()))
        if len(schemes) < 2 or len(schemes) > 3:
            st.error("Enter two or three distinct scheme names.")
        else:
            try:
                with st.spinner("Retrieving official details and comparing evidence…"):
                    result = compare_schemes(schemes, st.session_state.language)
                comparison = result["comparison"]
                st.write(comparison.get("summary", ""))
                for row in comparison.get("schemes", []):
                    with st.expander(row.get("name", "Scheme"), expanded=True):
                        st.markdown(f"**Benefits:** {row.get('benefits', 'Not found in retrieved evidence')}")
                        st.markdown(f"**Eligibility:** {row.get('eligibility', 'Not found in retrieved evidence')}")
                        st.markdown(f"**Documents:** {row.get('documents', 'Not found in retrieved evidence')}")
                        st.markdown(f"**Application:** {row.get('application', 'Not found in retrieved evidence')}")
                show_sources(result["retrieval"].get("citations", []), "comparison")
            except SERVICE_ERRORS as exc:
                st.error(str(exc))
                try:
                    from services.feature_service import retrieve_for_queries
                    queries = [f"{name} official scheme benefits eligibility required documents application process" for name in schemes]
                    retrieved = retrieve_for_queries(queries, st.session_state.language)
                    show_sources(retrieved.get("citations", []), "comparison_unstructured")
                except Exception as retrieval_exc:
                    st.caption(f"Source retrieval also failed: {retrieval_exc}")


def agent_page() -> None:
    st.title("Scheme research agent")
    st.write("Describe a household or need. The agent creates a bounded query plan, searches only the configured government registry, then grounds its response in retrieved excerpts.")
    request = st.text_area("Research request", placeholder="I am a small farmer in Maharashtra looking for support for crop loss.", height=110)
    if st.button("Research official sources", type="primary"):
        if not request.strip():
            st.error("Describe what you want to research.")
        else:
            try:
                with st.spinner("Planning official-source searches…"):
                    result = run_scheme_agent(request.strip(), st.session_state.language)
                with st.expander("Research plan", expanded=True):
                    st.write(result["plan"].get("interpreted_request", ""))
                    st.caption("Search queries generated by the agent")
                    for query in result["plan"].get("search_queries", []):
                        st.markdown(f"- {query}")
                st.markdown(result["answer"])
                show_sources(result["retrieval"].get("citations", []), "agent")
            except SERVICE_ERRORS as exc:
                st.error(str(exc))


def dashboard_page() -> None:
    st.title("Your dashboard")
    st.caption("This dashboard is stored locally and associated with this browser session. No account is required.")
    guest = st.session_state.guest_id
    preferred = dashboard_store.get_preferred_language(guest) or st.session_state.language
    data = dashboard_store.dashboard_data(guest)
    c1, c2, c3 = st.columns(3)
    c1.metric("Recent searches", len(data["searches"]))
    c2.metric("Saved official sources", len(data["schemes"]))
    c3.metric("Saved assessments", len(data["assessments"]))
    st.write(f"Preferred answer language: **{preferred}**")
    with st.expander("Recent searches", expanded=True):
        if data["searches"]:
            for row in data["searches"]:
                st.markdown(f"- {row['query']} · {row['language']} · {row['created_at']}")
        else:
            st.caption("Questions you ask will appear here.")
    with st.expander("Saved official sources", expanded=True):
        if data["schemes"]:
            for row in data["schemes"]:
                st.markdown(f"- [{row['title']}]({row['url']}) · retrieved {row['retrieved_at']}")
        else:
            st.caption("Use Save source in a result to keep a source here.")
    with st.expander("Saved eligibility assessments"):
        if data["assessments"]:
            for row in data["assessments"]:
                st.markdown(f"**{row['scheme']}** · {row['created_at']}")
                st.json(row["payload"]["assessment"])
        else:
            st.caption("Opt in to save an assessment from the eligibility checker.")
    st.divider()
    if st.button("Clear my dashboard data", type="secondary"):
        dashboard_store.clear_guest_data(guest)
        st.session_state.pop("language", None)
        st.session_state.pop("messages", None)
        st.rerun()


def main() -> None:
    initialize_state()
    st.sidebar.title("YojanaMitra AI")
    page = st.sidebar.radio("Go to", ["Assistant", "Eligibility checker", "Scheme comparison", "Research agent", "Dashboard"], label_visibility="collapsed")
    st.sidebar.caption(f"Retrieval backend: {active_backend()}")
    st.sidebar.caption("Answers rely on approved public sources and may not reflect changes after retrieval.")
    if page == "Assistant":
        assistant_page()
    elif page == "Eligibility checker":
        eligibility_page()
    elif page == "Scheme comparison":
        comparison_page()
    elif page == "Research agent":
        agent_page()
    else:
        dashboard_page()


if __name__ == "__main__":
    main()
