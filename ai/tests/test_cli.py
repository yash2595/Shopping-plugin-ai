import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_offline_cli_runs_without_any_model_key():
    env = os.environ.copy()
    env["AI_PROVIDER"] = "mock"
    env.pop("GEMINI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "shopping_ai.cli", "ai/examples/cart_proposal.json", "--fresh-demo"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["status"] == "tool_proposed"
    assert response["tool"]["name"] == "add_to_cart"


def test_cli_rejects_malformed_input_without_echoing_it(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"password": "private-test-value"}')
    env = os.environ.copy()
    env["AI_PROVIDER"] = "mock"
    result = subprocess.run(
        [sys.executable, "-m", "shopping_ai.cli", str(path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "private-test-value" not in result.stdout + result.stderr
