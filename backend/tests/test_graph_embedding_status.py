from app.storage.embedding_service import EmbeddingProviderInfo
from app.storage.neo4j_storage import Neo4jStorage


class FakeEmbedding:
    @property
    def info(self):
        return EmbeddingProviderInfo(
            provider="ollama",
            model="nomic-embed-text",
            dimensions=768,
            base_url="http://localhost:11434",
        )


class FakeRecord(dict):
    def __getitem__(self, key):
        return self.get(key)


class FakeResult:
    def __init__(self, record):
        self.record = FakeRecord(record)

    def single(self):
        return self.record


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query, **kwargs):
        if "MATCH (g:Graph" in query:
            return FakeResult({
                "provider": "ollama",
                "model": "nomic-embed-text",
                "dimensions": 768,
                "provider_id": "ollama:nomic-embed-text:768",
            })
        if "MATCH (n:Entity" in query:
            return FakeResult({"total": 3, "embedded": 2})
        if "MATCH ()-[r:RELATION" in query:
            return FakeResult({"total": 2, "embedded": 1})
        raise AssertionError(query)


class FakeDriver:
    def session(self):
        return FakeSession()


def test_embedding_status_reports_coverage_and_safe_to_report_false():
    storage = Neo4jStorage.__new__(Neo4jStorage)
    storage._driver = FakeDriver()
    storage._embedding = FakeEmbedding()

    status = storage.get_embedding_status("graph-1")

    assert status["graph_id"] == "graph-1"
    assert status["node_count"] == 3
    assert status["nodes_embedded"] == 2
    assert status["relationship_count"] == 2
    assert status["relationships_embedded"] == 1
    assert status["safe_to_report"] is False
    assert "Missing embeddings" in status["issues"][0]


def test_reembed_graph_uses_document_texts_and_updates_metadata(monkeypatch):
    storage = Neo4jStorage.__new__(Neo4jStorage)
    storage._embedding = FakeEmbedding()
    updated = {"nodes": [], "relationships": [], "metadata": False}

    class Embedding(FakeEmbedding):
        def embed_batch(self, texts, batch_size=32):
            updated["texts"] = texts
            return [[0.1] * 768 for _ in texts]

    storage._embedding = Embedding()
    storage.get_embedding_status = lambda graph_id: {
        "graph_id": graph_id,
        "compatible": True,
        "safe_to_report": False,
    }

    def fake_read_targets(graph_id):
        return {
            "nodes": [{"uuid": "n1", "text": "Fed: central bank"}],
            "relationships": [{"uuid": "r1", "text": "Fed tightens policy"}],
        }

    def fake_write(graph_id, nodes, relationships):
        updated["nodes"] = nodes
        updated["relationships"] = relationships

    storage._read_embedding_targets = fake_read_targets
    storage._write_embeddings = fake_write
    storage._update_graph_embedding_metadata = lambda graph_id: updated.__setitem__("metadata", True)

    result = storage.reembed_graph("graph-1")

    assert updated["texts"] == ["Fed: central bank", "Fed tightens policy"]
    assert len(updated["nodes"][0]["embedding"]) == 768
    assert len(updated["relationships"][0]["embedding"]) == 768
    assert updated["metadata"] is True
    assert result["embedded_nodes"] == 1
    assert result["embedded_relationships"] == 1
