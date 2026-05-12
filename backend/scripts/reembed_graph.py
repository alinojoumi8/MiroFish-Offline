"""
Backfill embeddings for an existing MiroFish graph.

Example:
  uv run python scripts/reembed_graph.py --graph-id 78860c01-3c78-4020-ab24-631dc78e85df
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import Neo4jStorage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-embed a graph with the active embedding provider")
    parser.add_argument("--graph-id", required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()

    storage = Neo4jStorage()
    try:
        before = storage.get_embedding_status(args.graph_id)
        if args.status_only:
            print(json.dumps({"before": before}, ensure_ascii=False, indent=2))
            return 0 if before.get("safe_to_report") else 2

        result = storage.reembed_graph(args.graph_id, batch_size=args.batch_size)
        print(json.dumps({"before": before, "result": result}, ensure_ascii=False, indent=2))
        return 0 if result["status"].get("safe_to_report") else 2
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
