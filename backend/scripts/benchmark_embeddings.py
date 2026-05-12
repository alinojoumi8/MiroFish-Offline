"""
Benchmark embedding providers and compatible graph search.

Examples:
  uv run python scripts/benchmark_embeddings.py --queries queries.txt
  uv run python scripts/benchmark_embeddings.py --graph-id <graph_id> --providers ollama gemini
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.embedding_benchmark import load_queries, run_embedding_benchmark  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark MiroFish embedding providers")
    parser.add_argument("--queries", help="Text file with one query per line")
    parser.add_argument("--graph-id", help="Optional graph id for compatible graph search")
    parser.add_argument("--providers", nargs="+", default=["ollama"], choices=["ollama", "gemini"])
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()

    report = run_embedding_benchmark(
        graph_id=args.graph_id,
        providers=args.providers,
        queries=load_queries(args.queries),
        limit=args.limit,
    )

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
