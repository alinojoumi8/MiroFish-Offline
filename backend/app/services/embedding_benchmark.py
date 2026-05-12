"""Embedding provider benchmark helpers for CLI and API use."""

import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..storage import Neo4jStorage
from ..storage.embedding_service import EmbeddingService


DEFAULT_QUERIES = [
    "What are the main actors in this event?",
    "Which organizations influence the outcome?",
    "What facts support the strongest market reaction?",
    "Which regulators are involved?",
    "What timeline is implied by the relationships?",
    "Who opposes the main position?",
    "Which entities are connected through financial signals?",
    "What evidence is repeated across sources?",
    "Which agents should be interviewed?",
    "What relationships are most important for the report?",
]


def load_queries(path: Optional[str]) -> List[str]:
    if not path:
        return DEFAULT_QUERIES
    text = Path(path).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(round((pct / 100) * (len(values) - 1))))
    return values[idx]


def summarize_latencies(latencies: List[float]) -> Dict[str, float]:
    return {
        "count": len(latencies),
        "mean_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "p50_ms": round(percentile(latencies, 50), 2),
        "p95_ms": round(percentile(latencies, 95), 2),
    }


def benchmark_provider(provider: str, queries: List[str], graph_id: Optional[str], limit: int) -> Dict[str, Any]:
    service = EmbeddingService(provider=provider)
    result: Dict[str, Any] = {
        "provider": service.info.to_dict(),
        "embedding_latency": {},
        "graph_search_latency": {},
        "queries": [],
        "error": None,
    }

    embed_latencies: List[float] = []
    search_latencies: List[float] = []

    storage = Neo4jStorage(embedding_service=service) if graph_id else None
    try:
        for query in queries:
            query_result: Dict[str, Any] = {
                "query": query,
                "embedding_ms": None,
                "graph_search_ms": None,
                "top_edge_ids": [],
                "top_node_ids": [],
                "manual_relevance_score": None,
                "manual_relevance_notes": "",
                "error": None,
            }

            try:
                start = time.perf_counter()
                service.embed(query)
                elapsed_ms = (time.perf_counter() - start) * 1000
                embed_latencies.append(elapsed_ms)
                query_result["embedding_ms"] = round(elapsed_ms, 2)

                if storage and graph_id:
                    start = time.perf_counter()
                    search = storage.search(graph_id, query, limit=limit, scope="both")
                    search_ms = (time.perf_counter() - start) * 1000
                    search_latencies.append(search_ms)
                    query_result["graph_search_ms"] = round(search_ms, 2)
                    query_result["top_edge_ids"] = [item.get("uuid") for item in search.get("edges", [])]
                    query_result["top_node_ids"] = [item.get("uuid") for item in search.get("nodes", [])]
            except Exception as exc:
                query_result["error"] = str(exc)

            result["queries"].append(query_result)
    finally:
        if storage:
            storage.close()

    result["embedding_latency"] = summarize_latencies(embed_latencies)
    result["graph_search_latency"] = summarize_latencies(search_latencies)
    return result


def add_overlap(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(results) < 2:
        return {}

    left, right = results[0], results[1]
    overlaps = []
    for left_query, right_query in zip(left["queries"], right["queries"]):
        left_ids = set(left_query.get("top_edge_ids") or []) | set(left_query.get("top_node_ids") or [])
        right_ids = set(right_query.get("top_edge_ids") or []) | set(right_query.get("top_node_ids") or [])
        union = left_ids | right_ids
        overlap = len(left_ids & right_ids) / len(union) if union else None
        overlaps.append({
            "query": left_query["query"],
            "overlap": overlap,
            "left_error": left_query.get("error"),
            "right_error": right_query.get("error"),
        })
    return {
        "providers": [left["provider"]["provider_id"], right["provider"]["provider_id"]],
        "top_k_overlap_by_query": overlaps,
    }


def run_embedding_benchmark(
    graph_id: Optional[str],
    providers: List[str],
    queries: Optional[List[str]] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    selected_queries = queries or DEFAULT_QUERIES
    results = [benchmark_provider(provider, selected_queries, graph_id, limit) for provider in providers]
    return {
        "graph_id": graph_id,
        "query_count": len(selected_queries),
        "providers": results,
        "overlap": add_overlap(results),
    }
