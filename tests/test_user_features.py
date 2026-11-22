from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.languages import AUTO_DETECT, LANGUAGES, normalize_detected_language, resolve_voice_response_language
from services.voice_service import speech_controls_html


class FeatureServiceTests(unittest.TestCase):
    def test_all_eleven_languages_have_voice_codes_and_detected_labels_normalize(self):
        self.assertEqual(len(LANGUAGES), 11)
        self.assertTrue(all(item["code"].endswith("-IN") for item in LANGUAGES.values()))
        self.assertEqual(normalize_detected_language("Marathi (India)"), "Marathi")
        self.assertIsNone(normalize_detected_language("French"))
        self.assertEqual(resolve_voice_response_language("Hindi", "English"), "Hindi")
        self.assertEqual(resolve_voice_response_language("", "Tamil"), "Tamil")
        self.assertEqual(AUTO_DETECT, "Auto-detect from question")

    def test_browser_speech_widget_has_controls_and_escapes_markup(self):
        html = speech_controls_html("Hello </script><script>alert(1)</script>", "hi-IN")
        self.assertIn("speechSynthesis", html)
        self.assertIn("Replay", html)
        self.assertIn("\u003c/script\u003e", html)
        self.assertNotIn("Hello </script>", html)

    def test_dashboard_persists_and_clears_guest_data(self):
        from database import dashboard_store as store

        with tempfile.TemporaryDirectory() as directory, patch.object(store, "DATABASE_PATH", Path(directory) / "dashboard.sqlite3"):
            store.set_preferred_language("guest", "Tamil")
            store.save_search("guest", "farm benefit", "Tamil")
            store.save_scheme("guest", "Official page", "https://example.gov.in/x", "2026-11-22 00:00 UTC")
            store.save_assessment("guest", "Scheme", {"result": "More information required"})
            data = store.dashboard_data("guest")
            self.assertEqual(store.get_preferred_language("guest"), "Tamil")
            self.assertEqual(data["searches"][0]["query"], "farm benefit")
            self.assertEqual(data["schemes"][0]["url"], "https://example.gov.in/x")
            self.assertEqual(data["assessments"][0]["payload"]["result"], "More information required")
            store.clear_guest_data("guest")
            self.assertEqual(store.dashboard_data("guest"), {"searches": [], "schemes": [], "assessments": []})
            self.assertIsNone(store.get_preferred_language("guest"))

    def test_audio_transcription_uses_recorded_bytes_and_returns_detected_language(self):
        from services.gemini_service import GeminiService

        response = type("Response", (), {"text": json.dumps({"transcript": "मुझे किसान योजना बताइए", "language": "Hindi"})})()
        with patch("services.gemini_service.GEMINI_API_KEY", "test-key"), patch("services.gemini_service.genai.Client") as constructor:
            client = constructor.return_value
            client.models.generate_content.return_value = response
            result = GeminiService().transcribe_audio(b"wav-data", "audio/wav", AUTO_DETECT)
            self.assertEqual(result["language"], "Hindi")
            self.assertIn("किसान", result["transcript"])
            self.assertEqual(client.models.generate_content.call_args.kwargs["model"], "gemini-3.8-flash")

    def test_translation_and_structured_gemini_features(self):
        from services.gemini_service import GeminiService

        replies = [
            "farmer income support Maharashtra",
            json.dumps({"result": "More information required", "satisfied": [], "not_satisfied": [], "missing": ["land record"], "explanation": "The evidence does not state the applicant's land details.", "source_ids": ["S1", "S999"]}),
            json.dumps({"summary": "The sources describe different programmes.", "schemes": [{"name": "A", "benefits": "Not found", "eligibility": "Farmer", "documents": "Not found", "application": "Portal", "source_ids": ["S1", "S999"]}]}),
            json.dumps({"interpreted_request": "farmer support", "search_queries": ["farmer support official", "https://bad.invalid", ""], "beneficiary": "farmer", "location": "Maharashtra"}),
        ]
        with patch("services.gemini_service.GEMINI_API_KEY", "test-key"), patch("services.gemini_service.genai.Client") as constructor:
            client = constructor.return_value
            client.models.generate_content.side_effect = [type("R", (), {"text": text})() for text in replies]
            service = GeminiService()
            source = [{"id": "S1", "url": "https://example.gov.in/scheme", "retrieved_at": "2026-11-22 UTC"}]
            evidence = [{"citation_id": "S1", "url": source[0]["url"], "retrieved_at": source[0]["retrieved_at"], "text": "Official eligibility details."}]
            self.assertEqual(service.translate_for_search("शेतकरी योजना", "Marathi"), "farmer income support Maharashtra")
            eligibility = service.assess_eligibility("Scheme", {"state": "MH"}, evidence, source)
            self.assertEqual(eligibility["source_ids"], ["S1"])
            comparison = service.compare_schemes(["A", "B"], evidence, source, "English")
            self.assertEqual(comparison["schemes"][0]["source_ids"], ["S1"])
            plan = service.plan_agent_search("Need support for farmers", "English")
            self.assertEqual(plan["search_queries"], ["farmer support official"])
            self.assertEqual(client.models.generate_content.call_count, 4)

    def test_gemini_api_errors_are_actionable_without_echoing_keys(self):
        from services.gemini_service import GeminiService

        error = type("APIError", (Exception,), {"status_code": 401})() 
        error.args = ("unauthorized key=AQ.this-is-a-long-test-secret-value",)
        message = GeminiService._friendly_api_error(error)
        self.assertIn("authentication or access failed (401)", message)
        self.assertIn("[redacted]", message)
        self.assertNotIn("this-is-a-long-test-secret-value", message)
        missing_model = type("APIError", (Exception,), {"status_code": 404})() 
        self.assertIn("GEMINI_MODEL", GeminiService._friendly_api_error(missing_model))
        quota = type("APIError", (Exception,), {"status_code": 429})() 
        self.assertIn("quota or rate limit", GeminiService._friendly_api_error(quota))

    def test_gemini_uses_fallback_model_after_primary_503(self):
        from services.gemini_service import GeminiService

        overload = RuntimeError("temporarily overloaded")
        overload.status_code = 503
        response = type("Response", (), {"text": "Verified answer"})()
        with patch("services.gemini_service.GEMINI_API_KEY", "test-key"), \
             patch("services.gemini_service.GEMINI_MODEL", "gemini-3.8-flash"), \
             patch("services.gemini_service.GEMINI_FALLBACK_MODEL", "gemini-3.6-flash"), \
             patch("services.gemini_service.types.GenerateContentConfig", side_effect=lambda **kwargs: kwargs), \
             patch("services.gemini_service.genai.Client") as constructor:
            client = constructor.return_value
            client.models.generate_content.side_effect = [overload, response]
            answer = GeminiService._generate("prompt", "system", temperature=0.2)
        self.assertEqual(answer, "Verified answer")
        calls = client.models.generate_content.call_args_list
        self.assertEqual([call.kwargs["model"] for call in calls], ["gemini-3.8-flash", "gemini-3.6-flash"])
        self.assertNotIn("temperature", calls[0].kwargs["config"])
        retry_options = constructor.call_args.kwargs["http_options"].retry_options
        self.assertEqual(retry_options.attempts, 1)
        client.close.assert_called_once()

    def test_gemini_outage_fallback_uses_only_retrieved_excerpts(self):
        from app import evidence_excerpt_fallback

        answer = evidence_excerpt_fallback("Gemini unavailable (503).", [
            {"citation_id": "S1", "text": "Submit the form to the district office."},
            {"citation_id": "S1", "text": "Duplicate excerpt."},
            {"citation_id": "S2", "text": "Supporting documents are required."},
        ])
        self.assertIn("[S1] Submit the form to the district office.", answer)
        self.assertIn("[S2] Supporting documents are required.", answer)
        self.assertNotIn("Duplicate excerpt", answer)

    def test_gemini_retries_on_fallback_model_after_primary_503(self):
        from services.gemini_service import GeminiService

        overloaded = RuntimeError("temporarily overloaded")
        overloaded.status_code = 503
        response = type("Response", (), {"text": "Grounded answer"})()
        with patch("services.gemini_service.GEMINI_API_KEY", "test-key"), \
             patch("services.gemini_service.GEMINI_MODEL", "gemini-3.8-flash"), \
             patch("services.gemini_service.GEMINI_FALLBACK_MODEL", "gemini-3.6-flash"), \
             patch("services.gemini_service.types.GenerateContentConfig", side_effect=lambda **kwargs: kwargs), \
             patch("services.gemini_service.genai.Client") as constructor:
            client = constructor.return_value
            client.models.generate_content.side_effect = [overloaded, response]
            answer = GeminiService._generate("hello", "system", temperature=0.2)
        self.assertEqual(answer, "Grounded answer")
        self.assertEqual([call.kwargs["model"] for call in client.models.generate_content.call_args_list], ["gemini-3.8-flash", "gemini-3.6-flash"])
        self.assertNotIn("temperature", client.models.generate_content.call_args_list[0].kwargs["config"])
        client.close.assert_called_once()

    def test_chat_fallback_displays_only_actual_source_excerpts(self):
        from app import evidence_excerpt_fallback

        evidence = [
            {"citation_id": "S1", "text": "Applicants should submit the form to the district office."},
            {"citation_id": "S1", "text": "Duplicate chunk from the same cited source."},
            {"citation_id": "S2", "text": "The official page lists required supporting documents."},
        ]
        text = evidence_excerpt_fallback("Gemini is unavailable (503).", evidence)
        self.assertIn("[S1] Applicants should submit the form to the district office.", text)
        self.assertIn("[S2] The official page lists required supporting documents.", text)
        self.assertNotIn("Duplicate chunk", text)

    def test_feature_workflows_retrieve_sources_for_eligibility_comparison_and_agent(self):
        from services import feature_service as features

        source = {"id": "S1", "url": "https://example.gov.in/scheme", "title": "Scheme rules", "retrieved_at": "2026-11-22 UTC"}
        evidence = [{"citation_id": "S1", "url": source["url"], "title": source["title"], "retrieved_at": source["retrieved_at"], "text": "Eligibility is described here.", "score": 1}]
        retrieval = {"evidence": evidence, "citations": [source], "failures": [], "indexed_count": 1, "live_pages_retrieved": 1, "vector_backend": "test"}
        with patch.object(features, "GEMINI_API_KEY", "test-key"), patch("services.gemini_service.GEMINI_API_KEY", "test-key"), patch.object(features, "retrieve_evidence", return_value=retrieval), patch.object(features.GeminiService, "assess_eligibility", return_value={"result": "More information required", "satisfied": [], "not_satisfied": [], "missing": [], "explanation": "Incomplete", "source_ids": ["S1"]}):
            assessment = features.assess_scheme_eligibility("Scheme", {"state": "MH"}, "English")
            self.assertEqual(assessment["retrieval"]["citations"][0]["url"], source["url"])
        with patch.object(features, "GEMINI_API_KEY", ""), patch.object(features, "retrieve_evidence", return_value=retrieval):
            comparison = features.compare_schemes(["A", "B"], "English")
            self.assertEqual(comparison["retrieval"]["citations"][0]["retrieved_at"], source["retrieved_at"])
        with patch.object(features, "GEMINI_API_KEY", "test-key"), patch("services.gemini_service.GEMINI_API_KEY", "test-key"), patch.object(features.GeminiService, "plan_agent_search", return_value={"interpreted_request": "need", "search_queries": ["farmer"], "beneficiary": "farmer", "location": ""}), patch.object(features.GeminiService, "agent_answer", return_value="Grounded answer [S1]"), patch.object(features, "retrieve_evidence", return_value=retrieval):
            agent = features.run_scheme_agent("Need help", "English")
            self.assertIn("[S1]", agent["answer"])
            self.assertEqual(agent["retrieval"]["citations"][0]["url"], source["url"])


if __name__ == "__main__":
    unittest.main()
