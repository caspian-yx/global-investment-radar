from __future__ import annotations

import hashlib
import json

import pytest


def test_email_body_audit_is_byte_exact_and_reproducible(tmp_path) -> None:
    from radar.email_audit import archive_email_body

    body = '<html lang="zh-CN">市场观察 · NVIDIA</html>'
    first = archive_email_body(body, run_id="run-1", output_dir=tmp_path)
    second = archive_email_body(body, run_id="run-1", output_dir=tmp_path)

    assert first == second
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert first.sha256 == expected
    assert first.body_path.read_bytes() == body.encode("utf-8")
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "run_id": "run-1",
        "renderer": "daily_email.html.j2",
        "renderer_version": "c9.1-presentation-v1",
        "sha256": expected,
        "encoding": "utf-8",
    }
    assert len(list(tmp_path.iterdir())) == 2


def test_email_body_audit_rejects_changed_bytes_for_existing_hash(tmp_path) -> None:
    from radar.email_audit import archive_email_body

    audit = archive_email_body("<p>body</p>", run_id="run-1", output_dir=tmp_path)
    audit.body_path.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="audit body conflict"):
        archive_email_body("<p>body</p>", run_id="run-1", output_dir=tmp_path)


def test_email_body_audit_rejects_different_body_for_same_run_id(tmp_path) -> None:
    from radar.email_audit import archive_email_body

    archive_email_body("<p>first</p>", run_id="run-1", output_dir=tmp_path)

    with pytest.raises(RuntimeError, match="email audit body conflict"):
        archive_email_body("<p>second</p>", run_id="run-1", output_dir=tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "email-run-1.html",
        "email-run-1.json",
    ]
