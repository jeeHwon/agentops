"""Write docs/api.md (and optionally the OpenAPI JSON) from the aops API routes.

    uv run --extra serve python scripts/api_docs.py [--openapi <file>]
"""

import argparse
import json
from pathlib import Path

from aops.server import create_app
from aops.server.mapping import markdown

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--openapi", type=Path, help="OpenAPI JSON도 이 경로에 쓴다")
    opts = args.parse_args()
    app = create_app()
    (ROOT / "docs" / "api.md").write_text(markdown(app), encoding="utf-8")
    if opts.openapi:
        opts.openapi.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote docs/api.md" + (f" and {opts.openapi}" if opts.openapi else ""))


if __name__ == "__main__":
    main()
