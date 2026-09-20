from types import SimpleNamespace

import scripts.run_parallel_simulation as parallel_sim


class FakeMemoryStore:
    def __init__(self):
        self.actions = []

    def add_action(self, **kwargs):
        self.actions.append(kwargs)
        return kwargs

    def build_context_for_agent(self, agent_id, limit=8):
        return f"- [twitter] Alice posted a remembered claim for agent {agent_id}"


def test_record_action_memory_writes_logged_action_to_store():
    store = FakeMemoryStore()

    parallel_sim.record_action_memory(
        memory_store=store,
        platform="twitter",
        round_num=4,
        action_data={
            "agent_id": 1,
            "agent_name": "Alice",
            "action_type": "CREATE_POST",
            "action_args": {"content": "Remember this claim"},
            "timestamp": "2026-06-20T10:00:00",
        },
    )

    assert store.actions == [
        {
            "platform": "twitter",
            "round_num": 4,
            "agent_id": 1,
            "agent_name": "Alice",
            "action_type": "CREATE_POST",
            "action_args": {"content": "Remember this claim"},
            "timestamp": "2026-06-20T10:00:00",
            "result": None,
            "success": True,
        }
    ]


def test_attach_memory_context_to_agent_updates_string_persona():
    store = FakeMemoryStore()
    agent = SimpleNamespace(persona="Original persona.")

    changed = parallel_sim.attach_memory_context_to_agent(
        agent=agent,
        memory_store=store,
        agent_id=1,
    )

    assert changed is True
    assert "Original persona." in agent.persona
    assert "Recent simulation memory" in agent.persona
    assert "remembered claim" in agent.persona


def test_attach_memory_context_to_agent_replaces_previous_context():
    store = FakeMemoryStore()
    agent = SimpleNamespace(persona="Original persona.")

    parallel_sim.attach_memory_context_to_agent(agent, store, agent_id=1)
    parallel_sim.attach_memory_context_to_agent(agent, store, agent_id=1)

    assert agent.persona.count("Recent simulation memory") == 1
