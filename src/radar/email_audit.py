"""Archive the exact single-delivery email body before any SMTP side effect."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EmailBodyAudit:
    body_path: Path
    manifest_path: Path
    sha256: str


def archive_email_body(html: str, *, run_id: str, output_dir: Path) -> EmailBodyAudit:
    """Persist recipient-free HTML and metadata, rejecting conflicting retries."""

    body = html.encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    # One run id owns one immutable body.  Do not put the digest in the
    # filename: doing so would allow a retry with changed bytes to create a
    # second artifact instead of surfacing an audit conflict.
    stem = f"email-{run_id}"
    body_path = output_dir / f"{stem}.html"
    manifest_path = output_dir / f"{stem}.json"
    manifest = {
        "run_id": run_id,
        "renderer": "daily_email.html.j2",
        "renderer_version": "c9.1-presentation-v1",
        "sha256": digest,
        "encoding": "utf-8",
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    if body_path.exists():
        if body_path.read_bytes() != body:
            raise RuntimeError("email audit body conflict")
    else:
        body_path.write_bytes(body)
    if manifest_path.exists():
        if manifest_path.read_bytes() != manifest_bytes:
            raise RuntimeError("email audit manifest conflict")
    else:
        manifest_path.write_bytes(manifest_bytes)
    if body_path.read_bytes() != body or manifest_path.read_bytes() != manifest_bytes:
        raise RuntimeError("email audit verification failed")
    return EmailBodyAudit(body_path=body_path, manifest_path=manifest_path, sha256=digest)
