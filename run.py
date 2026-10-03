"""Run FeedbackLens without typing module commands."""

from pathlib import Path
import subprocess
import sys
import webbrowser

from feedbacklens.core import load_env
from feedbacklens.web import serve
import feedbacklens.__main__ as cli


def main():
    venv_py = Path(__file__).resolve().parent / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        return subprocess.call([str(venv_py), *sys.argv])

    load_env()

    if len(sys.argv) > 1:
        if sys.argv[1] in ("test", "--test", "-t"):
            import unittest
            suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent / "tests"))
            return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
        return cli.main(sys.argv[1:])

    url = "http://127.0.0.1:8765"
    print(f"Starting FeedbackLens on {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    serve(8765)


if __name__ == "__main__":
    sys.exit(main() or 0)
