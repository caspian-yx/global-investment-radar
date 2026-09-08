from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily.yml"
RADAR_COMMAND = "uv run radar daily --no-ai"


def _daily_run_script() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"- name: Run live no-AI radar and deliver email\n"
        r"(?: {8}.*\n)*?"
        r" {8}run: \|\n"
        r"(?P<script>(?: {10}.*(?:\n|$))+)",
        workflow,
    )
    assert match is not None, "daily workflow must expose a multiline Bash run script"
    return "\n".join(line[10:] for line in match.group("script").splitlines())


def test_daily_workflow_has_failure_safe_artifact_constraints() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    script = _daily_run_script()
    job_environment = workflow.split("    env:\n", 1)[1].split("    steps:\n", 1)[0]

    assert "shell: bash" in workflow
    assert "secrets." not in job_environment
    assert "set -o pipefail" in script
    assert f"{RADAR_COMMAND} " in script
    assert "| tee outputs/run-result.json" in script
    assert "outputs/run-diagnostics.log" in script
    assert "continue-on-error" not in workflow
    assert "if: ${{ always() }}" in workflow
    assert "outputs/reports/**/*.html" in workflow
    assert "outputs/run-result.json" in workflow
    assert "outputs/run-diagnostics.log" in workflow


def test_daily_workflow_script_preserves_failure_through_tee(tmp_path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    failing_command = (
        "bash -c 'printf run-result; printf diagnostic-message >&2; exit 23'"
    )
    script = _daily_run_script().replace(RADAR_COMMAND, failing_command)

    completed = subprocess.run(
        [bash, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 23
    assert (tmp_path / "outputs" / "run-result.json").read_text() == "run-result"
    assert (tmp_path / "outputs" / "run-diagnostics.log").read_text() == (
        "diagnostic-message"
    )
