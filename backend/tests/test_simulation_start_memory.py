from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import simulation as simulation_api
from app.api import simulation_bp
from app.services.simulation_manager import SimulationStatus
from app.services.simulation_runner import SimulationRunner


class FakeManager:
    def __init__(self, state):
        self.state = state
        self.saved = []

    def get_simulation(self, simulation_id):
        return self.state

    def _save_simulation_state(self, state):
        self.saved.append(state)


def make_app(storage):
    app = Flask(__name__)
    app.register_blueprint(simulation_bp, url_prefix="/api/simulation")
    app.extensions["neo4j_storage"] = storage
    return app


def test_start_simulation_passes_graph_storage_to_runner(monkeypatch):
    storage = object()
    state = SimpleNamespace(
        status=SimulationStatus.READY,
        graph_id="graph-1",
        project_id="project-1",
    )
    manager = FakeManager(state)
    captured = {}

    def fake_start_simulation(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            to_dict=lambda: {
                "simulation_id": kwargs["simulation_id"],
                "runner_status": "running",
                "graph_memory_update_enabled": True,
                "graph_memory_graph_id": kwargs["graph_id"],
            }
        )

    monkeypatch.setattr(simulation_api, "SimulationManager", lambda: manager)
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "start_simulation",
        fake_start_simulation,
    )

    response = make_app(storage).test_client().post(
        "/api/simulation/start",
        json={
            "simulation_id": "sim-1",
            "platform": "parallel",
            "enable_graph_memory_update": True,
        },
    )

    assert response.status_code == 200
    assert captured["storage"] is storage
    assert captured["graph_id"] == "graph-1"
    assert response.get_json()["data"]["graph_memory_update_enabled"] is True


def test_start_simulation_requires_storage_when_graph_memory_enabled(monkeypatch):
    state = SimpleNamespace(
        status=SimulationStatus.READY,
        graph_id="graph-1",
        project_id="project-1",
    )
    monkeypatch.setattr(simulation_api, "SimulationManager", lambda: FakeManager(state))

    def fail_if_called(**kwargs):
        raise AssertionError("runner should not start without graph storage")

    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "start_simulation",
        fail_if_called,
    )

    response = make_app(None).test_client().post(
        "/api/simulation/start",
        json={
            "simulation_id": "sim-1",
            "platform": "parallel",
            "enable_graph_memory_update": True,
        },
    )

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["success"] is False
    assert "GraphStorage" in payload["error"]


def test_runner_rejects_graph_memory_without_storage(tmp_path, monkeypatch):
    simulation_id = "sim_0123456789ab"
    sim_dir = tmp_path / simulation_id
    sim_dir.mkdir()
    (sim_dir / "simulation_config.json").write_text(
        '{"time_config":{"total_simulation_hours":1,"minutes_per_round":60}}',
        encoding="utf-8",
    )
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "run_parallel_simulation.py").write_text("print('unused')", encoding="utf-8")

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "SCRIPTS_DIR", str(script_dir))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_processes", {})
    monkeypatch.setattr(SimulationRunner, "_graph_memory_enabled", {})

    with pytest.raises(ValueError, match="GraphStorage"):
        SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            platform="parallel",
            enable_graph_memory_update=True,
            graph_id="01234567-89ab-4def-8abc-0123456789ab",
            storage=None,
        )
