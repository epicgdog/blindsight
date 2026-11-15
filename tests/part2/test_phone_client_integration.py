"""Run the phone app's real JS modules (node) against the live echo server."""
import os
import subprocess

import pytest

from .conftest import ROOT

pytestmark = pytest.mark.node


def run_node(*args, env=None, timeout=60):
    return subprocess.run(
        ["node", *args], cwd=ROOT, env={**os.environ, **(env or {})},
        capture_output=True, text=True, timeout=timeout,
    )


def test_node_unit_suite():
    r = run_node("--test", "app/test/unit/*.test.js")
    assert r.returncode == 0, r.stdout + r.stderr


def test_phone_pipeline_against_echo_server(server):
    r = run_node("--test", "app/test/integration/*.test.js", env={"INSIGHT_WS": server["ws"]})
    assert r.returncode == 0, r.stdout + r.stderr
