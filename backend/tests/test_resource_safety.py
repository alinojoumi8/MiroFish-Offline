import json

import pytest
from flask import Flask

from app.api import report_bp, simulation as simulation_api, simulation_bp
from app.models.project import ProjectManager
from app.services.report_agent import ReportManager
from app.services.simulation_manager import SimulationManager
from app.services.simulation_runner import SimulationRunner
from app.utils.resource_safety import (
    ResourceValidationError,
    resolve_resource_path,
    validate_graph_id,
    validate_project_id,
    validate_report_id,
    validate_script_name,
    validate_simulation_id,
    validate_task_id,
)


PROJECT_ID = 'proj_0123456789ab'
SIMULATION_ID = 'sim_0123456789ab'
REPORT_ID = 'report_0123456789ab'
UUID_ID = '123e4567-e89b-12d3-a456-426614174000'


@pytest.mark.parametrize(
    ('validator', 'value'),
    [
        (validate_project_id, PROJECT_ID),
        (validate_simulation_id, SIMULATION_ID),
        (validate_report_id, REPORT_ID),
        (validate_task_id, UUID_ID),
        (validate_graph_id, UUID_ID),
    ],
)
def test_valid_generated_resource_ids_are_accepted(validator, value):
    assert validator(value) == value


@pytest.mark.parametrize('value', [
    '..',
    '%2e%2e',
    '/tmp/resource',
    'proj_0123456789ab/child',
    'proj_0123456789ab\\child',
    'proj_0123456789aB',
])
def test_project_id_rejects_path_like_or_malformed_values(value):
    with pytest.raises(ResourceValidationError):
        validate_project_id(value)


def test_resolve_resource_path_rejects_storage_root_and_symlink_escape(tmp_path):
    root = tmp_path / 'resources'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / PROJECT_ID).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ResourceValidationError):
        resolve_resource_path(root)
    with pytest.raises(ResourceValidationError):
        resolve_resource_path(root, PROJECT_ID)


def test_resource_managers_reject_unsafe_ids_before_filesystem_access(tmp_path, monkeypatch):
    projects_dir = tmp_path / 'projects'
    reports_dir = tmp_path / 'reports'
    simulations_dir = tmp_path / 'simulations'
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(projects_dir))
    monkeypatch.setattr(ReportManager, 'REPORTS_DIR', str(reports_dir))
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(simulations_dir))

    manager = SimulationManager()
    for unsafe_id in ('..', '%2e%2e', '/tmp/resource', f'{SIMULATION_ID}/child'):
        with pytest.raises(ResourceValidationError):
            ProjectManager.get_project(unsafe_id)
        with pytest.raises(ResourceValidationError):
            ReportManager.get_report(unsafe_id)
        with pytest.raises(ResourceValidationError):
            manager.get_simulation(unsafe_id)


def test_resource_managers_keep_well_formed_missing_ids_as_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(tmp_path / 'projects'))
    monkeypatch.setattr(ReportManager, 'REPORTS_DIR', str(tmp_path / 'reports'))
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(tmp_path / 'simulations'))

    manager = SimulationManager()
    assert ProjectManager.get_project(PROJECT_ID) is None
    assert ReportManager.get_report(REPORT_ID) is None
    assert manager.get_simulation(SIMULATION_ID) is None


def test_project_delete_rejects_storage_root_and_preserves_unrelated_files(tmp_path, monkeypatch):
    projects_dir = tmp_path / 'projects'
    projects_dir.mkdir()
    sentinel = projects_dir / 'keep.txt'
    sentinel.write_text('keep', encoding='utf-8')
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(projects_dir))

    with pytest.raises(ResourceValidationError):
        ProjectManager.delete_project('..')

    assert sentinel.read_text(encoding='utf-8') == 'keep'


def test_script_download_allowlist_is_exact():
    assert validate_script_name('run_parallel_simulation.py') == 'run_parallel_simulation.py'
    with pytest.raises(ResourceValidationError):
        validate_script_name('../run_parallel_simulation.py')


def test_report_task_status_rejects_malformed_body_id_and_keeps_missing_valid_id_as_404():
    app = Flask(__name__)
    app.config['STRICT_RESOURCE_ID_VALIDATION'] = True
    app.register_blueprint(report_bp, url_prefix='/api/report')
    client = app.test_client()

    invalid_response = client.post('/api/report/generate/status', json={'task_id': '../outside'})
    valid_response = client.post('/api/report/generate/status', json={'task_id': UUID_ID})

    assert invalid_response.status_code == 400
    assert valid_response.status_code == 404


def test_project_metadata_leaf_symlink_is_rejected(tmp_path, monkeypatch):
    projects_dir = tmp_path / 'projects'
    project_dir = projects_dir / PROJECT_ID
    project_dir.mkdir(parents=True)
    outside_metadata = tmp_path / 'outside-project.json'
    outside_metadata.write_text(json.dumps({
        'project_id': PROJECT_ID,
        'name': 'outside',
        'status': 'created',
        'created_at': '',
        'updated_at': '',
    }), encoding='utf-8')
    (project_dir / 'project.json').symlink_to(outside_metadata)
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(projects_dir))

    with pytest.raises(ResourceValidationError):
        ProjectManager.get_project(PROJECT_ID)


def test_simulation_cleanup_rejects_leaf_symlinked_action_log(tmp_path, monkeypatch):
    simulation_dir = tmp_path / SIMULATION_ID
    simulation_dir.mkdir()
    outside_platform_dir = tmp_path / 'outside-twitter'
    outside_platform_dir.mkdir()
    outside_actions = outside_platform_dir / 'actions.jsonl'
    outside_actions.write_text('keep', encoding='utf-8')
    (simulation_dir / 'twitter').symlink_to(outside_platform_dir, target_is_directory=True)
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(tmp_path))

    with pytest.raises(ResourceValidationError):
        SimulationRunner.cleanup_simulation_logs(SIMULATION_ID)

    assert outside_actions.read_text(encoding='utf-8') == 'keep'


def test_simulation_config_download_rejects_leaf_symlink(tmp_path, monkeypatch):
    simulation_dir = tmp_path / SIMULATION_ID
    simulation_dir.mkdir()
    outside_config = tmp_path / 'outside-config.json'
    outside_config.write_text('{"secret": true}', encoding='utf-8')
    (simulation_dir / 'simulation_config.json').symlink_to(outside_config)
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(tmp_path))

    app = Flask(__name__)
    app.config['STRICT_RESOURCE_ID_VALIDATION'] = True
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')

    response = app.test_client().get(
        f'/api/simulation/{SIMULATION_ID}/config/download'
    )

    assert response.status_code == 400


@pytest.mark.parametrize('url', [
    '/api/simulation/list?project_id=../outside',
    '/api/report/list?simulation_id=../outside',
])
def test_resource_list_queries_reject_malformed_ids(url):
    app = Flask(__name__)
    app.config['STRICT_RESOURCE_ID_VALIDATION'] = True
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')

    response = app.test_client().get(url)

    assert response.status_code == 400


def test_report_lookup_skips_symlinked_report_metadata(tmp_path, monkeypatch):
    reports_dir = tmp_path / 'backend' / 'uploads' / 'reports'
    reports_dir.mkdir(parents=True)
    fake_api_dir = tmp_path / 'backend' / 'app' / 'api'
    fake_api_dir.mkdir(parents=True)
    outside_report = tmp_path / 'outside-report'
    outside_report.mkdir()
    (outside_report / 'meta.json').write_text(json.dumps({
        'report_id': REPORT_ID,
        'simulation_id': SIMULATION_ID,
        'created_at': '2026-01-01T00:00:00',
        'status': 'completed',
    }), encoding='utf-8')
    (reports_dir / REPORT_ID).symlink_to(outside_report, target_is_directory=True)
    monkeypatch.setattr(ReportManager, 'REPORTS_DIR', str(reports_dir))
    monkeypatch.setattr(simulation_api.os.path, 'dirname', lambda _: str(fake_api_dir))

    assert simulation_api._get_report_id_for_simulation(SIMULATION_ID) is None
