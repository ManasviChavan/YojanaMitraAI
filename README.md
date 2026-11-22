# YojanaMitra AI

YojanaMitra AI is a Streamlit assistant for information about Indian government schemes. It retrieves pages from a configured official-source registry, extracts HTML and text-PDF content, indexes passages, and grounds generated answers in cited evidence. The interface includes 11 languages, voice input/output, an eligibility checker, scheme comparison, a bounded research agent, and a local guest dashboard.

## Architecture

```text
Streamlit pages (app.py)
  Assistant: text and recorded audio
  Eligibility, comparison, bounded research agent
  Guest dashboard: local SQLite
          |
          v
services/rag_service.py -> trusted-source registry -> fetch/extract/chunk
                         -> Gemini/local embeddings -> FAISS/vector fallback
                         -> evidence-grounded Gemini response
```

`config/trusted_sources.json` holds the HTTPS host allowlist. `services/sources.py` observes robots.txt and fetch limits. `services/documents.py` extracts HTML and text PDFs. `services/retrieval.py` uses FAISS when its native library loads and a pure-Python vector fallback otherwise. The app records actual source URLs and retrieval timestamps with each citation. It does not use fabricated search results.

## Setup (PowerShell)

Use Python 3.10 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Set `GEMINI_API_KEY` in `.env` for generated answers, structured assessment/comparison, the research agent, and audio transcription. Retrieval and source display can run without the key using `EMBEDDING_PROVIDER=local`.

`GEMINI_MODEL` defaults to `gemini-3.8-flash`; `GEMINI_FALLBACK_MODEL` defaults to `gemini-3.6-flash`. When the primary returns a 503 overload response, the service tries the fallback. If both are unavailable, chat displays verbatim passages from retrieved official pages and their citations instead of inventing a summary.

Run:

```powershell
python -m streamlit run app.py
```

Run the local verification suite:

```powershell
python -m compileall app.py config services database tests
python -m unittest discover -s tests -v
```

## User features

- **Languages:** English, Hindi, Marathi, Gujarati, Bengali, Tamil, Telugu, Kannada, Malayalam, Punjabi, and Urdu. Auto-detect asks the model to answer in the language of the question.
- **Voice:** Streamlit captures a recording in the browser; Gemini transcribes it. The user can review/edit the transcript before submitting. Speech playback uses browser speech synthesis and offers available voices and playback controls.
- **Eligibility:** Retrieves scheme conditions and compares them with details the user chooses to provide. The result is preliminary guidance, not an official decision. Users must opt in before assessment details are stored.
- **Comparison:** Compares two or three named schemes using retrieved official excerpts. Unsupported fields are marked as missing by the grounded model.
- **Research agent:** Generates up to three bounded queries, searches only the configured trusted sources, then answers from those retrieved excerpts.
- **Dashboard:** Stores recent questions, saved source links, preferred language, and opted-in assessments in local SQLite. Guest data is tied to a browser session identifier; it is not a registered or cross-device account.

Every generated factual answer shows actual retrieved citations with URLs and retrieval dates. Retrieval timestamps indicate when this app fetched a page, not when the source government last changed it.

## Limits

- Registry retrieval starts from configured landing pages, public links, and available sitemaps. It is not a general web search engine and source sites may be unreachable or disallow automated access.
- Scanned/image-only PDFs need OCR, which is not included. Text PDFs are extracted with page numbers.
- Gemini features need a working API key and network access. The local test suite mocks Gemini and government responses; it does not prove live service availability or answer quality.
- Browser speech synthesis depends on the user's browser and installed voices. A supported voice may not exist for every language; the text remains available.
- Eligibility results can only compare details supplied by the user with conditions present in retrieved text. Confirm current requirements on the cited official portal before applying.
- On managed Windows systems where native FAISS/NumPy modules are blocked, a pure-Python vector fallback is used. Large indexes may search more slowly.
# YojanaMitraAI_
