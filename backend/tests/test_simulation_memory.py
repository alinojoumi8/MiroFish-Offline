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
