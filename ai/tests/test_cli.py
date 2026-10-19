import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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


def capture_file(tmp_path, *, stale=False):
    url = "https://store.example/product/one"
    node = {
        "@type": "Product",
        "name": "Demo phone",
        "url": url,
        "offers": {
            "@type": "Offer",
            "price": "100.10",
            "priceCurrency": "INR",
            "availability": "https://schema.org/InStock",
        },
    }
    data = {
        "target": {"tab_id": 1, "document_id": "demo", "active": True, "window_focused": True},
        "url": url,
        "observed_at": (datetime.now(UTC) - timedelta(days=1 if stale else 0)).isoformat(),
        "html": '<!-- PRIVATE_CAPTURE --><script type="application/ld+json">'
        + json.dumps(node)
        + "</script>",
        "visible_title": "Demo phone",
        "visible_price": "100.10",
        "visible_currency": "INR",
        "visible_stock": True,
    }
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run_capture(path, *extra):
    env = os.environ.copy()
    env["AI_PROVIDER"] = "must-not-be-loaded"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "shopping_ai.cli",
            "--capture",
            str(path),
            "--product-id",
            "demo-product",
            *extra,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_capture_cli_normalizes_and_answers_without_loading_live_provider(tmp_path):
    result = run_capture(capture_file(tmp_path))
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["mode"] == "offline-capture"
    assert output["product"]["product_id"] == "demo-product"
    assert output["product"]["price"] == "100.10"
    assert output["answer"]["status"] == "answered" and output["answer"]["tool"] is None
    assert "PRIVATE_CAPTURE" not in result.stdout + result.stderr
    assert "html" not in output["product"]


def test_capture_cli_cannot_propose_cart_changes(tmp_path):
    result = run_capture(capture_file(tmp_path), "--message", "add to cart")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["answer"]["tool"] is None


def test_capture_cli_refuses_stale_evidence_without_echoing_html(tmp_path):
    result = run_capture(capture_file(tmp_path, stale=True))
    assert result.returncode == 2
    assert not result.stdout and "PRIVATE_CAPTURE" not in result.stderr


@pytest.mark.parametrize("extra", [("--fresh-demo",), ("ai/examples/product_question.json",)])
def test_capture_cli_refuses_timestamp_refresh_and_mixed_request_modes(tmp_path, extra):
    result = run_capture(capture_file(tmp_path), *extra)
    assert result.returncode == 2 and not result.stdout
