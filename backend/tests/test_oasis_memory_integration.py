import asyncio
import importlib
import json
import sqlite3
from types import SimpleNamespace
import pytest

from camel.models import ModelFactory
from camel.types import ModelPlatformType, ModelType, OpenAIBackendRole
from camel.messages import BaseMessage
from oasis.social_agent.agent import SocialAgent, UserInfo
from oasis.social_agent.agents_generator import generate_twitter_agent_graph

from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator
from scripts.simulation_memory import SimulationMemoryStore, attach_memory_context_to_agent


def stub_model():
    return ModelFactory.create(model_platform=ModelPlatformType.DEFAULT, model_type=ModelType.STUB)


def test_generated_twitter_profile_loads_in_installed_oasis(tmp_path):
    profile = OasisAgentProfile(user_id=0, user_name='alice', name='Alice', bio='Bio', persona='Unique persona')
    path = str(tmp_path / 'profiles.csv')
    OasisProfileGenerator.__new__(OasisProfileGenerator)._save_twitter_csv([profile], path)
    graph = asyncio.run(generate_twitter_agent_graph(profile_path=path, model=stub_model()))
    agent = graph.get_agent(0)
    assert 'Unique persona' in agent.system_message.content


def test_memory_reaches_oasis_model_context_without_losing_history(tmp_path):
    agent = SocialAgent(agent_id=0, user_info=UserInfo(user_name='alice', name='Alice', description='Bio', profile={'other_info': {'user_profile': 'Original persona'}}), model=stub_model())
    agent.update_memory(BaseMessage.make_user_message(role_name='User', content='Earlier conversation'), OpenAIBackendRole.USER)
    store = SimulationMemoryStore(tmp_path, {0: 'Alice'})
    store.add_action('twitter', 1, 0, 'Alice', 'CREATE_POST', {'content': 'FIRST_MEMORY'})
    assert attach_memory_context_to_agent(agent, store, 0, limit=1)
    assert 'FIRST_MEMORY' in str(agent.memory.get_context()[0])
    store.add_action('reddit', 2, 0, 'Alice', 'CREATE_POST', {'content': 'SECOND_MEMORY'})
    assert attach_memory_context_to_agent(agent, store, 0, limit=1)
    context = str(agent.memory.get_context()[0])
    assert 'SECOND_MEMORY' in context
    assert 'FIRST_MEMORY' not in context
    assert 'Earlier conversation' in context
    assert 'Original persona' in context
    assert context.count('### Recent simulation memory') == 1


@pytest.mark.parametrize('platform', ['twitter', 'reddit'])
def test_single_platform_rounds_persist_actions(tmp_path, monkeypatch, platform):
    module = importlib.import_module(f'scripts.run_{platform}_simulation')
    runner_type = getattr(module, f'{platform.title()}SimulationRunner')
    runner = runner_type.__new__(runner_type)
    runner.config_path = str(tmp_path / 'config.json')
    runner.simulation_dir = str(tmp_path)
    runner.wait_for_commands = False
    runner.config = {
        'time_config': {'total_simulation_hours': 2, 'minutes_per_round': 60},
        'agent_configs': [{'agent_id': 0, 'entity_name': 'Alice'}],
        'event_config': {'initial_posts': [{'poster_agent_id': 0, 'content': 'Initial'}]},
    }
    profile = tmp_path / 'profile'
    profile.write_text('unused')
    db_path = tmp_path / 'actions.db'
    class Agent:
        persona = 'Persona'
    agent = Agent()
    graph = SimpleNamespace(get_agents=lambda: [(0, agent)], get_agent=lambda _: agent)
    observed_contexts = []
    class Environment:
        agent_graph = graph
        count = 0
        async def reset(self):
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE trace (user_id INTEGER, action TEXT, info TEXT)')
        async def step(self, actions):
            observed_contexts.append(agent.persona)
            self.count += 1
            with sqlite3.connect(db_path) as db:
                db.execute('INSERT INTO trace VALUES (?, ?, ?)', (0, 'create_post', json.dumps({'content': f'Post {self.count}'})))
        async def close(self):
            pass
    async def generate(**kwargs):
        return graph
    monkeypatch.setattr(module, f'generate_{platform}_agent_graph', generate)
    monkeypatch.setattr(module.oasis, 'make', lambda **kwargs: Environment())
    monkeypatch.setattr(module, 'IPCHandler', lambda *args: SimpleNamespace(update_status=lambda _: None))
    monkeypatch.setattr(runner, '_create_model', lambda: None)
    monkeypatch.setattr(runner, '_get_profile_path', lambda: str(profile))
    monkeypatch.setattr(runner, '_get_db_path', lambda: str(db_path))
    monkeypatch.setattr(runner, '_get_active_agents_for_round', lambda *args: [(0, agent)])
    asyncio.run(runner.run())
    records = [json.loads(line) for line in (tmp_path / 'memory/agent_memories.jsonl').read_text().splitlines()]
    assert [r['round'] for r in records] == [0, 1, 2]
    assert [r['content'] for r in records] == ['Post 1', 'Post 2', 'Post 3']
    assert all(r['platform'] == platform for r in records)
    assert 'Post 1' in observed_contexts[1]
    assert 'Post 2' in observed_contexts[2]
