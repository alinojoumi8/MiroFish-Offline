import hmac
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import Config
from app.models.task import TaskManager
from app.services.report_agent import Report, ReportStatus
from app.services.simulation_manager import SimulationStatus
from app.services.simulation_manager import SimulationState
from app.services.simulation_runner import RunnerStatus, SimulationRunState


DEFAULT_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
]


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def _record(self, level, message, *args, **kwargs):
        if args:
            message = message % args
        self.messages.append((level, message))

    def debug(self, message, *args, **kwargs):
        self._record("debug", message, *args, **kwargs)

    def info(self, message, *args, **kwargs):
        self._record("info", message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self._record("error", message, *args, **kwargs)

    def exception(self, message, *args, **kwargs):
        self._record("exception", message, *args, **kwargs)


def make_app(monkeypatch, logger=None, **overrides):
    import app as app_module
    import app.storage as storage_module
    from app.services.simulation_runner import SimulationRunner

    logger = logger or RecordingLogger()
    monkeypatch.setattr(app_module, "setup_logger", lambda *args, **kwargs: logger)
    monkeypatch.setattr(app_module, "get_logger", lambda *args, **kwargs: logger)
    monkeypatch.setattr(storage_module, "Neo4jStorage", lambda *args, **kwargs: object())
    monkeypatch.setattr(SimulationRunner, "register_cleanup", lambda: None)

    values = {
        "TESTING": True,
        "PROPAGATE_EXCEPTIONS": False,
        "SECRET_KEY": "test-only-secret",
        "DEBUG": False,
        "JSON_AS_ASCII": False,
        "MIROFISH_BIND_HOST": "127.0.0.1",
        "MIROFISH_ALLOWED_ORIGINS": DEFAULT_ORIGINS,
        "MIROFISH_CONTROL_TOKEN": None,
        "LLM_API_KEY": "test-llm-key",
        "NEO4J_URI": "bolt://test.invalid:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "test-password",
    }
    values.update(overrides)
    test_config = type("TestConfig", (), values)
    return create_app(test_config)


def test_config_defaults_are_loopback_debug_off_and_explicit_origins():
    assert Config.MIROFISH_BIND_HOST == "127.0.0.1"
    assert Config.DEBUG is False
    assert Config.MIROFISH_ALLOWED_ORIGINS == DEFAULT_ORIGINS
    assert Config.MIROFISH_CONTROL_TOKEN is None
    assert Config.SECRET_KEY != "mirofish-secret-key"
    assert len(Config.SECRET_KEY) >= 32
    assert Config.NEO4J_PASSWORD is None


def test_missing_neo4j_password_is_a_configuration_error():
    assert "NEO4J_PASSWORD not configured" in Config.validate()


def test_explicit_allowed_origins_are_trimmed_from_comma_list():
    env = os.environ.copy()
    env["MIROFISH_ALLOWED_ORIGINS"] = (
        " https://one.example, http://two.example:3100 ,,"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from app.config import Config; "
                "print(json.dumps(Config.MIROFISH_ALLOWED_ORIGINS))"
            ),
        ],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == [
        "https://one.example",
        "http://two.example:3100",
    ]


def test_non_loopback_bind_requires_control_token(monkeypatch):
    with pytest.raises(ValueError, match="MIROFISH_CONTROL_TOKEN"):
        make_app(
            monkeypatch,
            MIROFISH_BIND_HOST="0.0.0.0",
            MIROFISH_CONTROL_TOKEN="",
        )


def test_factory_rejects_missing_required_llm_configuration(monkeypatch):
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        make_app(monkeypatch, LLM_API_KEY=None)


def test_cors_allows_only_configured_origins_and_control_headers(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()

    allowed = client.get(
        "/api/graph/project/list", headers={"Origin": "http://localhost:3000"}
    )
    rejected = client.get(
        "/api/graph/project/list", headers={"Origin": "https://attacker.invalid"}
    )
    preflight = client.options(
        "/api/simulation/start",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-MiroFish-Control-Token",
        },
    )

    assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "Access-Control-Allow-Origin" not in rejected.headers
    assert preflight.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:3000"
    assert "X-MiroFish-Control-Token" in preflight.headers["Access-Control-Allow-Headers"]
    assert "X-Request-ID" in preflight.headers["Access-Control-Expose-Headers"]


def test_loopback_mutations_remain_token_free(monkeypatch):
    app = make_app(monkeypatch)
    app.add_url_rule(
        "/api/test-mutation",
        "loopback_test_mutation",
        lambda: {"success": True},
        methods=["POST"],
    )

    response = app.test_client().post("/api/test-mutation", json={"value": 1})

    assert response.status_code == 200


def test_external_direct_peer_requires_token_even_with_loopback_bind(monkeypatch):
    app = make_app(monkeypatch)
    app.add_url_rule(
        "/api/test-external-peer",
        "test_external_peer",
        lambda: {"success": True},
        methods=["POST"],
    )

    response = app.test_client().post(
        "/api/test-external-peer",
        environ_base={"REMOTE_ADDR": "192.0.2.44"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "control_token_required"


def test_direct_loopback_peer_does_not_trust_forwarded_for_for_token_guard(monkeypatch):
    app = make_app(monkeypatch)
    app.add_url_rule(
        "/api/test-forwarded-peer",
        "test_forwarded_peer",
        lambda: {"success": True},
        methods=["POST"],
    )

    response = app.test_client().post(
        "/api/test-forwarded-peer",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"X-Forwarded-For": "192.0.2.44"},
    )

    assert response.status_code == 200


def test_lan_mutations_require_token_use_constant_time_compare_and_allow_options(
    monkeypatch,
):
    comparisons = []
    real_compare = hmac.compare_digest

    def recording_compare(provided, configured):
        comparisons.append((provided, configured))
        return real_compare(provided, configured)

    monkeypatch.setattr(hmac, "compare_digest", recording_compare)
    app = make_app(
        monkeypatch,
        MIROFISH_BIND_HOST="0.0.0.0",
        MIROFISH_CONTROL_TOKEN="correct-token",
    )
    app.add_url_rule(
        "/api/test-mutation",
        "lan_test_mutation",
        lambda: {"success": True},
        methods=["POST"],
    )
    client = app.test_client()

    missing = client.post("/api/test-mutation", json={"value": 1})
    wrong = client.post(
        "/api/test-mutation",
        json={"value": 1},
        headers={"X-MiroFish-Control-Token": "wrong-token"},
    )
    accepted = client.post(
        "/api/test-mutation",
        json={"value": 1},
        headers={"X-MiroFish-Control-Token": "correct-token"},
    )
    options = client.options("/api/test-mutation")

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert accepted.status_code == 200
    assert options.status_code < 400
    assert ("wrong-token", "correct-token") in comparisons
    assert ("correct-token", "correct-token") in comparisons


def test_error_envelope_uses_caller_request_id_and_suppresses_tracebacks(monkeypatch):
    app = make_app(monkeypatch)
    app.add_url_rule(
        "/api/test-client-error",
        "test_client_error",
        lambda: (
            {
                "success": False,
                "error": "Invalid input",
                "traceback": "sensitive traceback",
            },
            400,
        ),
    )

    response = app.test_client().get(
        "/api/test-client-error", headers={"X-Request-ID": "caller-request-123"}
    )

    assert response.status_code == 400
    assert response.headers["X-Request-ID"] == "caller-request-123"
    assert response.get_json() == {
        "success": False,
        "error": {
            "code": "bad_request",
            "message": "Invalid input",
            "request_id": "caller-request-123",
        },
    }
    assert "traceback" not in response.get_data(as_text=True).lower()


def test_unexpected_exception_is_generic_and_logged_with_generated_request_id(
    monkeypatch,
):
    logger = RecordingLogger()
    app = make_app(monkeypatch, logger=logger)

    def fail():
        raise RuntimeError("database password leaked in exception")

    app.add_url_rule("/api/test-failure", "test_failure", fail)

    response = app.test_client().get("/api/test-failure")
    payload = response.get_json()
    request_id = response.headers["X-Request-ID"]

    assert response.status_code == 500
    assert payload == {
        "success": False,
        "error": {
            "code": "internal_server_error",
            "message": "An unexpected error occurred",
            "request_id": request_id,
        },
    }
    assert request_id
    assert "password leaked" not in response.get_data(as_text=True)
    assert any(
        level == "exception" and request_id in message
        for level, message in logger.messages
    )


def test_caught_5xx_detail_is_logged_with_request_id_before_suppression(monkeypatch):
    logger = RecordingLogger()
    app = make_app(monkeypatch, logger=logger)
    app.add_url_rule(
        "/api/test-caught-failure",
        "test_caught_failure",
        lambda: (
            {"success": False, "error": "provider secret at /tmp/private"},
            500,
        ),
    )

    response = app.test_client().get(
        "/api/test-caught-failure",
        headers={"X-Request-ID": "caught-request-123"},
    )

    assert "provider secret" not in response.get_data(as_text=True)
    assert any(
        level == "error"
        and "caught-request-123" in message
        and "provider secret at /tmp/private" in message
        for level, message in logger.messages
    )


def test_error_normalization_preserves_protocol_headers(monkeypatch):
    app = make_app(monkeypatch)
    app.add_url_rule(
        "/api/test-retry",
        "test_retry",
        lambda: (
            {"success": False, "error": "Try later"},
            429,
            {"Retry-After": "120"},
        ),
    )
    client = app.test_client()

    method_error = client.post("/api/graph/project/list")
    retry_error = client.get("/api/test-retry")

    assert method_error.status_code == 405
    assert "GET" in method_error.headers["Allow"]
    assert retry_error.headers["Retry-After"] == "120"


def test_unlisted_4xx_uses_http_error_code(monkeypatch):
    app = make_app(monkeypatch)
    app.add_url_rule(
        "/api/test-teapot",
        "test_teapot",
        lambda: ({"success": False, "error": "Short and stout"}, 418),
    )

    response = app.test_client().get("/api/test-teapot")

    assert response.status_code == 418
    assert response.get_json()["error"]["code"] == "http_error"


def test_identifiable_dependency_failure_maps_to_503(monkeypatch):
    app = make_app(monkeypatch)
    app.add_url_rule(
        "/api/test-dependency",
        "test_dependency",
        lambda: (
            {"success": False, "error": "GraphStorage not initialized"},
            500,
        ),
    )

    response = app.test_client().get("/api/test-dependency")

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "dependency_unavailable"
    assert response.get_json()["error"]["message"] == (
        "A required local service is unavailable"
    )


def test_starting_an_already_running_simulation_is_a_conflict(monkeypatch):
    from app.api import simulation as simulation_api

    state = SimpleNamespace(status=SimulationStatus.RUNNING)
    manager = SimpleNamespace(get_simulation=lambda _simulation_id: state)
    run_state = SimpleNamespace(runner_status=RunnerStatus.RUNNING)
    monkeypatch.setattr(simulation_api, "SimulationManager", lambda: manager)
    monkeypatch.setattr(
        simulation_api,
        "_check_simulation_prepared",
        lambda _simulation_id: (True, {}),
    )
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "get_run_state",
        lambda _simulation_id: run_state,
    )
    app = make_app(monkeypatch)

    response = app.test_client().post(
        "/api/simulation/start",
        json={"simulation_id": "sim_0123456789ab"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "conflict"


def test_starting_an_unprepared_simulation_is_a_state_conflict(monkeypatch):
    from app.api import simulation as simulation_api

    state = SimpleNamespace(status=SimulationStatus.CREATED)
    manager = SimpleNamespace(get_simulation=lambda _simulation_id: state)
    monkeypatch.setattr(simulation_api, "SimulationManager", lambda: manager)
    monkeypatch.setattr(
        simulation_api,
        "_check_simulation_prepared",
        lambda _simulation_id: (False, {}),
    )
    app = make_app(monkeypatch)

    response = app.test_client().post(
        "/api/simulation/start",
        json={"simulation_id": "sim_0123456789ab"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "conflict"


def test_stopping_a_non_running_simulation_is_a_state_conflict(monkeypatch):
    from app.api import simulation as simulation_api

    def not_running(_simulation_id):
        raise ValueError("Simulation is not running")

    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "stop_simulation",
        not_running,
    )
    app = make_app(monkeypatch)

    response = app.test_client().post(
        "/api/simulation/stop",
        json={"simulation_id": "sim_0123456789ab"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "conflict"


def test_broad_value_error_does_not_leak_raw_detail(monkeypatch):
    from app.api import simulation as simulation_api

    logger = RecordingLogger()

    def fail_with_detail(_simulation_id):
        raise ValueError("provider key secret at /tmp/provider.json")

    monkeypatch.setattr(simulation_api.logger, "error", logger.error)
    monkeypatch.setattr(simulation_api.logger, "exception", logger.exception)
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "stop_simulation",
        fail_with_detail,
    )
    app = make_app(monkeypatch)

    response = app.test_client().post(
        "/api/simulation/stop",
        json={"simulation_id": "sim_0123456789ab"},
        headers={"X-Request-ID": "stop-request-123"},
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 409
    assert "provider key" not in body
    assert "/tmp/provider.json" not in body
    assert "stop-request-123" in body


def test_failed_task_polling_exposes_only_safe_message_and_request_id(monkeypatch):
    task_manager = TaskManager()
    task_id = task_manager.create_task("test_failure")
    task_manager.fail_task(
        task_id,
        "provider key secret at /tmp/provider.json",
        request_id="task-request-123",
    )
    app = make_app(monkeypatch)

    response = app.test_client().get(f"/api/graph/task/{task_id}")
    data = response.get_json()["data"]

    assert response.status_code == 200
    assert data["status"] == "failed"
    assert data["error"] == "Task failed; check server logs"
    assert "error_request_id" not in data
    assert response.get_json()["error_request_id"] == "task-request-123"
    assert "provider key" not in response.get_data(as_text=True)
    assert "/tmp/provider.json" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("failed_state", "serializer"),
    [
        (
            SimulationState(
                simulation_id="sim_0123456789ab",
                project_id="proj_0123456789ab",
                graph_id="01234567-89ab-4def-8abc-0123456789ab",
                status=SimulationStatus.FAILED,
                error="provider key secret at /tmp/simulation.json",
            ),
            lambda state: state.to_simple_dict(),
        ),
        (
            SimulationRunState(
                simulation_id="sim_0123456789ab",
                runner_status=RunnerStatus.FAILED,
                error="provider key secret at /tmp/runner.log",
            ),
            lambda state: state.to_dict(),
        ),
        (
            Report(
                report_id="report_0123456789ab",
                simulation_id="sim_0123456789ab",
                graph_id="01234567-89ab-4def-8abc-0123456789ab",
                simulation_requirement="test",
                status=ReportStatus.FAILED,
                error="provider key secret at /tmp/report.json",
            ),
            lambda state: state.to_dict(),
        ),
    ],
)
def test_failed_state_polling_serializers_suppress_raw_detail(failed_state, serializer):
    data = serializer(failed_state)

    assert data["error"] == "Operation failed; check server logs"
    assert "provider key" not in json.dumps(data)
    assert "/tmp/" not in json.dumps(data)


def test_simulation_run_state_load_preserves_error_request_id(tmp_path, monkeypatch):
    from app.services.simulation_runner import SimulationRunner

    simulation_id = "sim_0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    (simulation_dir / "run_state.json").write_text(
        json.dumps({
            "simulation_id": simulation_id,
            "runner_status": "failed",
            "error": "Operation failed; check server logs",
            "error_request_id": "failure-request-123",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})

    state = SimulationRunner.get_run_state(simulation_id)

    assert state.error_request_id == "failure-request-123"


def test_request_logging_omits_json_bodies_and_secrets(monkeypatch):
    logger = RecordingLogger()
    app = make_app(monkeypatch, logger=logger)
    app.add_url_rule(
        "/api/test-logging",
        "test_logging",
        lambda: {"success": True},
        methods=["POST"],
    )

    app.test_client().post(
        "/api/test-logging",
        json={"password": "do-not-log-this", "prompt": "private body"},
    )

    messages = "\n".join(message for _level, message in logger.messages)
    assert "Request: POST /api/test-logging" in messages
    assert "do-not-log-this" not in messages
    assert "private body" not in messages
    assert "Request body" not in messages
