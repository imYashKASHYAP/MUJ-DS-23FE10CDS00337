"""The main pipeline: read reviews, ask Gemini, check answers, and count trends."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib import error, request


SENTIMENTS = {"positive", "neutral", "negative", "mixed"}
ASPECTS = {"product_quality", "delivery", "support", "pricing", "usability", "other"}
PRIORITIES = {"low", "medium", "high"}

# Tell Gemini the exact shape of one review analysis.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sentiment": {"type": "string", "enum": sorted(SENTIMENTS)},
        "summary": {"type": "string"},
        "aspects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "enum": sorted(ASPECTS)},
                    "sentiment": {"type": "string", "enum": sorted(SENTIMENTS)},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "sentiment", "evidence"],
            },
        },
        "priority": {"type": "string", "enum": sorted(PRIORITIES)},
        "recommended_action": {"type": "string"},
    },
    "required": ["sentiment", "summary", "aspects", "priority", "recommended_action"],
}


class AnalysisError(Exception):
    """A readable input, API, or model-output failure."""


def load_env(path: Path | None = None) -> None:
    """Read a local .env file into os.environ without overriding already-set variables."""
    if path is None:
        path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ and value and not value.startswith(("replace_with_", "your_")):
            os.environ[key] = value


def load_config(path: Path) -> dict[str, Any]:
    """Read model settings and reject missing or unsafe values early."""
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"Cannot read configuration: {exc}") from exc
    required = {"api_base_url", "model", "timeout_seconds", "max_retries", "retry_base_seconds", "max_review_chars"}
    if not isinstance(config, dict) or not required <= config.keys():
        raise AnalysisError(f"Configuration must contain: {', '.join(sorted(required))}")
    if config["api_base_url"] != "https://generativelanguage.googleapis.com/v1beta/models":
        raise AnalysisError("Only the official Gemini GenerateContent endpoint is supported")
    if not isinstance(config["model"], str) or not config["model"]:
        raise AnalysisError("model must be a nonempty string")
    for name in ("timeout_seconds", "max_retries", "retry_base_seconds", "max_review_chars"):
        value = config[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise AnalysisError(f"{name} must be a positive number")
    if int(config["max_retries"]) != config["max_retries"] or int(config["max_review_chars"]) != config["max_review_chars"]:
        raise AnalysisError("max_retries and max_review_chars must be whole numbers")
    return config


def load_reviews(path: Path, max_chars: int) -> list[dict[str, str]]:
    """Read a CSV file for the terminal version of the project."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AnalysisError(f"Cannot read input CSV: {exc}") from exc
    return parse_reviews(raw, max_chars)


def parse_reviews(raw: str, max_chars: int) -> list[dict[str, str]]:
    """Check the CSV header, review IDs, and review lengths."""
    reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff"), newline=""))
    if not reader.fieldnames or not {"id", "text"} <= set(reader.fieldnames):
        raise AnalysisError("Input CSV needs id and text columns")
    rows = []
    seen = set()
    for line, row in enumerate(reader, start=2):
        review_id = (row.get("id") or "").strip()
        text = (row.get("text") or "").strip()
        if not review_id or not text:
            raise AnalysisError(f"Line {line}: id and text must be nonempty")
        if review_id in seen:
            raise AnalysisError(f"Line {line}: duplicate id {review_id!r}")
        if len(text) > max_chars:
            raise AnalysisError(f"Line {line}: review exceeds {max_chars} characters")
        seen.add(review_id)
        rows.append({"id": review_id, "text": text})
    if not rows:
        raise AnalysisError("Input CSV has no reviews")
    return rows


def redact_pii(text: str) -> str:
    """Mask common emails and phone numbers before sending text to Gemini."""
    text = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[EMAIL]", text, flags=re.I)
    return re.sub(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)", "[PHONE]", text)


def validate_result(value: Any, review_text: str) -> dict[str, Any]:
    """Reject incomplete answers and evidence not found in the review."""
    if not isinstance(value, dict) or set(value) != set(RESPONSE_SCHEMA["required"]):
        raise AnalysisError("Model returned missing or extra fields")
    if value["sentiment"] not in SENTIMENTS or value["priority"] not in PRIORITIES:
        raise AnalysisError("Model returned an invalid sentiment or priority")
    for field in ("summary", "recommended_action"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise AnalysisError(f"Model returned an empty {field}")
    aspects = value["aspects"]
    if not isinstance(aspects, list) or len(aspects) > 3:
        raise AnalysisError("Model must return at most three aspects")
    for aspect in aspects:
        if not isinstance(aspect, dict) or set(aspect) != {"name", "sentiment", "evidence"}:
            raise AnalysisError("Model returned an invalid aspect")
        if aspect["name"] not in ASPECTS or aspect["sentiment"] not in SENTIMENTS:
            raise AnalysisError("Model returned an invalid aspect category")
        evidence = aspect["evidence"]
        if not isinstance(evidence, str) or not evidence or evidence.casefold() not in review_text.casefold():
            raise AnalysisError("Aspect evidence must quote the review exactly")
    return value


def _http_post(url: str, body: bytes, key: str, timeout: float) -> dict[str, Any]:
    """Make one HTTPS request; the API key goes in a header, not the URL."""
    req = request.Request(url, data=body, headers={
        "x-goog-api-key": key, "Content-Type": "application/json"
    }, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def analyze_review(
    text: str,
    prompt: str,
    config: dict[str, Any],
    api_key: str,
    post: Callable[[str, bytes, str, float], dict[str, Any]] = _http_post,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Analyze one review with Gemini and retry temporary API failures."""
    safe_text = redact_pii(text)
    # The system instruction is our prompt; the user content is review data.
    payload = {
        "systemInstruction": {"parts": [{"text": prompt}]},
        "contents": [{"role": "user", "parts": [{"text": safe_text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(RESPONSE_SCHEMA),
        },
    }
    body = json.dumps(payload).encode("utf-8")
    url = f"{config['api_base_url']}/{config['model']}:generateContent"
    attempts = int(config["max_retries"])
    for attempt in range(attempts):
        try:
            response = post(url, body, api_key, float(config["timeout_seconds"]))
            candidate = response["candidates"][0]
            if candidate.get("finishReason") != "STOP":
                raise AnalysisError(f"Model did not finish: {candidate.get('finishReason')}")
            result = json.loads(candidate["content"]["parts"][0]["text"])
            # A schema helps the model respond correctly, but we check again ourselves.
            return validate_result(result, safe_text)
        except error.HTTPError as exc:
            try:
                api_error = json.load(exc).get("error", {})
                error_code = api_error.get("status")
            except (ValueError, AttributeError):
                error_code = None
            label = f"Gemini API returned HTTP {exc.code}" + (f" ({error_code})" if error_code else "")
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise AnalysisError(label) from exc
        except (error.URLError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise AnalysisError(f"API connection failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AnalysisError(f"Malformed API response: {exc}") from exc
        sleep(float(config["retry_base_seconds"]) * (2 ** attempt))
    raise AnalysisError("API retry limit reached")


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep only the JSON Schema fields accepted by Gemini's responseSchema."""
    supported = {"type", "enum", "properties", "required", "items"}
    result = {}
    for name, value in schema.items():
        if name not in supported:
            continue
        if name == "properties":
            result[name] = {key: _gemini_schema(item) for key, item in value.items()}
        elif name == "items":
            result[name] = _gemini_schema(value)
        else:
            result[name] = value
    return result


def cache_key(text: str, prompt: str, config: dict[str, Any]) -> str:
    """Same review, prompt, and model produce the same cache filename."""
    material = json.dumps([redact_pii(text), prompt, config["model"]], ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def analyze_batch(
    reviews: list[dict[str, str]], prompt: str, config: dict[str, Any], api_key: str,
    cache_dir: Path, analyze: Callable[..., dict[str, Any]] = analyze_review,
) -> list[dict[str, Any]]:
    """Analyze every review, reusing saved answers to avoid repeat API calls."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for review in reviews:
        key = cache_key(review["text"], prompt, config)
        cache_path = cache_dir / f"{key}.json"
        try:
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                analysis = validate_result(cached, redact_pii(review["text"]))
            else:
                analysis = analyze(review["text"], prompt, config, api_key)
                cache_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisError(f"Cache failure for review {review['id']}: {exc}") from exc
        except AnalysisError as exc:
            raise AnalysisError(f"Review {review['id']}: {exc}") from exc
        results.append({"id": review["id"], "text": review["text"], "analysis": analysis})
    return results


def build_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Count sentiments, priorities, and topics without calling the model again."""
    sentiments = Counter(item["analysis"]["sentiment"] for item in results)
    priorities = Counter(item["analysis"]["priority"] for item in results)
    aspect_counts = Counter(
        aspect["name"] for item in results for aspect in item["analysis"]["aspects"]
    )
    return {
        "total_reviews": len(results),
        "sentiments": {name: sentiments[name] for name in sorted(SENTIMENTS)},
        "priorities": {name: priorities[name] for name in sorted(PRIORITIES)},
        "aspect_mentions": dict(sorted(aspect_counts.items())),
        "high_priority_ids": [item["id"] for item in results if item["analysis"]["priority"] == "high"],
    }
