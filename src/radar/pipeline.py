from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from radar.config import Settings, load_universe
from radar.deterministic import (
    assess_report_quality,
    build_deterministic_summary,
    cluster_documents,
    extract_events,
)
from radar.domain import (
    CollectionBatch,
    EvidencePack,
    PipelineResult,
    ProviderRunStatus,
    ProviderStatus,
    ReportQualityStatus,
    stable_id,
)
from radar.email import build_email_sender
from radar.llm import LLMProvider, build_llm_provider
from radar.providers import build_providers
from radar.report import HtmlReportRenderer, report_context
from radar.storage import RadarStore

logger = logging.getLogger(__name__)


def run_daily(settings: Settings, now: datetime | None = None) -> PipelineResult:
    timer_started = time.perf_counter()
    started_at = (now or datetime.now(UTC)).astimezone(UTC)
    business_now = started_at.astimezone(ZoneInfo(settings.business_timezone))
    report_date = business_now.date().isoformat()
    universe = load_universe(settings)
    llm = build_llm_provider(settings)
    ai_status = llm.name
    ai_profile = f"ai-{settings.llm_provider}" if settings.ai_enabled else "no-ai"
    run_key = f"daily:{report_date}:{settings.data_mode}:{ai_profile}:v0.1"
    run_id = stable_id("run", run_key)
    logger.info(
        "Pipeline started run_id=%s report_date=%s timezone=%s data_mode=%s ai_status=%s",
        run_id,
        report_date,
        settings.business_timezone,
        settings.data_mode,
        ai_status,
    )

    store = RadarStore(settings.database_url)
    store.initialize()
    store.start_run(
        run_id=run_id,
        run_key=run_key,
        started_at=started_at,
        data_mode=settings.data_mode,
        ai_status=ai_status,
    )

    try:
        return _run_started_pipeline(
            settings=settings,
            started_at=started_at,
            business_now=business_now,
            report_date=report_date,
            run_key=run_key,
            run_id=run_id,
            universe=universe,
            llm=llm,
            store=store,
            timer_started=timer_started,
        )
    except Exception as exc:
        elapsed_seconds = round(time.perf_counter() - timer_started, 3)
        try:
            store.fail_run(
                run_id,
                datetime.now(UTC),
                {
                    "error_type": type(exc).__name__,
                    "elapsed_seconds": elapsed_seconds,
                },
            )
        except Exception:
            logger.exception(
                "Failed to persist terminal run state run_id=%s original_error=%s",
                run_id,
                type(exc).__name__,
            )
        logger.error(
            "Pipeline failed run_id=%s error_type=%s elapsed_seconds=%.3f",
            run_id,
            type(exc).__name__,
            elapsed_seconds,
        )
        raise


def _run_started_pipeline(
    *,
    settings: Settings,
    started_at: datetime,
    business_now: datetime,
    report_date: str,
    run_key: str,
    run_id: str,
    universe: dict[str, Any],
    llm: LLMProvider,
    store: RadarStore,
    timer_started: float,
) -> PipelineResult:
    batches: list[CollectionBatch] = []
    provider_statuses: list[ProviderRunStatus] = []
    for provider in build_providers(settings, universe):
        logger.info("Provider started provider=%s", provider.name)
        try:
            batch = provider.collect(business_now)
            batches.append(batch)
            provider_statuses.append(
                ProviderRunStatus(
                    provider=provider.name,
                    status=batch.status,
                    item_count=len(batch.documents) + len(batch.markets) + len(batch.macro),
                    warnings=batch.warnings,
                    diagnostics=batch.diagnostics,
                )
            )
            logger.info(
                "Provider finished provider=%s status=%s documents=%d markets=%d "
                "macro=%d diagnostics=%s",
                provider.name,
                batch.status.value,
                len(batch.documents),
                len(batch.markets),
                len(batch.macro),
                batch.diagnostics,
            )
        except Exception as exc:  # provider isolation is a pipeline requirement
            logger.exception("Provider failed", extra={"provider": provider.name})
            provider_statuses.append(
                ProviderRunStatus(
                    provider=provider.name,
                    status=ProviderStatus.FAILED,
                    item_count=0,
                    warnings=[f"{type(exc).__name__}: {exc}"],
                )
            )

    documents = _flatten(batch.documents for batch in batches)
    markets = _flatten(batch.markets for batch in batches)
    macro = _flatten(batch.macro for batch in batches)
    clusters = cluster_documents(documents)
    events, claims, evidence = extract_events(clusters, universe["entities"], started_at)
    summary = build_deterministic_summary(markets, macro, events)
    logger.info(
        "Collection complete documents=%d markets=%d macro=%d clusters=%d events=%d",
        len(documents),
        len(markets),
        len(macro),
        len(clusters),
        len(events),
    )

    claim_map = {item.id: item for item in claims}
    evidence_map = {item.id: item for item in evidence}
    source_map = {item.id: item for item in documents}
    analyses = []
    for event in events[:6]:
        pack = EvidencePack(
            event=event,
            claims=[claim_map[item_id] for item_id in event.claim_ids],
            evidence=[evidence_map[item_id] for item_id in event.evidence_ids],
            sources=[source_map[item_id] for item_id in event.source_document_ids],
        )
        try:
            analyses.append(llm.analyze(pack))
        except Exception as exc:
            logger.exception("Optional AI analysis failed; deterministic report will continue")
            analyses.append(
                build_llm_provider(settings.model_copy(update={"ai_enabled": False})).analyze(
                    pack
                )
            )
            provider_statuses.append(
                ProviderRunStatus(
                    provider=f"llm:{llm.name}",
                    status=ProviderStatus.DEGRADED,
                    item_count=0,
                    warnings=[f"{type(exc).__name__}: {exc}"],
                )
            )

    store.save_documents(run_id, documents)
    store.save_evidence(run_id, evidence)
    store.save_claims(run_id, claims)
    store.save_events(run_id, events)
    store.save_markets(run_id, markets)
    store.save_macro(run_id, macro)

    expected_market_count = sum(
        batch.diagnostics.get("market_items_expected", 0) for batch in batches
    )
    if expected_market_count == 0:
        expected_market_count = (
            len(universe.get("assets", [])) if settings.data_mode == "live" else len(markets)
        )
    quality = assess_report_quality(
        market_count=len(markets),
        expected_market_count=expected_market_count,
        macro_count=len(macro),
        document_count=len(documents),
        event_count=len(events),
        provider_statuses=provider_statuses,
    )
    logger.info(
        "Report quality status=%s market_coverage=%.1f%% reasons=%s",
        quality.status.value,
        quality.market_coverage * 100,
        quality.reasons,
    )

    is_demo = any(batch.is_demo for batch in batches)
    ai_test_data = any(item.is_test_data for item in analyses)
    missing_data = [
        f"{status.provider}: {warning}"
        for status in provider_statuses
        for warning in status.warnings
        if status.status is not ProviderStatus.OK
    ]
    if not documents:
        missing_data.append("No source documents were collected.")
    if not markets:
        missing_data.append("No market observations were collected.")
    if not macro:
        missing_data.append("No macro observations were collected.")
    context = report_context(
        report_date=report_date,
        run_id=run_id,
        data_mode=settings.data_mode,
        is_demo=is_demo,
        ai_status=analyses[0].status if analyses else "disabled",
        ai_test_data=ai_test_data,
        quality=quality,
        provider_statuses=provider_statuses,
        markets=markets,
        macro=macro,
        events=events[:10],
        claims=claim_map,
        sources=source_map,
        analyses={item.event_id: item for item in analyses},
        summary=summary,
        coverage={
            "documents": len(documents),
            "events": len(events),
            "markets": len(markets),
            "macro": len(macro),
        },
        freshness={
            "latest_market_at": max((item.observed_at for item in markets), default=None),
            "latest_source_at": max((item.published_at for item in documents), default=None),
            "latest_macro_period": max((item.period for item in macro), default=None),
        },
        missing_data=missing_data,
    )
    renderer = HtmlReportRenderer()
    html = renderer.render(context)
    report_path, html_hash = renderer.write(
        html, settings.resolved_report_output_dir, report_date
    )
    report_id = stable_id("report", run_key)
    store.save_report(
        report_id=report_id,
        run_id=run_id,
        report_date=report_date,
        path=str(report_path),
        html_hash=html_hash,
        created_at=datetime.now(UTC),
    )
    logger.info("Report generated path=%s html_sha256=%s", report_path, html_hash)

    subject = f"Global Investment Radar — {report_date}"
    if quality.status is ReportQualityStatus.INSUFFICIENT:
        subject = f"INSUFFICIENT DATA — {subject}"
    logger.info("Email delivery started mode=%s", settings.email_mode)
    try:
        email_sender = build_email_sender(settings)
        email_status = email_sender.send(subject=subject, html=html)
    except Exception as exc:
        elapsed_seconds = round(time.perf_counter() - timer_started, 3)
        logger.error(
            "Email delivery failed error_type=%s report_preserved=%s elapsed_seconds=%.3f",
            type(exc).__name__,
            report_path,
            elapsed_seconds,
        )
        raise RuntimeError(f"Email delivery failed ({type(exc).__name__}).") from None
    logger.info("Email delivery finished status=%s", email_status)

    if quality.status is ReportQualityStatus.INSUFFICIENT:
        raise RuntimeError("Insufficient data quality gate rejected the daily report.")

    elapsed_seconds = round(time.perf_counter() - timer_started, 3)
    result = PipelineResult(
        report_path=str(report_path),
        run_id=run_id,
        report_date=report_date,
        is_demo=is_demo,
        ai_status=context["ai_status"],
        quality_status=quality.status,
        documents=len(documents),
        events=len(events),
        markets=len(markets),
        macro=len(macro),
        email_status=email_status,
        elapsed_seconds=elapsed_seconds,
        provider_statuses=provider_statuses,
    )
    details = {
        "documents": len(documents),
        "events": len(events),
        "markets": len(markets),
        "macro": len(macro),
        "quality_status": quality.status.value,
        "report_path": str(report_path),
        "email_status": email_status,
        "elapsed_seconds": elapsed_seconds,
    }
    logger.info(
        "Pipeline completed run_id=%s report_path=%s email_status=%s elapsed_seconds=%.3f",
        run_id,
        report_path,
        email_status,
        elapsed_seconds,
    )
    store.complete_run(run_id, datetime.now(UTC), details)
    return result


def _flatten[T](groups: Iterable[list[T]]) -> list[T]:
    return [item for group in groups for item in group]
