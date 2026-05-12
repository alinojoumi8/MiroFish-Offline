"""
Application status endpoints.
"""

from flask import current_app, jsonify

from ..config import Config
from . import status_bp


@status_bp.route('/status', methods=['GET'])
def get_status():
    """Report provider and vector-search readiness."""
    storage = current_app.extensions.get('neo4j_storage')

    llm = {
        "provider": Config.LLM_DEFAULT_PROVIDER if hasattr(Config, "LLM_DEFAULT_PROVIDER") else None,
        "base_url": Config.LLM_BASE_URL,
        "model": Config.LLM_MODEL_NAME,
        "configured": bool(Config.LLM_API_KEY),
    }

    if storage is None:
        data = {
            "service": "MiroFish-Offline Backend",
            "healthy": False,
            "llm": llm,
            "neo4j": {
                "healthy": False,
                "uri": Config.NEO4J_URI,
                "error": "GraphStorage not initialized",
            },
            "embedding": {
                "provider": Config.EMBEDDING_PROVIDER,
                "model": Config.EMBEDDING_MODEL if Config.EMBEDDING_PROVIDER == "ollama" else Config.GEMINI_EMBEDDING_MODEL,
                "dimensions": Config.EMBEDDING_DIMENSIONS,
                "healthy": False,
                "error": "GraphStorage not initialized",
            },
            "vector_search_usable": False,
        }
        return jsonify({"success": True, "data": data}), 200

    neo4j_status = storage.health_status()
    embedding_status = neo4j_status.get("embedding", {})
    data = {
        "service": "MiroFish-Offline Backend",
        "healthy": bool(neo4j_status.get("healthy") and embedding_status.get("healthy")),
        "llm": llm,
        "neo4j": {k: v for k, v in neo4j_status.items() if k != "embedding"},
        "embedding": embedding_status,
        "vector_search_usable": bool(neo4j_status.get("vector_search_usable")),
    }
    return jsonify({"success": True, "data": data}), 200
