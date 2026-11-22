from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.robotparser import RobotFileParser

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from services.documents import chunk_text, extract_html, extract_pdf
from services.sources import PublicSourceFetcher, SourceAccessError, validate_trusted_url


class RetrievalPipelineTests(unittest.TestCase):
    def test_trusted_registry_rejects_unlisted_or_unsafe_urls(self):
        registry = [{"allowed_domains": ["example.gov.in"]}]
        self.assertTrue(validate_trusted_url("https://portal.example.gov.in/schemes", registry))
        self.assertFalse(validate_trusted_url("http://example.gov.in/", registry))
        self.assertFalse(validate_trusted_url("https://example.gov.in.attacker.test/", registry))
        self.assertFalse(validate_trusted_url("https://127.0.0.1/", registry))
        self.assertFalse(validate_trusted_url("https://user@example.gov.in/", registry))

    def test_html_extraction_preserves_title_date_and_strips_scripts(self):
        html = b"""<html><head><title>Official Scheme</title><meta name='datePublished' content='2026-10-01'></head>
        <body><nav>Navigation</nav><main><h1>Farmer support</h1><p>Application details here.</p>
        <a href='/scheme'>Scheme details</a><script>private fake content</script></main></body></html>"""
        doc, links = extract_html(html, "https://example.gov.in/", "Example Government", "2026-11-22 10:00 UTC")
        self.assertEqual(doc["title"], "Official Scheme")
        self.assertEqual(doc["publication_date"], "2026-10-01")
        self.assertIn("Farmer support", doc["text"])
        self.assertNotIn("private fake content", doc["text"])
        self.assertEqual(links[0]["href"], "/scheme")
        self.assertEqual(doc["retrieved_at"], "2026-11-22 10:00 UTC")

    def test_pdf_extraction_keeps_page_numbers_and_metadata(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 72 720 Td (Farmer assistance scheme) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        output = BytesIO()
        writer.write(output)
        docs = extract_pdf(output.getvalue(), "https://example.gov.in/scheme.pdf", "Example Government", "2026-11-22 10:00 UTC")
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["page"], 1)
        self.assertIn("Farmer assistance scheme", docs[0]["text"])
        self.assertEqual(docs[0]["document_type"], "pdf")

    def test_chunking_obeys_size_and_preserves_source_text(self):
        text = ("The farmer support programme provides information and application guidance. " * 80).strip()
        chunks = chunk_text(text, chunk_size=400, overlap=50)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 400 for chunk in chunks))
        self.assertIn("application guidance", " ".join(chunks))

    def test_pure_python_index_replacement_retains_other_source_vectors(self):
        from services import embedding_service, retrieval

        def document(url: str, text: str) -> dict:
            return {
                "title": "Official scheme page", "url": url, "source_name": "Example Government",
                "document_type": "html", "retrieved_at": "2026-11-22 10:00 UTC", "text": text,
            }

        with tempfile.TemporaryDirectory() as directory:
            retrieval.VECTOR_STORE_PATH = Path(directory)
            docs = [
                document("https://example.gov.in/first", "Original farmer support policy and application details."),
                document("https://example.gov.in/second", "Scholarship support for students and education costs."),
            ]
            with patch.object(embedding_service, "EMBEDDING_PROVIDER", "local"), patch.object(retrieval, "_load_faiss", return_value=None):
                self.assertGreater(retrieval.index_documents(docs), 0)
                replacement = document(docs[0]["url"], "Updated farmer support policy and revised application details.")
                self.assertGreater(retrieval.index_documents([replacement]), 0)
                # Simulate FAISS becoming available after this fallback index was created.
                with patch.object(retrieval, "_load_faiss", return_value=object()):
                    results = retrieval.retrieve_chunks("scholarship support for students")
                    self.assertEqual(retrieval.active_backend(), "pure-Python vector fallback")
                metadata = json.loads((Path(directory) / retrieval.METADATA_FILE).read_text(encoding="utf-8"))

        self.assertTrue(results)
        self.assertIn("https://example.gov.in/second", {item["url"] for item in results})
        self.assertEqual(len(metadata["chunks"]), 2)

    def test_fetcher_obeys_robots_policy(self):
        fetcher = PublicSourceFetcher()
        parser = RobotFileParser()
        parser.parse(["User-agent: *", "Disallow: /"])
        with patch.object(fetcher, "_robots_for", return_value=(parser, [])), patch.object(fetcher.session, "get") as get:
            with self.assertRaises(SourceAccessError):
                fetcher._get_bytes("https://pmkisan.gov.in/")
            get.assert_not_called()

    def test_public_fetcher_extracts_only_the_returned_official_page(self):
        fetcher = PublicSourceFetcher()
        robots = RobotFileParser()
        robots.parse(["User-agent: *", "Allow: /"])
        html = b"<html><head><title>PM-KISAN official information</title></head><body><main>Farmer support details.</main></body></html>"
        with patch.object(fetcher, "_robots_for", return_value=(robots, [])), patch.object(
            fetcher, "_get_bytes", return_value=("https://pmkisan.gov.in/example", html, "text/html")
        ):
            documents, links = fetcher.fetch_document("https://pmkisan.gov.in/example")
        self.assertEqual(links, [])
        self.assertEqual(documents[0]["source_name"], "PM-KISAN")
        self.assertEqual(documents[0]["url"], "https://pmkisan.gov.in/example")
        self.assertIn("Farmer support details", documents[0]["text"])
        self.assertTrue(documents[0]["retrieved_at"])

    @unittest.skipUnless(importlib.util.find_spec("faiss"), "faiss-cpu must be installed for FAISS integration tests")
    def test_end_to_end_mocked_source_to_vector_citation_pipeline(self):
        from services import embedding_service, rag_service, retrieval

        with tempfile.TemporaryDirectory() as directory:
            retrieval.VECTOR_STORE_PATH = Path(directory)
            doc = {
                "title": "Farmer Assistance Scheme",
                "url": "https://pmkisan.gov.in/example-scheme",
                "source_name": "PM-KISAN",
                "document_type": "html",
                "page": None,
                "publication_date": "2026-04-15",
                "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "text": "This official public page describes farmer income support in Maharashtra. Applicants can review current eligibility and application information on the official portal.",
            }
            with patch.object(embedding_service, "EMBEDDING_PROVIDER", "local"), patch.object(
                rag_service, "retrieve_official_sources", return_value=([doc], [])
            ):
                result = rag_service.retrieve_evidence("farmer income support Maharashtra")
                self.assertEqual(result["live_pages_retrieved"], 1)
                self.assertGreater(result["indexed_count"], 0)
                self.assertTrue(result["evidence"])
                self.assertIn(result["vector_backend"], {"faiss", "pure-python vector fallback"})
                citation = result["citations"][0]
                self.assertEqual(citation["url"], doc["url"])
                self.assertEqual(citation["retrieved_at"], doc["retrieved_at"])
                self.assertTrue(citation["retrieved_this_search"])
                self.assertIn("farmer", result["evidence"][0]["text"].lower())

                # Re-fetch identical content: no duplicate vectors, but timestamp metadata refreshes.
                updated = {**doc, "retrieved_at": "2026-11-22 11:00 UTC"}
                with patch.object(rag_service, "retrieve_official_sources", return_value=([updated], [])):
                    again = rag_service.retrieve_evidence("farmer income support Maharashtra")
                self.assertEqual(again["indexed_count"], 0)
                self.assertEqual(again["citations"][0]["retrieved_at"], "2026-11-22 11:00 UTC")

    def test_rag_generation_prompt_uses_only_actual_cited_metadata(self):
        from services.gemini_service import GeminiService

        source = {
            "id": "S1", "url": "https://pmkisan.gov.in/actual-page",
            "source_name": "PM-KISAN", "title": "Farmer information",
            "retrieved_at": "2026-11-22 11:00 UTC", "page": None,
        }
        chunk = {"citation_id": "S1", "url": source["url"], "source_name": source["source_name"],
                 "title": source["title"], "retrieved_at": source["retrieved_at"],
                 "page": None, "text": "Official page says to review the published scheme terms."}
        fake_response = type("Response", (), {"text": "Review the published terms. [S1]"})()
        with patch("services.gemini_service.GEMINI_API_KEY", "unit-test-key"), patch("services.gemini_service.genai.Client") as client_constructor:
            client = client_constructor.return_value
            client.models.generate_content.return_value = fake_response
            answer = GeminiService().reply_with_evidence("What terms?", "English", [], [chunk], [source])
        self.assertIn("[S1]", answer)
        call = client.models.generate_content.call_args.kwargs
        self.assertIn(source["url"], str(call["contents"]))
        self.assertIn(source["retrieved_at"], str(call["contents"]))
        self.assertNotIn("fabricated.gov.in", str(call["contents"]))

    def test_rag_refuses_to_generate_without_retrieved_evidence(self):
        from services.gemini_service import GeminiService, GeminiServiceError

        with patch("services.gemini_service.GEMINI_API_KEY", "unit-test-key"), patch("services.gemini_service.genai.Client"):
            service = GeminiService()
            with self.assertRaises(GeminiServiceError):
                service.reply_with_evidence("question", "English", [], [], [])


if __name__ == "__main__":
    unittest.main()
