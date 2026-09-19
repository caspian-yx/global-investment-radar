from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily.yml"
OPERATOR_VALIDATION_WORKFLOW = (
    Path(__file__).parents[1] / ".github" / "workflows" / "operator-validation.yml"
)
RADAR_COMMAND = "uv run radar daily --ai"


def _daily_run_script() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"- name: Run live AI radar and deliver email\n"
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
    assert "AI_ENABLED: ${{ github.event_name == 'schedule' && 'true'" in job_environment
    assert 'DAILY_LLM_BUDGET: "1"' in job_environment
    assert 'AI_MAX_REQUESTS_PER_RUN: "10"' in job_environment
    assert "LLM_PROVIDER: deepseek" in job_environment
    assert 'LLM_MODEL: "deepseek-v4-flash"' in job_environment
    assert "DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}" in workflow
    assert "SEC_CONTACT_EMAIL: ${{ secrets.SEC_CONTACT_EMAIL }}" in workflow
    assert "--no-ai" not in workflow
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


def test_schedule_and_manual_defaults_activate_localized_c7_email() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "localization_mode:" in workflow
    run_mode_input = workflow.split("run_mode:", 1)[1].split("localization_mode:", 1)[0]
    localization_input = workflow.split("localization_mode:", 1)[1].split(
        "delivery_target:", 1
    )[0]
    assert "default: ai" in run_mode_input
    assert "default: zh-CN" in localization_input
    assert "- zh-CN" in workflow
    localization_switch = (
        "LOCALIZATION_ENABLED: "
        "${{ github.event_name == 'schedule' && 'true' || "
        "(inputs.localization_mode == 'zh-CN' && 'true' || 'false') }}"
    )
    assert localization_switch in workflow
    assert "LOCALIZATION_LOCALE: zh-CN" in workflow
    assert 'LOCALIZATION_MODEL: "deepseek-v4-flash"' in workflow
    assert 'LOCALIZATION_TIMEOUT_SECONDS: "90"' in workflow
    assert (
        "RUN_MODE: ${{ github.event_name == 'schedule' && 'ai' || "
        "inputs.run_mode || 'ai' }}"
    ) in workflow
    assert (
        "AI_ENABLED: ${{ github.event_name == 'schedule' && 'true' || "
        "(inputs.run_mode == 'no-ai' && 'false' || 'true') }}"
    ) in workflow
    assert (
        "DELIVERY_TARGET: ${{ github.event_name == 'schedule' && 'audience' || "
        "inputs.delivery_target || 'single' }}"
    ) in workflow
    assert (
        "AUDIENCE_ENABLED: ${{ github.event_name == 'schedule' && 'true' || "
        "(inputs.delivery_target == 'audience' && 'true' || 'false') }}"
    ) in workflow


def test_audience_delivery_requires_explicit_manual_target_and_never_starts_admin() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "delivery_target:" in workflow
    assert "default: single" in workflow
    assert "- audience" in workflow
    assert "radar daily --audience" in workflow
    assert "AUDIENCE_ENABLED" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL" in workflow
    assert "radar admin" not in workflow
    assert "EMAIL_TO" in workflow
    assert "recipient@example" not in workflow
    assert "github.event_name == 'schedule' && 'audience'" in workflow


def test_localization_cannot_silently_run_in_no_ai_mode(tmp_path) -> None:
    bash = shutil.which("bash")
    assert bash is not None

    completed = subprocess.run(
        [bash, "-c", _daily_run_script()],
        cwd=tmp_path,
        env={**os.environ, "LOCALIZATION_ENABLED": "true", "RUN_MODE": "no-ai"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "Chinese localization requires run_mode=ai" in completed.stderr


def test_operator_validation_workflow_is_manual_and_non_delivery_by_default() -> None:
    workflow = OPERATOR_VALIDATION_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "default: verify-db" in workflow
    assert "- send-test" in workflow
    assert "schedule:" not in workflow
    assert "if: ${{ inputs.mode == 'send-test' }}" in workflow
    assert "service.send_test(" in workflow
    assert "--audience" not in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}" in workflow


def test_operator_validation_can_seed_a_briefing_without_delivery() -> None:
    workflow = OPERATOR_VALIDATION_WORKFLOW.read_text(encoding="utf-8")

    assert "- seed-briefing" in workflow
    assert "if: ${{ inputs.mode == 'seed-briefing' }}" in workflow
    assert "EMAIL_MODE: disabled" in workflow
    assert 'AUDIENCE_ENABLED: "false"' in workflow
    assert "uv run radar daily --ai" in workflow
