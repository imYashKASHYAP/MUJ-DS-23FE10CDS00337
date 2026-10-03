"""Terminal entry point: use web, analyze, or report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import AnalysisError, analyze_batch, build_report, load_config, load_env, load_reviews


ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    """Choose web, analyze, or report based on the terminal command."""
    load_env()
    parser = argparse.ArgumentParser(description="Analyze customer reviews with a Gemini LLM")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="Analyze CSV reviews and create results + report")
    analyze.add_argument("--input", type=Path, required=True, help="CSV with id,text columns")
    analyze.add_argument("--output", type=Path, default=Path("results/analysis.json"))
    analyze.add_argument("--report", type=Path, default=Path("results/report.json"))
    analyze.add_argument("--cache-dir", type=Path, default=Path("cache"))
    analyze.add_argument("--config", type=Path, default=ROOT / "config.json")
    analyze.add_argument("--prompt", type=Path, default=ROOT / "prompts/review_analysis.txt")
    report = sub.add_parser("report", help="Rebuild aggregate report from existing analyses")
    report.add_argument("--input", type=Path, required=True)
    report.add_argument("--output", type=Path, default=Path("results/report.json"))
    web = sub.add_parser("web", help="Start the local browser dashboard")
    web.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    try:
        if args.command == "web":
            # The browser UI uses the same analysis code as the terminal version.
            from .web import serve
            serve(args.port)
        elif args.command == "analyze":
            config = load_config(args.config)
            try:
                prompt = args.prompt.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise AnalysisError(f"Cannot read prompt: {exc}") from exc
            if not prompt:
                raise AnalysisError("Prompt file is empty")
            key = os.environ.get("GEMINI_API_KEY", "")
            if not key:
                raise AnalysisError("Set GEMINI_API_KEY before running analysis")
            reviews = load_reviews(args.input, int(config["max_review_chars"]))
            # Gemini analyzes reviews; Python creates the final count report.
            results = analyze_batch(reviews, prompt, config, key, args.cache_dir)
            _write_json(args.output, results)
            _write_json(args.report, build_report(results))
            print(f"Analyzed {len(results)} reviews; saved {args.output} and {args.report}")
        else:
            try:
                results = json.loads(args.input.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AnalysisError(f"Cannot read analyses: {exc}") from exc
            if not isinstance(results, list) or any(
                not isinstance(item, dict) or not {"id", "analysis"} <= item.keys()
                for item in results
            ):
                raise AnalysisError("Analysis file must contain a list of result objects")
            _write_json(args.output, build_report(results))
            print(f"Saved report to {args.output}")
    except (AnalysisError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
