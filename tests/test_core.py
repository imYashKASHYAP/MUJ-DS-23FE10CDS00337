import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from feedbacklens.core import (
    AnalysisError, analyze_batch, analyze_review, build_report, load_env, load_reviews, redact_pii,
)


CONFIG = {
    "api_base_url": "https://generativelanguage.googleapis.com/v1beta/models",
    "model": "gemini-3.1-flash-lite",
    "timeout_seconds": 10,
    "max_retries": 3,
    "retry_base_seconds": 0.01,
    "max_review_chars": 4000,
}
RESULT = {
    "sentiment": "negative",
    "summary": "Checkout fails.",
    "aspects": [{"name": "usability", "sentiment": "negative", "evidence": "checkout crashes"}],
    "priority": "medium",
    "recommended_action": "Investigate checkout failures.",
}


class CoreTests(unittest.TestCase):
    def test_redacts_common_pii(self):
        masked = redact_pii("Email me at sam@example.com or +1 415 555 0199 about checkout")
        self.assertIn("[EMAIL]", masked)
        self.assertIn("[PHONE]", masked)
        self.assertNotIn("sam@example.com", masked)

    def test_csv_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.csv"
            path.write_text("id,text\n1,First\n1,Second\n", encoding="utf-8")
            with self.assertRaisesRegex(AnalysisError, "duplicate id"):
                load_reviews(path, 4000)

    def test_api_payload_and_retry(self):
        calls = []

        def post(url, body, key, timeout):
            payload = json.loads(body)
            calls.append(payload)
            self.assertEqual(url, CONFIG["api_base_url"] + "/gemini-3.1-flash-lite:generateContent")
            self.assertEqual(key, "test-key")
            self.assertEqual(timeout, 10)
            self.assertIn("contents", payload)
            self.assertIn("generationConfig", payload)
            self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
            self.assertIn("responseSchema", payload["generationConfig"])
            self.assertNotIn("sam@example.com", payload["contents"][0]["parts"][0]["text"])
            if len(calls) == 1:
                raise HTTPError(url, 429, "rate limit", {}, io.BytesIO())
            return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(RESULT)}]}}]}

        sleeps = []
        output = analyze_review("sam@example.com: checkout crashes", "Prompt", CONFIG, "test-key", post, sleeps.append)
        self.assertEqual(output, RESULT)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.01])

    def test_rejects_hallucinated_evidence(self):
        false_result = {**RESULT, "aspects": [{**RESULT["aspects"][0], "evidence": "slow shipping"}]}

        def post(*_):
            return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(false_result)}]}}]}

        with self.assertRaisesRegex(AnalysisError, "evidence"):
            analyze_review("checkout crashes", "Prompt", CONFIG, "test-key", post)

    def test_invalid_key_stops_without_retry(self):
        calls = []

        def post(url, *_):
            calls.append(1)
            body = io.BytesIO(json.dumps({"error": {"status": "PERMISSION_DENIED"}}).encode())
            raise HTTPError(url, 403, "permission denied", {}, body)

        with self.assertRaisesRegex(AnalysisError, "PERMISSION_DENIED"):
            analyze_review("checkout crashes", "Prompt", CONFIG, "test-key", post)
        self.assertEqual(len(calls), 1)

    def test_batch_cache_and_report(self):
        calls = []

        def fake_analyze(*_):
            calls.append(1)
            return RESULT

        reviews = [{"id": "1", "text": "checkout crashes"}, {"id": "2", "text": "checkout crashes"}]
        with tempfile.TemporaryDirectory() as directory:
            output = analyze_batch(reviews, "Prompt", CONFIG, "test-key", Path(directory), fake_analyze)
            self.assertEqual(len(calls), 1)
            self.assertEqual(build_report(output)["aspect_mentions"], {"usability": 2})
            self.assertEqual(build_report(output)["total_reviews"], 2)

    def test_load_env(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("TEST_KEY_VAR=hello_test_123\n# Comment\nINVALID_LINE\n", encoding="utf-8")
            load_env(env_file)
            self.assertEqual(os.environ.get("TEST_KEY_VAR"), "hello_test_123")



if __name__ == "__main__":
    unittest.main()
