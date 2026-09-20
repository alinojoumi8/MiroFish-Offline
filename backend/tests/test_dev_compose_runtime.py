import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI unavailable")
def test_compose_routes_browser_api_through_loopback_published_proxy(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MIROFISH_CONTROL_TOKEN=compose-test-token",
                "NEO4J_PASSWORD=compose-test-password",
                "LLM_API_KEY=ollama",
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(env_file),
            "-f",
            str(REPOSITORY_ROOT / "docker-compose.yml"),
            "config",
        ],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "MIROFISH_CONTROL_TOKEN": "compose-test-token"},
        check=True,
        capture_output=True,
        text=True,
    )
    config = result.stdout

    assert "MIROFISH_BIND_HOST: 0.0.0.0" in config
    assert "MIROFISH_CONTROL_TOKEN: compose-test-token" in config
    assert "EMBEDDING_BASE_URL: http://ollama:11434" in config
    assert "EMBEDDING_BASE_URL: http://ollama:11434/v1" not in config
    assert 'host_ip: 127.0.0.1' in config
    assert 'target: 3000' in config
    assert 'target: 5001' in config
