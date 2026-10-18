"""Launch the Lifeline app from the backend/ folder.

Prefer running from the project root:
    python app.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app  # noqa: E402


if __name__ == "__main__":
    app.run(debug=True)
