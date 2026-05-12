import pytest

from app.storage.embedding_service import EmbeddingError, EmbeddingProviderInfo
from app.storage.neo4j_storage import Neo4jStorage


class FakeEmbedding:
    def __init__(self, provider="ollama", model="nomic-embed-text"):
        self.provider = provider
        self.model = model

    @property
    def info(self):
        return EmbeddingProviderInfo(
            provider=self.provider,
            model=self.model,
            dimensions=768,
            base_url="http://localhost:11434",
        )


def test_graph_embedding_mismatch_requires_reembed(monkeypatch):
    storage = Neo4jStorage.__new__(Neo4jStorage)
    storage._embedding = FakeEmbedding()

    monkeypatch.setattr(
        storage,
        "get_graph_embedding_info",
        lambda graph_id: {
            "provider": "gemini",
            "model": "gemini-embedding-2",
            "dimensions": 768,
            "provider_id": "gemini:gemini-embedding-2:768",
        },
    )

    with pytest.raises(EmbeddingError, match="Rebuild or re-embed"):
        storage._assert_embedding_compatible("graph-1")


def test_legacy_graph_metadata_refuses_non_default_provider(monkeypatch):
    storage = Neo4jStorage.__new__(Neo4jStorage)
    storage._embedding = FakeEmbedding(provider="gemini", model="gemini-embedding-2")

    monkeypatch.setattr(
        storage,
        "get_graph_embedding_info",
        lambda graph_id: {
            "provider": None,
            "model": None,
            "dimensions": None,
            "provider_id": None,
        },
    )

    with pytest.raises(EmbeddingError, match="no embedding provider metadata"):
        storage._assert_embedding_compatible("legacy-graph")
