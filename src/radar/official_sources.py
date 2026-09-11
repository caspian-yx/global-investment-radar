from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import JsonValue

from radar.domain import (
    CollectionBatch,
    ProviderStatus,
    SourceCategory,
    SourceDocument,
    SourceRole,
    SourceTier,
)
from radar.ingestion import build_source_document

SEC_PROVIDER_NAME = "sec-edgar-submissions"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
SEC_MAX_RESPONSE_BYTES = 10_000_000
SEC_DIAGNOSTIC_KEYS = (
    "ciks_expected",
    "ciks_succeeded",
    "ciks_failed",
    "requests_attempted",
    "total_filings",
    "qualified_filings",
    "excluded_forms",
    "outside_lookback",
    "malformed_filings",
    "duplicate_filings",
    "historical_files_ignored",
)
ACCESSION_PATTERN = re.compile(r"^\d{10}-\d{2}-\d{6}$")
CONTACT_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class SECParseResult:
    documents: list[SourceDocument] = field(default_factory=list)
    diagnostics: dict[str, int] = field(
        default_factory=lambda: {
            key: 0
            for key in SEC_DIAGNOSTIC_KEYS
            if key
            not in {
                "ciks_expected",
                "ciks_succeeded",
                "ciks_failed",
                "requests_attempted",
            }
        }
    )


class SECFilingsProvider:
    """Collect recent filing metadata from SEC's official Submissions JSON."""

    name = SEC_PROVIDER_NAME

    def __init__(
        self,
        *,
        filers: list[dict[str, Any]],
        forms: list[str],
        lookback_hours: int,
        contact_email: str | None,
        timeout: float = 15.0,
        request_interval_seconds: float = 0.12,
    ) -> None:
        self.filers = filers
        self.forms = frozenset(forms)
        self.lookback = timedelta(hours=lookback_hours)
        self.contact_email = contact_email
        self.timeout = timeout
        self.request_interval_seconds = request_interval_seconds

    def collect(self, collected_at: datetime) -> CollectionBatch:
        diagnostics = {key: 0 for key in SEC_DIAGNOSTIC_KEYS}
        diagnostics["ciks_expected"] = len(self.filers)
        if not self._valid_contact_email(self.contact_email):
            return CollectionBatch(
                provider=self.name,
                status=ProviderStatus.FAILED,
                warnings=[
                    "SEC collection requires a valid configured contact email; "
                    "no request was sent."
                ],
                diagnostics=diagnostics,
                error_type="SECContactConfigurationError",
                collected_at=collected_at,
            )

        documents: list[SourceDocument] = []
        warnings: list[str] = []
        last_request_at: float | None = None
        headers = {
            "User-Agent": f"GlobalInvestmentRadar/0.1 {self.contact_email}",
            "Accept-Encoding": "gzip, deflate",
        }
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for filer in self.filers:
                filer_label = self._safe_filer_label(filer)
                try:
                    cik = self._normalize_cik(filer.get("cik"))
                    last_request_at = self._respect_rate_limit(last_request_at)
                    diagnostics["requests_attempted"] += 1
                    response = client.get(SEC_SUBMISSIONS_URL.format(cik=cik))
                    response.raise_for_status()
                    if len(response.content) > SEC_MAX_RESPONSE_BYTES:
                        raise ValueError("SEC response exceeded size limit")
                    body = response.json()
                    parsed = self._parse_submissions(
                        body=body,
                        configured_cik=cik,
                        configured_name=filer_label,
                        collected_at=collected_at,
                    )
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    diagnostics["ciks_failed"] += 1
                    warnings.append(
                        f"{filer_label}: SEC submissions collection failed "
                        f"({type(exc).__name__})."
                    )
                    continue

                diagnostics["ciks_succeeded"] += 1
                documents.extend(parsed.documents)
                for key, value in parsed.diagnostics.items():
                    diagnostics[key] += value

        if self.filers and diagnostics["ciks_failed"] == len(self.filers):
            status = ProviderStatus.FAILED
            error_type = "SECCollectionError"
        elif diagnostics["ciks_failed"]:
            status = ProviderStatus.DEGRADED
            error_type = None
        elif not documents:
            status = ProviderStatus.NO_DATA
            error_type = None
            warnings.append(
                "SEC submissions parsed successfully; no configured recent filings were found."
            )
        else:
            status = ProviderStatus.OK
            error_type = None
        return CollectionBatch(
            provider=self.name,
            status=status,
            documents=documents,
            warnings=warnings,
            diagnostics=diagnostics,
            error_type=error_type,
            collected_at=collected_at,
        )

    def _parse_submissions(
        self,
        *,
        body: Any,
        configured_cik: str,
        configured_name: str,
        collected_at: datetime,
    ) -> SECParseResult:
        if not isinstance(body, dict):
            raise ValueError("SEC response must be an object")
        response_cik = self._normalize_cik(body.get("cik"))
        if response_cik != configured_cik:
            raise ValueError("SEC response CIK does not match the configured filer")
        response_name = body.get("name")
        publisher = (
            response_name.strip()
            if isinstance(response_name, str) and response_name.strip()
            else configured_name
        )
        filings = body.get("filings")
        if not isinstance(filings, dict):
            raise ValueError("SEC response is missing filings")
        recent = filings.get("recent")
        if not isinstance(recent, dict):
            raise ValueError("SEC response is missing recent filings")
        accession_numbers = recent.get("accessionNumber")
        if not isinstance(accession_numbers, list):
            raise ValueError("SEC recent filings are malformed")

        result = SECParseResult()
        historical_files = filings.get("files")
        if isinstance(historical_files, list):
            result.diagnostics["historical_files_ignored"] = len(historical_files)
        result.diagnostics["total_filings"] = len(accession_numbers)
        seen_accessions: set[str] = set()
        now_utc = collected_at.astimezone(UTC)
        effective_lookback = self.lookback
        if now_utc.weekday() == 0 and effective_lookback < timedelta(hours=96):
            effective_lookback = timedelta(hours=96)

        for index in range(len(accession_numbers)):
            accession = self._string_column(recent, "accessionNumber", index)
            form = self._string_column(recent, "form", index)
            acceptance_raw = self._string_column(recent, "acceptanceDateTime", index)
            if (
                not ACCESSION_PATTERN.fullmatch(accession)
                or not form
                or not acceptance_raw
            ):
                result.diagnostics["malformed_filings"] += 1
                continue
            if form not in self.forms:
                result.diagnostics["excluded_forms"] += 1
                continue
            try:
                published_at = self._parse_acceptance_time(acceptance_raw)
            except ValueError:
                result.diagnostics["malformed_filings"] += 1
                continue
            if published_at > now_utc + timedelta(minutes=5):
                result.diagnostics["malformed_filings"] += 1
                continue
            if now_utc - published_at > effective_lookback:
                result.diagnostics["outside_lookback"] += 1
                continue
            if accession in seen_accessions:
                result.diagnostics["duplicate_filings"] += 1
                continue
            seen_accessions.add(accession)

            report_date = self._string_column(recent, "reportDate", index)
            items = self._string_column(recent, "items", index)
            primary_document = self._string_column(recent, "primaryDocument", index)
            metadata: dict[str, JsonValue] = {
                "cik": response_cik,
                "form": form,
                "report_date": report_date,
                "items": items,
                "primary_document": primary_document,
            }
            content_parts = [f"SEC form {form} accepted {published_at.isoformat()}."]
            if report_date:
                content_parts.append(f"Report date: {report_date}.")
            if items:
                content_parts.append(f"Items: {items}.")
            if primary_document:
                content_parts.append(f"Primary document: {primary_document}.")
            accession_compact = accession.replace("-", "")
            archive_cik = str(int(response_cik))
            url = (
                f"{SEC_ARCHIVES_URL}/{archive_cik}/{accession_compact}/"
                f"{accession}-index.html"
            )
            result.documents.append(
                build_source_document(
                    provider=self.name,
                    trusted_provider_native_id=accession,
                    source_category=SourceCategory.REGULATORY,
                    source_role=SourceRole.PRIMARY,
                    tier=SourceTier.A,
                    publisher=publisher,
                    url=url,
                    title=f"{publisher} {form} filing",
                    content=" ".join(content_parts),
                    language="en",
                    region="US",
                    published_at=published_at,
                    collected_at=collected_at,
                    provider_metadata=metadata,
                )
            )
            result.diagnostics["qualified_filings"] += 1
        return result

    def _respect_rate_limit(self, last_request_at: float | None) -> float:
        if last_request_at is not None and self.request_interval_seconds > 0:
            remaining = self.request_interval_seconds - (time.monotonic() - last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        return time.monotonic()

    def _parse_acceptance_time(self, raw: str) -> datetime:
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid SEC acceptance time") from exc
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("SEC acceptance time must include a timezone")
        return value.astimezone(UTC)

    def _string_column(self, recent: dict[str, Any], key: str, index: int) -> str:
        column = recent.get(key)
        if not isinstance(column, list) or index >= len(column):
            return ""
        value = column[index]
        return value.strip() if isinstance(value, str) else ""

    def _normalize_cik(self, value: Any) -> str:
        if isinstance(value, bool):
            raise ValueError("invalid SEC CIK")
        if isinstance(value, int):
            digits = str(value)
        elif isinstance(value, str):
            digits = value.strip()
        else:
            raise ValueError("invalid SEC CIK")
        if not digits.isdigit() or len(digits) > 10:
            raise ValueError("invalid SEC CIK")
        return digits.zfill(10)

    def _valid_contact_email(self, value: str | None) -> bool:
        return bool(value and CONTACT_EMAIL_PATTERN.fullmatch(value.strip()))

    def _safe_filer_label(self, filer: dict[str, Any]) -> str:
        name = filer.get("name")
        return name.strip() if isinstance(name, str) and name.strip() else "Configured filer"
