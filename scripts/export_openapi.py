from __future__ import annotations

import json
from pathlib import Path

from app.api.main import app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "contracts" / "openapi" / "v1.json"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(serialized)
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
