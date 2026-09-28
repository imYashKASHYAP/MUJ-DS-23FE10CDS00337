# Code Directory

This directory references the complete source code implementation for the **FeedbackLens** capstone project.

### Core Modules:
- **`feedbacklens/`**: Main Python package containing pipeline, server, and static UI assets.
  - [`core.py`](../feedbacklens/core.py): PII redaction, Gemini REST API caller, anti-hallucination validation, caching, report aggregation.
  - [`web.py`](../feedbacklens/web.py): Local threaded HTTP server keeping API key secure.
  - [`__main__.py`](../feedbacklens/__main__.py): Command-line interface.
  - [`static/`](../feedbacklens/static/): Vanilla HTML5 / CSS3 / ES6+ JavaScript frontend.
- **`run.py`**: Auto-detects virtual environment and launches the dashboard in browser.
- **`tests/test_core.py`**: Offline test suite with 100% mocked API calls.
