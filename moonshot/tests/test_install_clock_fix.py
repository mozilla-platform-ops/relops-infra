import sys

import pytest

import install_clock_fix as clock_fix


def test_explicit_user_does_not_require_login_terminal(monkeypatch):
    def no_terminal():
        raise OSError("no controlling terminal")

    monkeypatch.setattr(clock_fix.os, "getlogin", no_terminal)
    monkeypatch.setattr(sys, "argv", ["install_clock_fix.py", "-H", "155", "-u", "aerickson", "-f"])
    args = clock_fix.parse_args()
    assert args.user == "aerickson"
    assert args.force is True


@pytest.mark.parametrize("runner", [clock_fix.run_ssh_command, clock_fix.check_ssh_command])
def test_ssh_is_noninteractive_and_bounded(monkeypatch, runner):
    def fake_run(cmd, **kwargs):
        assert "BatchMode=yes" in cmd
        assert "ConnectTimeout=30" in cmd
        assert kwargs["timeout"] == 180
        return clock_fix.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(clock_fix.subprocess, "run", fake_run)
    assert runner("host", "aerickson", "true").returncode == 0
