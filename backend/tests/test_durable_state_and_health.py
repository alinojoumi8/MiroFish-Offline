import json
import errno
import multiprocessing
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import create_app
from app.models.project import Project, ProjectManager, ProjectStatus
from app.models.task import TaskManager, TaskStatus
from app.services.report_agent import ReportManager
from app.services.simulation_manager import (
    SimulationManager,
    SimulationState,
    SimulationStatus,
)


def _manager(tmp_path):
    return TaskManager(db_path=tmp_path / "tasks.sqlite3")


def _increment_locked_state(state_path, lock_path, iterations):
    from app.utils.atomic_state import atomic_write_json, resource_lock

    for _ in range(iterations):
        with resource_lock("shared-counter", lock_path=lock_path):
            current = json.loads(Path(state_path).read_text(encoding="utf-8"))
            current["value"] += 1
            atomic_write_json(state_path, current)


def test_task_registry_survives_manager_restart(tmp_path):
    manager = _manager(tmp_path)
    task_id = manager.create_task("build_graph", {"project_id": "proj_123"})
    manager.update_task(
        task_id,
        status=TaskStatus.COMPLETED,
        progress=100,
        message="done",
        result={"graph_id": "graph-123"},
        progress_detail={"stage": "persisted"},
    )

    restored = _manager(tmp_path).get_task(task_id)

    assert restored is not None
    assert restored.to_dict() == manager.get_task(task_id).to_dict()


def test_startup_interrupts_stale_active_tasks_and_preserves_request_id(tmp_path):
    manager = _manager(tmp_path)
    task_id = manager.create_task("prepare")
    old = (datetime.now() - timedelta(hours=2)).isoformat()
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        worker_id = connection.execute(
            "SELECT owner_worker_id FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE tasks SET status = ?, updated_at = ?, error_request_id = ? WHERE task_id = ?",
            (TaskStatus.PROCESSING.value, old, "request-before-restart", task_id),
        )
        connection.execute(
            "UPDATE workers SET heartbeat_at = ? WHERE worker_id = ?",
            (old, worker_id),
        )

    task = _manager(tmp_path).get_task(task_id)

    assert task.status == TaskStatus.INTERRUPTED
    assert task.error == "Task interrupted by server restart"
    assert task.error_request_id == "request-before-restart"


def test_second_live_manager_does_not_interrupt_first_managers_task(tmp_path):
    first = _manager(tmp_path)
    task_id = first.create_task("prepare")
    first.update_task(task_id, status=TaskStatus.PROCESSING)

    second = _manager(tmp_path)

    assert second.get_task(task_id).status == TaskStatus.PROCESSING


def test_periodic_reaper_interrupts_task_after_owner_lease_expires(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    owner = TaskManager(
        db_path=db_path, lease_seconds=0.2, heartbeat_interval=0.04
    )
    reaper = TaskManager(
        db_path=db_path, lease_seconds=0.2, heartbeat_interval=0.04
    )
    task_id = owner.create_task("lease-expiry")
    owner.update_task(task_id, status=TaskStatus.PROCESSING)
    owner._stop_heartbeat.set()

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        if reaper.get_task(task_id).status == TaskStatus.INTERRUPTED:
            break
        time.sleep(0.04)

    assert reaper.get_task(task_id).status == TaskStatus.INTERRUPTED
    owner.close()
    reaper.close()


def test_ownerless_legacy_active_task_is_recovered(tmp_path):
    manager = _manager(tmp_path)
    task_id = manager.create_task("legacy-ownerless")
    manager.update_task(task_id, status=TaskStatus.PROCESSING)
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        connection.execute(
            "UPDATE tasks SET owner_worker_id = NULL WHERE task_id = ?", (task_id,)
        )

    recovered = _manager(tmp_path).get_task(task_id)

    assert recovered.status == TaskStatus.INTERRUPTED


def test_sqlite_is_authoritative_across_manager_instances(tmp_path):
    first = _manager(tmp_path)
    second = _manager(tmp_path)
    task_id = first.create_task("cross-instance")
    assert second.get_task(task_id).progress == 0

    first.update_task(task_id, progress=88, message="committed")

    assert second.get_task(task_id).progress == 88
    assert second.list_tasks()[0]["message"] == "committed"


def test_task_to_dict_preserves_original_public_shape(tmp_path):
    manager = _manager(tmp_path)
    task_id = manager.create_task("shape")
    manager.fail_task(task_id, request_id="internal-correlation")

    assert "error_request_id" not in manager.get_task(task_id).to_dict()


def test_schema_migrates_v0_to_current_and_rejects_future_versions(tmp_path):
    current_path = tmp_path / "current.sqlite3"
    with sqlite3.connect(current_path) as connection:
        connection.execute("PRAGMA user_version = 0")

    manager = TaskManager(db_path=current_path)
    with sqlite3.connect(current_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        worker_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workers'"
        ).fetchone()

    assert version == manager.schema_version
    assert {"owner_worker_id", "finished_at", "retry_source_id"} <= task_columns
    assert worker_table == (1,)

    future_path = tmp_path / "future.sqlite3"
    with sqlite3.connect(future_path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="newer schema version"):
        TaskManager(db_path=future_path)


def test_concurrent_retry_is_idempotent_and_creates_one_child(tmp_path):
    first = _manager(tmp_path)
    second = _manager(tmp_path)
    source_id = first.create_task("retry-once", {"value": 1})
    first.fail_task(source_id)
    barrier = threading.Barrier(3)
    retry_ids = []

    def retry(manager):
        barrier.wait()
        retry_ids.append(manager.retry_task(source_id))

    threads = [threading.Thread(target=retry, args=(first,)), threading.Thread(target=retry, args=(second,))]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=3)

    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE retry_source_id = ?", (source_id,)
        ).fetchone()[0]

    assert len(set(retry_ids)) == 1
    assert count == 1


def test_cleanup_retention_uses_terminal_time_not_creation_time(tmp_path):
    manager = _manager(tmp_path)
    retained_id = manager.create_task("old-created-recently-finished")
    deleted_id = manager.create_task("recent-created-old-finished")
    old = (datetime.now() - timedelta(days=2)).isoformat()
    recent = datetime.now().isoformat()
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        connection.execute(
            "UPDATE tasks SET status = ?, created_at = ?, updated_at = ?, finished_at = ? WHERE task_id = ?",
            (TaskStatus.COMPLETED.value, old, recent, recent, retained_id),
        )
        connection.execute(
            "UPDATE tasks SET status = ?, created_at = ?, updated_at = ?, finished_at = ? WHERE task_id = ?",
            (TaskStatus.COMPLETED.value, recent, old, old, deleted_id),
        )

    assert manager.cleanup_old_tasks(max_age_hours=24, limit=10) == 1
    assert manager.get_task(retained_id) is not None
    assert manager.get_task(deleted_id) is None


def test_retry_creates_pending_task_with_lineage_and_original_metadata(tmp_path):
    manager = _manager(tmp_path)
    original_id = manager.create_task("report", {"simulation_id": "sim_123"})
    manager.fail_task(original_id, "private detail", "failed-request")

    retry_id = manager.retry_task(original_id)
    retry = manager.get_task(retry_id)

    assert retry_id != original_id
    assert retry.status == TaskStatus.PENDING
    assert retry.task_type == "report"
    assert retry.metadata == {
        "simulation_id": "sim_123",
        "retry_of_task_id": original_id,
        "retry_attempt": 1,
    }


def test_concurrent_task_updates_preserve_disjoint_fields(tmp_path):
    manager = _manager(tmp_path)
    task_id = manager.create_task("concurrent")
    barrier = threading.Barrier(3)

    def update_progress():
        barrier.wait()
        manager.update_task(task_id, progress=70)

    def update_message():
        barrier.wait()
        manager.update_task(task_id, message="still running")

    threads = [threading.Thread(target=update_progress), threading.Thread(target=update_message)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    task = manager.get_task(task_id)
    assert task.progress == 70
    assert task.message == "still running"


def test_cleanup_is_terminal_only_and_bounded(tmp_path):
    manager = _manager(tmp_path)
    terminal_ids = [manager.create_task("old") for _ in range(3)]
    active_id = manager.create_task("active")
    old = (datetime.now() - timedelta(days=2)).isoformat()
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        connection.executemany(
            "UPDATE tasks SET status = ?, created_at = ?, updated_at = ? WHERE task_id = ?",
            [(TaskStatus.COMPLETED.value, old, old, task_id) for task_id in terminal_ids],
        )
        connection.execute(
            "UPDATE tasks SET status = ?, created_at = ?, updated_at = ? WHERE task_id = ?",
            (TaskStatus.PROCESSING.value, old, old, active_id),
        )

    deleted = manager.cleanup_old_tasks(max_age_hours=24, limit=2)

    assert deleted == 2
    assert sum(manager.get_task(task_id) is not None for task_id in terminal_ids) == 1
    assert manager.get_task(active_id) is not None


def test_atomic_write_replaces_complete_file_and_cleans_temp_on_failure(tmp_path, monkeypatch):
    from app.utils.atomic_state import atomic_write_text

    destination = tmp_path / "state.json"
    destination.write_text("old", encoding="utf-8")
    atomic_write_text(destination, "new")
    assert destination.read_text(encoding="utf-8") == "new"

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(destination, "partial")

    assert destination.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_keyed_locks_serialize_same_resource_but_not_different_resources():
    from app.utils.atomic_state import resource_lock

    first_entered = threading.Event()
    release_first = threading.Event()
    same_entered = threading.Event()
    other_entered = threading.Event()

    def hold_first():
        with resource_lock("simulation:one"):
            first_entered.set()
            release_first.wait(timeout=2)

    def enter_same():
        first_entered.wait(timeout=2)
        with resource_lock("simulation:one"):
            same_entered.set()

    def enter_other():
        first_entered.wait(timeout=2)
        with resource_lock("simulation:two"):
            other_entered.set()

    threads = [
        threading.Thread(target=hold_first),
        threading.Thread(target=enter_same),
        threading.Thread(target=enter_other),
    ]
    for thread in threads:
        thread.start()
    assert first_entered.wait(timeout=2)
    assert other_entered.wait(timeout=2)
    assert not same_entered.wait(timeout=0.05)
    release_first.set()
    for thread in threads:
        thread.join(timeout=2)
    assert same_entered.is_set()


def test_resource_lock_serializes_across_processes(tmp_path):
    state_path = tmp_path / "counter.json"
    lock_path = tmp_path / ".counter.lock"
    state_path.write_text('{"value": 0}', encoding="utf-8")
    processes = [
        multiprocessing.Process(
            target=_increment_locked_state,
            args=(str(state_path), str(lock_path), 25),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)

    assert [process.exitcode for process in processes] == [0, 0]
    assert json.loads(state_path.read_text(encoding="utf-8"))["value"] == 50


@pytest.mark.parametrize("fsync_errno,raises", [(errno.EINVAL, False), (errno.EIO, True)])
def test_directory_fsync_suppresses_only_unsupported_errors(
    tmp_path, monkeypatch, fsync_errno, raises
):
    from app.utils.atomic_state import atomic_write_text

    real_fsync = os.fsync
    calls = 0

    def fail_directory_only(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(fsync_errno, "directory fsync")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_only)
    if raises:
        with pytest.raises(OSError) as error:
            atomic_write_text(tmp_path / "state.txt", "new")
        assert error.value.errno == errno.EIO
    else:
        atomic_write_text(tmp_path / "state.txt", "new")
        assert (tmp_path / "state.txt").read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("manager_kind", ["project", "simulation", "report"])
def test_state_managers_keep_previous_file_when_atomic_replace_fails(
    tmp_path, monkeypatch, manager_kind
):
    project_id = "proj_0123456789ab"
    simulation_id = "sim_0123456789ab"
    report_id = "report_0123456789ab"

    if manager_kind == "project":
        monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
        project = Project(
            project_id=project_id,
            name="before",
            status=ProjectStatus.CREATED,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        project_dir = Path(ProjectManager._get_project_dir(project_id))
        project_dir.mkdir(parents=True)
        target = Path(ProjectManager._get_project_meta_path(project_id))
        target.write_text('{"name":"before"}', encoding="utf-8")
        operation = lambda: ProjectManager.save_project(project)
    elif manager_kind == "simulation":
        monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path / "simulations"))
        manager = SimulationManager()
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id="01234567-89ab-4def-8abc-0123456789ab",
            status=SimulationStatus.CREATED,
        )
        simulation_dir = Path(manager._get_simulation_dir(simulation_id, create=True))
        target = simulation_dir / "state.json"
        target.write_text('{"status":"before"}', encoding="utf-8")
        operation = lambda: manager._save_simulation_state(state)
    else:
        monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path / "reports"))
        report_dir = Path(ReportManager._ensure_report_folder(report_id))
        target = report_dir / "progress.json"
        target.write_text('{"status":"before"}', encoding="utf-8")
        operation = lambda: ReportManager.update_progress(report_id, "running", 50, "half")

    original = target.read_text(encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        operation()

    assert target.read_text(encoding="utf-8") == original


def test_simulation_profile_save_keeps_previous_file_when_replace_fails(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from app.services.oasis_profile_generator import OasisProfileGenerator

    target = tmp_path / "reddit_profiles.json"
    target.write_text('[{"before":true}]', encoding="utf-8")
    profile = SimpleNamespace(
        to_reddit_format=lambda: {
            "user_id": 1,
            "realname": "Test",
            "username": "test",
            "bio": "bio",
            "persona": "persona",
            "age": 30,
            "gender": "other",
            "mbti": "INTJ",
            "country": "CA",
        }
    )
    generator = object.__new__(OasisProfileGenerator)

    monkeypatch.setattr(
        os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed"))
    )
    with pytest.raises(OSError, match="replace failed"):
        generator.save_profiles([profile], str(target), platform="reddit")

    assert target.read_text(encoding="utf-8") == '[{"before":true}]'


def test_invalid_graph_memory_id_persists_failed_restartable_state(tmp_path, monkeypatch):
    from app.services.simulation_runner import RunnerStatus, SimulationRunner

    simulation_id = "sim_0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    (simulation_dir / "simulation_config.json").write_text(
        json.dumps({"time_config": {"total_simulation_hours": 1, "minutes_per_round": 30}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})

    with pytest.raises(ValueError):
        SimulationRunner.start_simulation(
            simulation_id,
            enable_graph_memory_update=True,
            graph_id="../invalid",
            storage=object(),
        )

    state = SimulationRunner.get_run_state(simulation_id)
    assert state.runner_status == RunnerStatus.FAILED
    assert state.process_pid is None
    assert state.graph_memory_update_enabled is False


def _proc_identity(process):
    # Popen may return before Linux exposes the post-exec command line.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        stat_tail = Path(f"/proc/{process.pid}/stat").read_text(encoding="utf-8").rpartition(") ")[2]
        command = [
            part.decode("utf-8")
            for part in Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")
            if part
        ]
        if command == list(process.args):
            return stat_tail.split()[19], command
        assert process.poll() is None, "Test child exited before publishing its identity"
        time.sleep(0.01)
    raise AssertionError("Test child did not publish its expected process identity")


def _proc_group_member_is_running(pid, process_group_id):
    try:
        stat_tail = (
            Path(f"/proc/{pid}/stat")
            .read_text(encoding="utf-8")
            .rpartition(") ")[2]
            .split()
        )
    except FileNotFoundError:
        return False
    return (
        stat_tail[0] != "Z"
        and int(stat_tail[2]) == process_group_id
        and int(stat_tail[3]) == process_group_id
    )


def _wait_for_test_process_marker(marker_path, timeout=3):
    deadline = time.monotonic() + timeout
    while not marker_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker_path.exists()


def _kill_controlled_test_group(process_group_id):
    controlled_members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_tail = (
                (entry / "stat")
                .read_text(encoding="utf-8")
                .rpartition(") ")[2]
                .split()
            )
        except FileNotFoundError:
            continue
        if int(stat_tail[2]) != process_group_id or stat_tail[0] == "Z":
            continue
        assert int(stat_tail[3]) == process_group_id
        controlled_members.append(int(entry.name))
    if controlled_members:
        os.killpg(process_group_id, signal.SIGKILL)


def _write_term_resistant_group_script(path):
    path.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child_code = (\n"
        "    'import os, pathlib, signal, sys, time\\n'\n"
        "    'signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n'\n"
        "    'pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\\n'\n"
        "    'time.sleep(30)\\n'\n"
        ")\n"
        "subprocess.Popen([sys.executable, '-c', child_code, sys.argv[1]])\n"
        "if sys.argv[2] != '-':\n"
        "    while not pathlib.Path(sys.argv[2]).exists():\n"
        "        time.sleep(0.01)\n"
        "    raise SystemExit(0)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(sys.platform != "linux", reason="process-group identity uses /proc")
def test_launch_rollback_kills_child_that_survives_leader_sigterm(tmp_path, monkeypatch):
    from app.services.simulation_runner import SimulationRunState, SimulationRunner

    simulation_id = "sim_0123456789ab"
    script_path = tmp_path / "controlled_group.py"
    child_marker = tmp_path / "child.pid"
    _write_term_resistant_group_script(script_path)
    command = [sys.executable, str(script_path), str(child_marker), "-"]
    process = subprocess.Popen(command, start_new_session=True)
    _wait_for_test_process_marker(child_marker)
    child_pid = int(child_marker.read_text(encoding="utf-8"))
    start_time, observed_command = _proc_identity(process)
    process_group_id = os.getpgid(process.pid)
    assert process_group_id == process.pid
    state = SimulationRunState(
        simulation_id=simulation_id,
        process_pid=process.pid,
        process_start_time=start_time,
        process_command=observed_command,
    )
    state.process_group_id = process_group_id
    state.process_session_id = os.getsid(process.pid)
    monkeypatch.setattr(SimulationRunner, "_processes", {simulation_id: process})

    try:
        assert SimulationRunner._rollback_launched_process(process, state, timeout=0.2)
        deadline = time.monotonic() + 2
        while (
            _proc_group_member_is_running(child_pid, process_group_id)
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert _proc_group_member_is_running(child_pid, process_group_id) is False
        assert state.process_cleanup_required is False
    finally:
        _kill_controlled_test_group(process_group_id)
        process.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="process-group identity uses /proc")
def test_launch_rollback_cleans_descendant_after_leader_already_exited(
    tmp_path, monkeypatch
):
    from app.services.simulation_runner import SimulationRunState, SimulationRunner

    simulation_id = "sim_0123456789ab"
    script_path = tmp_path / "controlled_group.py"
    child_marker = tmp_path / "child.pid"
    release_marker = tmp_path / "release"
    _write_term_resistant_group_script(script_path)
    command = [
        sys.executable,
        str(script_path),
        str(child_marker),
        str(release_marker),
    ]
    process = subprocess.Popen(command, start_new_session=True)
    _wait_for_test_process_marker(child_marker)
    child_pid = int(child_marker.read_text(encoding="utf-8"))
    start_time, observed_command = _proc_identity(process)
    process_group_id = os.getpgid(process.pid)
    assert process_group_id == process.pid
    state = SimulationRunState(
        simulation_id=simulation_id,
        process_pid=process.pid,
        process_start_time=start_time,
        process_command=observed_command,
    )
    state.process_group_id = process_group_id
    state.process_session_id = os.getsid(process.pid)
    monkeypatch.setattr(SimulationRunner, "_processes", {simulation_id: process})
    release_marker.write_text("exit", encoding="utf-8")
    process.wait(timeout=3)

    try:
        assert SimulationRunner._rollback_launched_process(process, state, timeout=0.2)
        deadline = time.monotonic() + 2
        while (
            _proc_group_member_is_running(child_pid, process_group_id)
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert _proc_group_member_is_running(child_pid, process_group_id) is False
        assert state.process_cleanup_required is False
    finally:
        _kill_controlled_test_group(process_group_id)


def test_shutdown_cleanup_retains_process_ownership_when_group_is_unverified(
    tmp_path, monkeypatch
):
    from app.services.simulation_runner import (
        RunnerStatus,
        SimulationRunState,
        SimulationRunner,
    )

    simulation_id = "sim_0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    state = SimulationRunState(
        simulation_id=simulation_id,
        runner_status=RunnerStatus.RUNNING,
        process_pid=12345,
        process_start_time="persisted-start",
        process_command=["python", "simulation.py"],
        process_group_id=12345,
        process_session_id=12345,
    )
    process = SimpleNamespace(pid=12345, poll=lambda: 0)
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_processes", {simulation_id: process})
    monkeypatch.setattr(SimulationRunner, "_action_queues", {})
    monkeypatch.setattr(SimulationRunner, "_stdout_files", {})
    monkeypatch.setattr(SimulationRunner, "_stderr_files", {})
    monkeypatch.setattr(SimulationRunner, "_graph_memory_enabled", {})
    monkeypatch.setattr(SimulationRunner, "_cleanup_done", False)
    monkeypatch.setattr(
        SimulationRunner,
        "_terminate_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("group ownership is unverified")
        ),
    )
    SimulationRunner._save_run_state(state)

    SimulationRunner.cleanup_all_simulations()

    retained = SimulationRunner.get_run_state(simulation_id)
    assert retained.runner_status == RunnerStatus.RUNNING
    assert retained.process_cleanup_required is True
    assert retained.process_cleanup_error
    assert SimulationRunner._processes[simulation_id] is process


@pytest.mark.skipif(sys.platform != "linux", reason="safe foreign PID verification uses /proc")
def test_owner_worker_launch_persists_identity_and_stops_verified_process(
    tmp_path, monkeypatch
):
    from app.services.simulation_runner import RunnerStatus, SimulationRunner

    simulation_id = "sim_0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    (simulation_dir / "simulation_config.json").write_text(
        '{"time_config":{"total_simulation_hours":1,"minutes_per_round":60}}',
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "run_parallel_simulation.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "SCRIPTS_DIR", str(scripts))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_processes", {})
    monkeypatch.setattr(SimulationRunner, "_monitor_threads", {})
    monkeypatch.setattr(SimulationRunner, "_stdout_files", {})
    monkeypatch.setattr(SimulationRunner, "_stderr_files", {})

    state = SimulationRunner.start_simulation(simulation_id)
    try:
        assert state.process_pid
        assert state.process_start_time
        assert state.process_command[-2:] == [
            "--config",
            str(simulation_dir / "simulation_config.json"),
        ]
        stopped = SimulationRunner.stop_simulation(simulation_id)
        assert stopped.runner_status == RunnerStatus.STOPPED
    finally:
        process = SimulationRunner._processes.get(simulation_id)
        if process and process.poll() is None:
            process.kill()


@pytest.mark.skipif(sys.platform != "linux", reason="safe foreign PID verification uses /proc")
@pytest.mark.parametrize("identity_matches", [True, False])
def test_foreign_worker_only_stops_verified_process(tmp_path, monkeypatch, identity_matches):
    from app.services.simulation_runner import RunnerStatus, SimulationRunState, SimulationRunner

    simulation_id = "sim_0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    config_path = simulation_dir / "simulation_config.json"
    config_path.write_text("{}", encoding="utf-8")
    command = [sys.executable, "-c", "import time; time.sleep(30)", "--config", str(config_path)]
    process = subprocess.Popen(command, start_new_session=True)
    start_time, observed_command = _proc_identity(process)
    state = SimulationRunState(
        simulation_id=simulation_id,
        runner_status=RunnerStatus.RUNNING,
        process_pid=process.pid,
        process_start_time=start_time if identity_matches else "reused-pid",
        process_command=observed_command,
    )
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_processes", {})
    SimulationRunner._save_run_state(state)

    try:
        if identity_matches:
            stopped = SimulationRunner.stop_simulation(simulation_id)
            assert stopped.runner_status == RunnerStatus.STOPPED
            assert stopped.process_cleanup_required is False
            process.wait(timeout=3)
        else:
            with pytest.raises(ValueError, match="identity"):
                SimulationRunner.stop_simulation(simulation_id)
            assert process.poll() is None
            retained = SimulationRunner.get_run_state(simulation_id)
            assert retained.runner_status == RunnerStatus.RUNNING
            assert retained.process_cleanup_required is True
            assert retained.process_cleanup_error
    finally:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), 9)
            process.wait(timeout=3)


@pytest.mark.skipif(sys.platform != "linux", reason="safe foreign PID verification uses /proc")
def test_foreign_worker_retains_cleanup_when_leader_identity_is_gone(
    tmp_path, monkeypatch
):
    from app.services.simulation_runner import (
        RunnerStatus,
        SimulationRunState,
        SimulationRunner,
    )

    simulation_id = "sim_0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    process = subprocess.Popen(command, start_new_session=True)
    start_time, observed_command = _proc_identity(process)
    state = SimulationRunState(
        simulation_id=simulation_id,
        runner_status=RunnerStatus.RUNNING,
        process_pid=process.pid,
        process_start_time=start_time,
        process_command=observed_command,
        process_group_id=process.pid,
        process_session_id=process.pid,
    )
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_processes", {})
    SimulationRunner._save_run_state(state)
    process.terminate()
    process.wait(timeout=3)

    with pytest.raises(ValueError, match="identity"):
        SimulationRunner.stop_simulation(simulation_id)

    retained = SimulationRunner.get_run_state(simulation_id)
    assert retained.runner_status == RunnerStatus.RUNNING
    assert retained.process_cleanup_required is True
    assert retained.process_cleanup_error


def test_graph_memory_updater_rolls_back_when_launch_fails(tmp_path, monkeypatch):
    from app.services.simulation_runner import RunnerStatus, SimulationRunner
    from app.services.graph_memory_updater import GraphMemoryManager

    simulation_id = "sim_0123456789ab"
    graph_id = "01234567-89ab-4def-8abc-0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    (simulation_dir / "simulation_config.json").write_text(
        '{"time_config":{"total_simulation_hours":1,"minutes_per_round":60}}',
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "run_parallel_simulation.py").write_text("print('unused')", encoding="utf-8")
    active_updaters = set()
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "SCRIPTS_DIR", str(scripts))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_graph_memory_enabled", {})
    monkeypatch.setattr(
        GraphMemoryManager,
        "create_updater",
        lambda sim_id, *_args: active_updaters.add(sim_id),
    )
    monkeypatch.setattr(
        GraphMemoryManager,
        "stop_updater",
        lambda sim_id: active_updaters.discard(sim_id),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("launch failed")),
    )

    with pytest.raises(RuntimeError, match="launch failed"):
        SimulationRunner.start_simulation(
            simulation_id,
            enable_graph_memory_update=True,
            graph_id=graph_id,
            storage=object(),
        )

    state = SimulationRunner.get_run_state(simulation_id)
    assert active_updaters == set()
    assert simulation_id not in SimulationRunner._graph_memory_enabled
    assert state.runner_status == RunnerStatus.FAILED
    assert state.graph_memory_update_enabled is False
    assert state.graph_memory_update_error


def test_graph_memory_cleanup_failure_retains_ownership_until_retry(
    tmp_path, monkeypatch
):
    from app.services.graph_memory_updater import GraphMemoryManager
    from app.services.simulation_runner import RunnerStatus, SimulationRunner

    simulation_id = "sim_0123456789ab"
    graph_id = "01234567-89ab-4def-8abc-0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    (simulation_dir / "simulation_config.json").write_text(
        '{"time_config":{"total_simulation_hours":1,"minutes_per_round":60}}',
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "run_parallel_simulation.py").write_text("print('unused')", encoding="utf-8")

    class FlakyUpdater:
        def __init__(self):
            self.stop_attempts = 0

        def stop(self):
            self.stop_attempts += 1
            if self.stop_attempts == 1:
                raise RuntimeError("cleanup failed")

    updater = FlakyUpdater()
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "SCRIPTS_DIR", str(scripts))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_graph_memory_enabled", {})
    monkeypatch.setattr(GraphMemoryManager, "_updaters", {simulation_id: updater})
    monkeypatch.setattr(
        GraphMemoryManager,
        "create_updater",
        lambda *_args, **_kwargs: updater,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("launch failed")),
    )

    with pytest.raises(RuntimeError, match="launch failed"):
        SimulationRunner.start_simulation(
            simulation_id,
            enable_graph_memory_update=True,
            graph_id=graph_id,
            storage=object(),
        )

    failed = SimulationRunner.get_run_state(simulation_id)
    assert failed.runner_status == RunnerStatus.FAILED
    assert failed.graph_memory_update_enabled is True
    assert failed.graph_memory_cleanup_required is True
    assert SimulationRunner._graph_memory_enabled[simulation_id] is True
    assert GraphMemoryManager.get_updater(simulation_id) is updater

    persisted_failed = json.loads(
        (simulation_dir / "run_state.json").read_text(encoding="utf-8")
    )
    assert persisted_failed["graph_memory_update_enabled"] is True
    assert persisted_failed["graph_memory_cleanup_required"] is True

    reconciled = SimulationRunner.retry_graph_memory_cleanup(simulation_id)

    assert updater.stop_attempts == 2
    assert GraphMemoryManager.get_updater(simulation_id) is None
    assert simulation_id not in SimulationRunner._graph_memory_enabled
    assert reconciled.graph_memory_update_enabled is False
    assert reconciled.graph_memory_graph_id is None
    assert reconciled.graph_memory_update_error is None
    assert reconciled.graph_memory_cleanup_required is False
    persisted_reconciled = json.loads(
        (simulation_dir / "run_state.json").read_text(encoding="utf-8")
    )
    assert persisted_reconciled["graph_memory_update_enabled"] is False
    assert persisted_reconciled["graph_memory_graph_id"] is None
    assert persisted_reconciled["graph_memory_update_error"] is None
    assert persisted_reconciled["graph_memory_cleanup_required"] is False


@pytest.mark.skipif(sys.platform != "linux", reason="process-group identity uses /proc")
def test_launch_rollback_terminates_verified_process_group(tmp_path, monkeypatch):
    from app.services.simulation_runner import RunnerStatus, SimulationRunner

    simulation_id = "sim_0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    (simulation_dir / "simulation_config.json").write_text(
        '{"time_config":{"total_simulation_hours":1,"minutes_per_round":60}}',
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    child_pid_path = simulation_dir / "child.pid"
    (scripts / "run_parallel_simulation.py").write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "pathlib.Path('child.pid').write_text(str(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "SCRIPTS_DIR", str(scripts))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_processes", {})
    monkeypatch.setattr(SimulationRunner, "_stdout_files", {})
    monkeypatch.setattr(SimulationRunner, "_stderr_files", {})
    original_save = SimulationRunner._save_run_state
    failed_once = False
    launched_pid = None

    def fail_first_post_launch_save(cls, state):
        nonlocal failed_once, launched_pid
        if state.process_pid and not failed_once:
            launched_pid = state.process_pid
            deadline = time.monotonic() + 2
            while not child_pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert child_pid_path.exists()
            failed_once = True
            raise RuntimeError("state persistence failed")
        return original_save(state)

    monkeypatch.setattr(
        SimulationRunner,
        "_save_run_state",
        classmethod(fail_first_post_launch_save),
    )

    try:
        with pytest.raises(RuntimeError, match="state persistence failed"):
            SimulationRunner.start_simulation(simulation_id)

        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
            stat = Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8")
            if stat.rpartition(") ")[2].split()[0] == "Z":
                break
            time.sleep(0.02)

        child_running = Path(f"/proc/{child_pid}").exists() and (
            Path(f"/proc/{child_pid}/stat")
            .read_text(encoding="utf-8")
            .rpartition(") ")[2]
            .split()[0]
            != "Z"
        )
        assert child_running is False
        failed = SimulationRunner.get_run_state(simulation_id)
        assert failed.runner_status == RunnerStatus.FAILED
        assert failed.process_cleanup_required is False
    finally:
        if launched_pid:
            try:
                os.killpg(launched_pid, 9)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(sys.platform != "linux", reason="process-group identity uses /proc")
def test_launch_rollback_fails_closed_when_process_identity_is_unverified(
    tmp_path, monkeypatch
):
    from app.services.simulation_runner import RunnerStatus, SimulationRunner

    simulation_id = "sim_0123456789ab"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    (simulation_dir / "simulation_config.json").write_text(
        '{"time_config":{"total_simulation_hours":1,"minutes_per_round":60}}',
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "run_parallel_simulation.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    launched = []
    real_popen = subprocess.Popen

    def record_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SimulationRunner, "SCRIPTS_DIR", str(scripts))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    monkeypatch.setattr(SimulationRunner, "_processes", {})
    monkeypatch.setattr(subprocess, "Popen", record_popen)
    monkeypatch.setattr(SimulationRunner, "_read_process_identity", lambda *_args: None)

    try:
        with pytest.raises(RuntimeError, match="persist simulation process identity"):
            SimulationRunner.start_simulation(simulation_id)

        assert launched[0].poll() is None
        failed = SimulationRunner.get_run_state(simulation_id)
        assert failed.runner_status == RunnerStatus.FAILED
        assert failed.process_cleanup_required is True
        assert failed.process_pid == launched[0].pid
    finally:
        if launched and launched[0].poll() is None:
            os.killpg(os.getpgid(launched[0].pid), 9)
            launched[0].wait(timeout=3)


class _TestConfig:
    TESTING = True
    PROPAGATE_EXCEPTIONS = False
    SECRET_KEY = "test-secret"
    DEBUG = False
    JSON_AS_ASCII = False
    MIROFISH_BIND_HOST = "127.0.0.1"
    MIROFISH_ALLOWED_ORIGINS = ["http://localhost:3000"]
    MIROFISH_CONTROL_TOKEN = None
    LLM_API_KEY = "test-key"
    LLM_BASE_URL = "http://test.invalid:11434/v1"
    LLM_MODEL_NAME = "test-model"
    NEO4J_URI = "bolt://test.invalid:7687"
    NEO4J_USER = "neo4j"
    NEO4J_PASSWORD = "test-password"
    EMBEDDING_MODEL = "test-embed"
    EMBEDDING_BASE_URL = "http://test.invalid:11434"
    UPLOAD_FOLDER = "/tmp/unused-mirofish-health"
    READINESS_MIN_FREE_DISK_BYTES = 1
    READINESS_TIMEOUT_SECONDS = 0.2
    READINESS_CACHE_TTL_SECONDS = 0


def _health_app(monkeypatch, config_class=_TestConfig):
    import app as app_module
    import app.storage as storage_module
    from app.services.simulation_runner import SimulationRunner

    monkeypatch.setattr(app_module, "setup_logger", lambda *args, **kwargs: _SilentLogger())
    monkeypatch.setattr(app_module, "get_logger", lambda *args, **kwargs: _SilentLogger())
    monkeypatch.setattr(storage_module, "Neo4jStorage", lambda *args, **kwargs: object())
    monkeypatch.setattr(SimulationRunner, "register_cleanup", lambda: None)
    return create_app(config_class)


class _SilentLogger:
    def debug(self, *args, **kwargs):
        pass

    info = debug
    warning = debug
    error = debug
    exception = debug


def test_liveness_is_process_only_even_when_dependencies_fail(monkeypatch):
    app = _health_app(monkeypatch)
    app.extensions["readiness_checks"] = {
        name: (lambda: (False, "down"))
        for name in ("neo4j", "llm", "embedding", "uploads", "disk")
    }

    response = app.test_client().get("/health/live")

    assert response.status_code == 200
    assert response.get_json()["status"] == "live"


def test_readiness_and_api_status_report_all_injected_components(monkeypatch):
    app = _health_app(monkeypatch)
    app.extensions["readiness_checks"] = {
        name: (lambda: (True, "ok"))
        for name in ("neo4j", "llm", "embedding", "uploads", "disk")
    }
    client = app.test_client()

    ready = client.get("/health/ready")
    status = client.get("/api/status")

    assert ready.status_code == 200
    assert status.status_code == 200
    assert ready.get_json() == status.get_json()
    assert ready.get_json()["status"] == "ready"
    assert set(ready.get_json()["components"]) == {
        "neo4j",
        "llm",
        "embedding",
        "uploads",
        "disk",
    }


def test_readiness_returns_503_with_component_safe_detail(monkeypatch):
    app = _health_app(monkeypatch)
    app.extensions["readiness_checks"] = {
        "neo4j": lambda: (True, "ok"),
        "llm": lambda: (False, "models endpoint unavailable"),
        "embedding": lambda: (True, "ok"),
        "uploads": lambda: (True, "ok"),
        "disk": lambda: (True, "ok"),
    }

    client = app.test_client()
    response = client.get("/health/ready")
    api_response = client.get("/api/status")

    assert response.status_code == 503
    assert api_response.status_code == 503
    assert api_response.get_json() == response.get_json()
    assert response.get_json()["status"] == "not_ready"
    assert response.get_json()["components"]["llm"] == {
        "ready": False,
        "detail": "models endpoint unavailable",
    }


def test_readiness_has_hard_overall_deadline(monkeypatch):
    app = _health_app(monkeypatch)

    def blocked():
        time.sleep(0.5)
        return True, "late"

    app.extensions["readiness_checks"] = {
        "blocked": blocked,
        "quick": lambda: (True, "ok"),
    }
    started = time.monotonic()

    response = app.test_client().get("/health/ready")

    assert time.monotonic() - started < 0.35
    assert response.status_code == 503
    assert response.get_json()["components"]["blocked"]["detail"] == "timeout"


def test_readiness_short_ttl_cache_returns_consistent_snapshot(monkeypatch):
    config = type(
        "CachedReadinessConfig",
        (_TestConfig,),
        {"READINESS_CACHE_TTL_SECONDS": 10},
    )
    app = _health_app(monkeypatch, config)
    state = {"ready": True}
    app.extensions["readiness_checks"] = {
        "changing": lambda: (state["ready"], "snapshot")
    }

    first = app.test_client().get("/health/ready")
    state["ready"] = False
    second = app.test_client().get("/health/ready")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json() == first.get_json()


def test_readiness_reuses_one_inflight_computation_for_simultaneous_timeouts(monkeypatch):
    config = type(
        "SingleFlightReadinessConfig",
        (_TestConfig,),
        {"READINESS_TIMEOUT_SECONDS": 0.05, "READINESS_CACHE_TTL_SECONDS": 0},
    )
    app = _health_app(monkeypatch, config)
    invocations = 0
    invocation_lock = threading.Lock()
    release = threading.Event()

    def blocked():
        nonlocal invocations
        with invocation_lock:
            invocations += 1
        release.wait(timeout=0.3)
        return True, "released"

    app.extensions["readiness_checks"] = {"blocked": blocked}
    barrier = threading.Barrier(7)
    statuses = []

    def request_readiness():
        barrier.wait()
        statuses.append(app.test_client().get("/health/ready").status_code)

    threads = [threading.Thread(target=request_readiness) for _ in range(6)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=1)
    release.set()

    assert statuses == [503] * 6
    assert invocations == 1


def test_readiness_retires_one_hung_generation_and_recovers_with_bounded_workers(
    monkeypatch,
):
    config = type(
        "RecoveringReadinessConfig",
        (_TestConfig,),
        {"READINESS_TIMEOUT_SECONDS": 0.05, "READINESS_CACHE_TTL_SECONDS": 10},
    )
    app = _health_app(monkeypatch, config)
    release_first = threading.Event()
    recovered = False
    invocations = 0
    invocation_lock = threading.Lock()

    def changing_dependency():
        nonlocal invocations
        with invocation_lock:
            invocations += 1
            generation = invocations
        if generation == 1:
            release_first.wait(timeout=2)
            return False, "stale"
        return recovered, "fresh"

    app.extensions["readiness_checks"] = {"dependency": changing_dependency}

    first = app.test_client().get("/health/ready")
    recovered = True
    second = app.test_client().get("/health/ready")
    for _ in range(20):
        assert app.test_client().get("/health/ready").status_code == 200

    release_first.set()

    assert first.status_code == 503
    assert second.status_code == 200
    assert second.get_json()["components"]["dependency"] == {
        "ready": True,
        "detail": "fresh",
    }
    assert invocations == 2
    assert len(app.extensions["readiness_cache"]["executor"]._threads) <= 10


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": "malformed"},
        {},
    ],
)
def test_llm_readiness_rejects_empty_or_malformed_model_catalog(
    monkeypatch, payload
):
    import app.health as health_module

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(health_module.requests, "get", lambda *args, **kwargs: Response())
    app = SimpleNamespace(
        config={
            "READINESS_TIMEOUT_SECONDS": 0.2,
            "LLM_BASE_URL": "http://local.invalid/v1",
            "LLM_MODEL_NAME": "required-model",
        },
        extensions={},
    )

    assert health_module._default_checks(app)["llm"]() == (
        False,
        "configured model unavailable",
    )


@pytest.mark.parametrize(
    "timeout,disk",
    [
        (0, 1),
        (-1, 1),
        (float("nan"), 1),
        (float("inf"), 1),
        (1, -1),
    ],
)
def test_app_rejects_invalid_readiness_ranges(monkeypatch, timeout, disk):
    config = type(
        "InvalidReadinessConfig",
        (_TestConfig,),
        {
            "READINESS_TIMEOUT_SECONDS": timeout,
            "READINESS_MIN_FREE_DISK_BYTES": disk,
        },
    )

    with pytest.raises(ValueError, match="READINESS"):
        _health_app(monkeypatch, config)


def test_each_app_uses_its_configured_upload_folder_for_tasks(tmp_path, monkeypatch):
    first_uploads = tmp_path / "first"
    second_uploads = tmp_path / "second"
    first_config = type("FirstAppConfig", (_TestConfig,), {"UPLOAD_FOLDER": str(first_uploads)})
    second_config = type("SecondAppConfig", (_TestConfig,), {"UPLOAD_FOLDER": str(second_uploads)})

    first_app = _health_app(monkeypatch, first_config)
    second_app = _health_app(monkeypatch, second_config)
    task_id = first_app.extensions["task_manager"].create_task("first-only")

    assert Path(first_app.extensions["task_manager"].db_path) == first_uploads / "tasks.sqlite3"
    assert Path(second_app.extensions["task_manager"].db_path) == second_uploads / "tasks.sqlite3"
    assert second_app.extensions["task_manager"].get_task(task_id) is None
