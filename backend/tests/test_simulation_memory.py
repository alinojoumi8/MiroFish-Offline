import json

from scripts.simulation_memory import SimulationMemoryStore


def read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_add_action_writes_structured_memory_record(tmp_path):
    store = SimulationMemoryStore(tmp_path, agent_names={1: "Alice"})

    record = store.add_action(
        platform="twitter",
        round_num=2,
        agent_id=1,
        agent_name="Alice",
        action_type="CREATE_POST",
        action_args={"content": "Local models are shaping the debate."},
        timestamp="2026-06-20T10:00:00",
    )

    memory_file = tmp_path / "memory" / "agent_memories.jsonl"
    rows = read_jsonl(memory_file)

    assert record["agent_id"] == 1
    assert rows == [record]
    assert rows[0]["platform"] == "twitter"
    assert rows[0]["round"] == 2
    assert rows[0]["action_type"] == "CREATE_POST"
    assert rows[0]["content"] == "Local models are shaping the debate."
    assert rows[0]["target_agent_id"] is None
    assert "Alice posted" in rows[0]["text"]


def test_cross_platform_retrieval_includes_actor_and_target_memories(tmp_path):
    store = SimulationMemoryStore(tmp_path, agent_names={1: "Alice", 2: "Bob"})

    store.add_action(
        platform="twitter",
        round_num=1,
        agent_id=1,
        agent_name="Alice",
        action_type="CREATE_POST",
        action_args={"content": "The launch will change the market."},
        timestamp="2026-06-20T10:00:00",
    )
    store.add_action(
        platform="reddit",
        round_num=3,
        agent_id=2,
        agent_name="Bob",
        action_type="LIKE_POST",
        action_args={
            "post_author_name": "Alice",
            "post_content": "The launch will change the market.",
        },
        timestamp="2026-06-20T11:00:00",
    )

    memories = store.get_agent_memories(agent_id=1, limit=10)
    context = store.build_context_for_agent(agent_id=1, limit=10)

    assert [m["platform"] for m in memories] == ["reddit", "twitter"]
    assert memories[0]["target_agent_id"] == 1
    assert "Bob liked Alice's post" in memories[0]["text"]
    assert "[reddit]" in context
    assert "[twitter]" in context


def test_cached_memory_reads_cross_platform_appends_once(tmp_path, monkeypatch):
    import scripts.simulation_memory as module
    first = module.SimulationMemoryStore(tmp_path)
    second = module.SimulationMemoryStore(tmp_path)
    assert first.get_agent_memories(1) == []
    first.add_action('twitter', 1, 1, 'Alice', 'CREATE_POST', {'content': 'One'})
    calls = []
    original_loads = module.json.loads
    def counted_loads(value):
        calls.append(value)
        return original_loads(value)
    monkeypatch.setattr(module.json, 'loads', counted_loads)
    assert len(first.get_agent_memories(1)) == 1
    assert len(first.get_agent_memories(1)) == 1
    assert len(calls) == 1
    second.add_action('reddit', 2, 1, 'Alice', 'CREATE_POST', {'content': 'Two'})
    assert [r['content'] for r in first.get_agent_memories(1)] == ['Two', 'One']
    assert len(calls) == 2


def test_memory_cache_waits_for_complete_line_and_handles_replacement(tmp_path):
    import scripts.simulation_memory as module
    store = module.SimulationMemoryStore(tmp_path)
    store.memory_path.write_bytes(b'{"participants": [1], "content": "partial"')
    assert store.get_agent_memories(1) == []
    with store.memory_path.open('ab') as handle:
        handle.write(b'}\n')
    assert store.get_agent_memories(1)[0]['content'] == 'partial'
    replacement = tmp_path / 'replacement'
    replacement.write_text('{"participants": [1], "content": "replacement"}\n')
    replacement.replace(store.memory_path)
    assert [r['content'] for r in store.get_agent_memories(1)] == ['replacement']
