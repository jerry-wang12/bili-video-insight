"""Test installer side effects with isolated registrations and command substitutes."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def executable(path, text):
    path.write_text("#!/bin/sh\nset -eu\n" + text)
    path.chmod(0o755)


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copy2(Path(__file__).parents[1] / "install.sh", project / "install.sh")
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "commands.log"
    env = {
        **os.environ,
        "PATH": str(tools) + ":/usr/bin:/bin",
        "CODEX_HOME": str(tmp_path / "codex"),
        "BILI_SETUP_TEST_LOG": str(log),
        "BILI_SETUP_TEST_BIN": str(tools),
    }
    executable(tools / "uname", "printf 'Darwin\\n'\n")
    executable(
        tools / "uv",
        """printf 'uv %s\n' "$*" >> "$BILI_SETUP_TEST_LOG"
mkdir -p .venv/bin
cat > .venv/bin/python <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x .venv/bin/python
""",
    )
    executable(tools / "ffmpeg", "exit 0\n")
    executable(tools / "ffprobe", "exit 0\n")
    executable(project / "bili", 'printf "doctor\\n" >> "$BILI_SETUP_TEST_LOG"\n')
    executable(
        tools / "brew",
        """printf 'brew %s\n' "$*" >> "$BILI_SETUP_TEST_LOG"
for tool in ffmpeg ffprobe; do
    printf '#!/bin/sh\nexit 0\n' > "$BILI_SETUP_TEST_BIN/$tool"
    chmod +x "$BILI_SETUP_TEST_BIN/$tool"
done
""",
    )
    return project, env, log, tools


def run_setup(setup, *args):
    project, env, _, _ = setup
    return subprocess.run(
        ["sh", str(project / "install.sh"), *args],
        env=env,
        cwd=project.parent,
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_is_read_only(setup):
    project, env, log, _ = setup
    result = run_setup(setup, "--check")
    assert result.returncode == 0
    assert not log.exists()
    assert not (project / ".venv").exists()
    assert not Path(env["CODEX_HOME"]).exists()


def test_setup_registers_and_can_be_repeated_without_replacing_environment(setup):
    project, env, log, _ = setup
    for _ in range(2):
        result = run_setup(setup)
        assert result.returncode == 0, result.stderr
        target = Path(env["CODEX_HOME"]) / "skills/bili-video-insight"
        assert target.is_symlink() and target.resolve() == project
    calls = log.read_text().splitlines()
    assert "brew" not in log.read_text()
    assert "--python 3.12" in calls[0]
    assert "--python " + str(project / ".venv/bin/python") in calls[2]
    assert all("--locked --extra asr --inexact" in c for c in calls if c.startswith("uv"))


@pytest.mark.parametrize("broken_link", [False, True])
def test_conflicting_skill_stops_before_any_installation(setup, broken_link):
    _, env, log, _ = setup
    target = Path(env["CODEX_HOME"]) / "skills/bili-video-insight"
    target.parent.mkdir(parents=True)
    if broken_link:
        target.symlink_to(target.parent / "missing")
    else:
        target.mkdir()
        (target / "keep").write_text("unrelated skill")
    result = run_setup(setup)
    assert result.returncode != 0
    assert "不会覆盖" in result.stderr
    assert not log.exists()
    if broken_link:
        assert target.is_symlink()
    else:
        assert (target / "keep").read_text() == "unrelated skill"


def test_missing_ffmpeg_uses_package_manager_before_registering(setup):
    _, _, log, tools = setup
    (tools / "ffmpeg").unlink()
    (tools / "ffprobe").unlink()
    result = run_setup(setup)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[0] == "brew install ffmpeg"


def test_failed_dependency_installation_does_not_register_skill(setup):
    _, env, log, tools = setup
    executable(tools / "uv", "exit 9\n")
    result = run_setup(setup)
    assert result.returncode == 9
    assert not log.exists()
    assert not (Path(env["CODEX_HOME"]) / "skills/bili-video-insight").exists()


def test_linux_installs_ffmpeg_through_sudo_apt(setup):
    _, _, log, tools = setup
    (tools / "ffmpeg").unlink()
    (tools / "ffprobe").unlink()
    executable(tools / "uname", "printf 'Linux\\n'\n")
    executable(tools / "id", "printf '1000\\n'\n")
    executable(tools / "sudo", 'exec "$@"\n')
    executable(
        tools / "apt-get",
        """printf 'apt-get %s\n' "$*" >> "$BILI_SETUP_TEST_LOG"
if [ "$1" = install ]; then
    for tool in ffmpeg ffprobe; do
        printf '#!/bin/sh\nexit 0\n' > "$BILI_SETUP_TEST_BIN/$tool"
        chmod +x "$BILI_SETUP_TEST_BIN/$tool"
    done
fi
""",
    )
    result = run_setup(setup)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[:2] == ["apt-get update", "apt-get install -y ffmpeg"]


def test_failed_system_package_installation_stops_before_python_and_registration(setup):
    _, env, log, tools = setup
    (tools / "ffmpeg").unlink()
    executable(tools / "brew", "exit 7\n")
    result = run_setup(setup)
    assert result.returncode == 7
    assert not log.exists()
    assert not Path(env["CODEX_HOME"]).exists()
