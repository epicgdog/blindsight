"""scripts/tunnel.sh: tunnel URL -> QR/app address, LAN fallback."""
import os
import stat
import subprocess

from .conftest import ROOT

SCRIPT = ROOT / "scripts/tunnel.sh"


def fake_bin(tmp_path, body):
    d = tmp_path / "bin"
    d.mkdir()
    p = d / "cloudflared"
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return d


def run(tmp_path, bin_dir, *args):
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin:{os.environ.get('PATH','')}",
           "BLINDSIGHT_NO_EXPO": "1", "BLINDSIGHT_TUNNEL_TIMEOUT": "5"}
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)


def test_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert SCRIPT.stat().st_mode & stat.S_IEXEC


def test_tunnel_url_becomes_wss_address(tmp_path):
    b = fake_bin(tmp_path, 'echo "INF |  https://quiet-lobby-demo.trycloudflare.com" >&2; sleep 30\n')
    r = run(tmp_path, b, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "wss://quiet-lobby-demo.trycloudflare.com" in out
    assert "EXPO_PUBLIC_SERVER_URL=wss://quiet-lobby-demo.trycloudflare.com/ws/phone" in out


def test_falls_back_to_lan_when_tunnel_fails(tmp_path):
    b = fake_bin(tmp_path, "exit 1\n")
    r = run(tmp_path, b, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "EXPO_PUBLIC_SERVER_URL=ws://" in out and ":8000/ws/phone" in out
    assert "fallback" in out.lower()


def test_prints_qr(tmp_path):
    b = fake_bin(tmp_path, 'echo "https://a-b.trycloudflare.com" >&2; sleep 30\n')
    r = run(tmp_path, b, "--dry-run")
    assert "█" in r.stdout + r.stderr or "▄" in r.stdout + r.stderr


def test_never_prints_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERPORT_API_TOKEN", "blindsight-test-secret-do-not-print")
    b = fake_bin(tmp_path, "exit 1\n")
    r = run(tmp_path, b, "--dry-run")
    # Exercise a known sentinel, never require or read the developer's credentials.
    secrets = ["blindsight-test-secret-do-not-print"]
    for s in filter(None, secrets):
        assert s not in r.stdout + r.stderr
