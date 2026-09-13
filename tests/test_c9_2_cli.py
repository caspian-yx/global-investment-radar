from types import SimpleNamespace

from typer.testing import CliRunner

from radar.cli import app


def test_daily_audience_is_explicit_and_passed_to_pipeline(monkeypatch) -> None:
    calls: list[bool] = []

    def fake_run_daily(settings, *, audience=False):
        calls.append(audience)
        return SimpleNamespace(model_dump_json=lambda indent=2: "{}")

    monkeypatch.setattr("radar.cli.run_daily", fake_run_daily)
    result = CliRunner().invoke(app, ["daily", "--audience"])

    assert result.exit_code == 0, result.stdout
    assert calls == [True]


def test_recipient_cli_uses_service_and_hides_personal_note_from_list(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")
    monkeypatch.setenv("APP_ENV", "test")
    runner = CliRunner()

    added = runner.invoke(
        app,
        [
            "recipients",
            "add",
            "Alice",
            "alice@example.com",
            "--mode",
            "english",
            "--note",
            "private note",
        ],
    )
    assert added.exit_code == 0, added.stdout
    listed = runner.invoke(app, ["recipients", "list"])

    assert listed.exit_code == 0, listed.stdout
    assert "alice@example.com" in listed.stdout
    assert "private note" not in listed.stdout


def test_preview_command_does_not_construct_or_call_smtp(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")
    monkeypatch.setenv("APP_ENV", "test")
    runner = CliRunner()
    added = runner.invoke(
        app, ["recipients", "add", "Alice", "alice@example.com", "--mode", "english"]
    )
    assert added.exit_code == 0, added.stdout
    recipient_id = added.stdout.strip()

    preview = runner.invoke(app, ["preview", "--recipient", recipient_id])
    assert preview.exit_code != 0
    assert "SMTP" not in preview.stdout
