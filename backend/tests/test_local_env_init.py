import stat
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read_env(path):
    return {
        key: value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }


def test_documented_local_env_initialization_fills_required_compose_values(tmp_path):
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    assert "python3 scripts/init_local_env.py" in readme

    env_file = tmp_path / ".env"
    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/init_local_env.py"),
            "--template",
            str(REPOSITORY_ROOT / ".env.example"),
            "--env-file",
            str(env_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    values = _read_env(env_file)

    assert values["MIROFISH_CONTROL_TOKEN"]
    assert values["NEO4J_PASSWORD"]
    assert values["MIROFISH_CONTROL_TOKEN"] not in result.stdout + result.stderr
    assert values["NEO4J_PASSWORD"] not in result.stdout + result.stderr
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    first_values = values.copy()
    subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/init_local_env.py"),
            "--env-file",
            str(env_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert _read_env(env_file) == first_values
