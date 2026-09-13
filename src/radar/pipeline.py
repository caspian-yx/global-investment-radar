from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from radar.briefing import compose_daily_brief, select_daily_stories
from radar.config import Settings, load_universe
from radar.deterministic import (
    assess_report_quality,
    extract_market_events,
    extract_story_events,
)
from radar.domain import (
    AIEnrichmentResult,
    EvidencePack,
    PipelineResult,
    ProviderRunStatus,
    ProviderStatus,
    ReportQualityStatus,
    StructuredAnalysis,
    stable_id,
)
from radar.email import build_email_sender
from radar.enrichment import run_structured_explanation_enrichment
from radar.explanation import (
    build_cross_asset_context,
    build_driver_candidates,
    build_explanation_bundles,
    build_macro_backdrop,
    build_observation_evidence,
    unique_by_id,
    validate_driver_candidate_bundle,
)
from radar.impact import build_c8_market_target_events, evaluate_market_impacts
from radar.importance import (
    C7_RULE_VERSION,
    load_view_profiles,
    rank_events,
    rank_scores,
    score_events,
    summarize_c7_scores,
)
from radar.ingestion import collect_intelligence_providers, deduplicate_source_documents
from radar.llm import AnalysisProvider, LLMError, build_generation_provider, build_llm_provider
from radar.localization import (
    C91_RULE_VERSION,
    LocalizationDiagnostics,
    build_localization_snapshot,
    localize_briefing,
    validate_localization_snapshot,
)
from radar.macro import build_macro_source_document
from radar.providers import build_providers
from radar.relevance import (
    EventTargetDecision,
    build_event_target_relationships,
    summarize_relationships,
)
from radar.report import HtmlReportRenderer, report_context
from radar.storage import RadarStore
from radar.story import STORY_RULE_VERSION, consolidate_stories

logger = logging.getLogger(__name__)


def _initial_ai_status(settings: Settings) -> str:
    if not settings.effective_ai_enabled:
        return "disabled"
    provider = settings.llm_provider.casefold().strip()
    if provider in {"", "disabled", "none"}:
        return "disabled"
    if provider == "fake":
        return "disabled" if settings.app_env == "production" else "test-data"
    return "enabled"


def _resolve_ai_status(
    settings: Settings,
    enrichment: AIEnrichmentResult,
    analyses: list[StructuredAnalysis],
    *,
    has_bundles: bool,
    provider_available: bool,
) -> str:
    legacy_status = analyses[0].status if analyses else None
    if not settings.effective_ai_enabled:
        return "disabled"
    provider = settings.llm_provider.casefold().strip()
    if provider == "fake" and settings.app_env == "production":
        return "disabled"
    if provider == "fake" and legacy_status == "test-data":
        return "test-data"
    if not has_bundles:
        return "enabled"
    if not provider_available:
        return "failed"

    usage = enrichment.usage
    if usage.requests_succeeded:
        if usage.requests_failed or usage.targets_skipped_due_to_budget:
            return "partial"
        return "enabled"
    if usage.requests_failed:
        return "failed"
    if usage.targets_skipped_due_to_budget or usage.budget_exhausted:
        return "partial"
    return "failed"


def run_daily(settings: Settings, now: datetime | None = None) -> PipelineResult:
    timer_started = time.perf_counter()
    started_at = (now or datetime.now(UTC)).astimezone(UTC)
    business_now = started_at.astimezone(ZoneInfo(settings.business_timezone))
    report_date = business_now.date().isoformat()
    universe = load_universe(settings)
    llm = build_llm_provider(settings)
    ai_status = _initial_ai_status(settings)
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
    llm: AnalysisProvider,
    store: RadarStore,
    timer_started: float,
) -> PipelineResult:
    batches = collect_intelligence_providers(
        build_providers(settings, universe),
        business_now,
    )
    provider_statuses: list[ProviderRunStatus] = []
    for batch in batches:
        provider_statuses.append(
            ProviderRunStatus(
                provider=batch.provider,
                status=batch.status,
                item_count=len(batch.documents) + len(batch.markets) + len(batch.macro),
                warnings=batch.warnings,
                diagnostics=batch.diagnostics,
                error_type=batch.error_type,
            )
        )
        logger.info(
            "Provider finished provider=%s status=%s documents=%d markets=%d "
            "macro=%d diagnostics=%s",
            batch.provider,
            batch.status.value,
            len(batch.documents),
            len(batch.markets),
            len(batch.macro),
            batch.diagnostics,
        )

    document_dedupe = deduplicate_source_documents(_flatten(batch.documents for batch in batches))
    documents = document_dedupe.documents
    logger.info("Document ingestion deduplication diagnostics=%s", document_dedupe.diagnostics)
    markets = _flatten(batch.markets for batch in batches)
    macro = _flatten(batch.macro for batch in batches)
    macro_source_document_ids = {
        item.source_document_id for item in macro if item.source_document_id is not None
    }
    official_documents = [item for item in documents if item.id not in macro_source_document_ids]
    existing_stories = store.load_stories(STORY_RULE_VERSION)
    existing_memberships = store.load_story_memberships(STORY_RULE_VERSION)
    persisted_story_documents = store.load_source_documents(
        membership.source_document_id for membership in existing_memberships
    )
    story_consolidation = consolidate_stories(
        official_documents,
        universe["entities"],
        existing_stories=existing_stories,
        existing_memberships=existing_memberships,
        known_documents=persisted_story_documents,
        now=started_at,
    )
    logger.info("Story consolidation diagnostics=%s", story_consolidation.diagnostics)
    story_documents_by_id = {
        document.id: document
        for members in story_consolidation.documents_by_story.values()
        for document in members
    }
    current_documents_by_id = {document.id: document for document in documents}
    story_documents_by_id.update(current_documents_by_id)
    documents = [story_documents_by_id[item_id] for item_id in sorted(story_documents_by_id)]
    official_events, official_claims, official_evidence = extract_story_events(
        story_consolidation.stories,
        story_documents_by_id.values(),
        started_at,
    )
    market_events, market_claims, market_evidence = extract_market_events(
        markets, universe["assets"], started_at
    )
    macro_events, macro_claims, macro_evidence = store.save_macro_with_first_observed_events(
        run_id, macro, started_at
    )
    canonical_macro = store.load_canonical_macro_observations(macro)
    macro_source_documents = [
        document
        for item in canonical_macro
        if (document := build_macro_source_document(item)) is not None
    ]
    documents = unique_by_id([*documents, *macro_source_documents])
    events = sorted(
        [*official_events, *market_events, *macro_events],
        key=lambda item: item.importance_score,
        reverse=True,
    )
    views = load_view_profiles(universe)
    raw_c7_scores = score_events(
        events,
        story_consolidation.stories,
        documents,
        views,
        universe,
        started_at,
    )
    c7_scores = [
        ranked_score
        for view in views
        for ranked_score in rank_scores([item for item in raw_c7_scores if item.view_id == view.id])
    ]
    selected_view = next(
        (view for view in views if view.id == settings.radar_view_id),
        None,
    )
    if selected_view is None:
        raise ValueError(f"Configured RADAR_VIEW_ID is unknown: {settings.radar_view_id}")
    selected_c7_scores = [item for item in c7_scores if item.view_id == selected_view.id]
    ranked_events = rank_events(events, selected_c7_scores)
    c7_diagnostics = summarize_c7_scores(c7_scores)
    c7_diagnostics.update(
        {
            "selected_for_report": min(10, len(ranked_events)),
            "filtered_from_report": max(0, len(ranked_events) - 10),
        }
    )
    logger.info(
        "C7 scoring view=%s diagnostics=%s",
        selected_view.id,
        c7_diagnostics,
    )
    c1_claims = [*official_claims, *market_claims, *macro_claims]
    c1_evidence = [*official_evidence, *market_evidence, *macro_evidence]
    observation_evidence = build_observation_evidence(markets, [])
    cross_asset_claims, cross_asset_evidence, _cross_asset_by_target = build_cross_asset_context(
        markets, market_events, universe["assets"], started_at
    )
    macro_backdrop_claims, macro_backdrop_evidence, _macro_backdrop_by_target = (
        build_macro_backdrop(canonical_macro, market_events, started_at)
    )
    evidence = unique_by_id(
        [
            *c1_evidence,
            *observation_evidence,
            *cross_asset_evidence,
            *macro_backdrop_evidence,
        ]
    )
    candidate_events = unique_by_id([*official_events, *macro_events])
    relationships = build_event_target_relationships(
        target_events=market_events,
        candidate_events=candidate_events,
        documents=documents,
        assets=universe["assets"],
        entity_config=universe.get("entities", []),
    )
    relationship_summary = summarize_relationships(relationships)
    logger.info(
        "C6 relationships evaluated=%d allowed=%d rejected=%d "
        "rejected_by_reason=%s allowed_by_channel=%s",
        relationship_summary["evaluated"],
        relationship_summary["allowed"],
        relationship_summary["rejected"],
        relationship_summary["rejected_by_reason"],
        relationship_summary["allowed_by_channel"],
    )
    c8_target_events = build_c8_market_target_events(markets, started_at)
    c8_relationships = build_event_target_relationships(
        target_events=c8_target_events,
        candidate_events=candidate_events,
        documents=documents,
        assets=universe["assets"],
        entity_config=universe.get("entities", []),
    )
    c8_evaluation = evaluate_market_impacts(
        candidate_events=candidate_events,
        market_events=c8_target_events,
        markets=markets,
        relationships=c8_relationships,
        stories=story_consolidation.stories,
        assets=universe["assets"],
        evaluated_at=started_at,
    )
    logger.info("C8 market reaction diagnostics=%s", c8_evaluation.diagnostics.model_dump())
    allowed_event_ids_by_target: dict[str, set[str]] = {
        target.id: set() for target in market_events
    }
    for relationship in relationships:
        if relationship.decision is EventTargetDecision.ALLOW:
            allowed_event_ids_by_target[relationship.target_event_id].add(
                relationship.candidate_event_id
            )
    candidate_events_by_id = {item.id: item for item in candidate_events}
    driver_candidates = []
    driver_claims = []
    for target in sorted(market_events, key=lambda item: item.id):
        target_driver_candidates, target_driver_claims = build_driver_candidates(
            target_events=[target],
            candidate_events=[
                candidate_events_by_id[event_id]
                for event_id in sorted(allowed_event_ids_by_target[target.id])
            ],
            evidence=evidence,
            documents=documents,
            assets=universe["assets"],
            as_of=started_at,
        )
        driver_candidates.extend(target_driver_candidates)
        driver_claims.extend(target_driver_claims)
    driver_candidates = unique_by_id(driver_candidates)
    driver_claims = unique_by_id(driver_claims)
    claims = unique_by_id([*c1_claims, *cross_asset_claims, *macro_backdrop_claims, *driver_claims])
    for candidate in driver_candidates:
        validate_driver_candidate_bundle(
            candidate,
            events=events,
            claims=claims,
            evidence=evidence,
            markets=markets,
            documents=documents,
            assets=universe["assets"],
        )
    explanation_bundles = build_explanation_bundles(
        target_events=market_events,
        driver_candidates=driver_candidates,
        claims=claims,
        evidence=evidence,
    )
    driver_candidates = [
        candidate for bundle in explanation_bundles for candidate in bundle.driver_candidates
    ]
    generation_bundles = [bundle for bundle in explanation_bundles if bundle.driver_candidates]
    generation_provider = None
    if settings.effective_ai_enabled and generation_bundles:
        try:
            generation_provider = build_generation_provider(settings)
        except LLMError as exc:
            logger.warning(
                "Optional structured explanation provider unavailable error_type=%s",
                type(exc).__name__,
            )
    ai_enrichment = run_structured_explanation_enrichment(
        generation_bundles,
        generation_provider,
        max_requests=settings.ai_max_requests_per_run,
        max_input_tokens=settings.ai_max_input_tokens_per_run,
        max_output_tokens=settings.ai_max_output_tokens_per_run,
    )
    structured_explanations = ai_enrichment.explanations
    logger.info(
        "AI enrichment bundles=%d attempted=%d succeeded=%d failed=%d "
        "skipped_budget=%d input_tokens=%d output_tokens=%d total_tokens=%d "
        "budget_exhausted=%s",
        len(generation_bundles),
        ai_enrichment.usage.requests_attempted,
        ai_enrichment.usage.requests_succeeded,
        ai_enrichment.usage.requests_failed,
        ai_enrichment.usage.targets_skipped_due_to_budget,
        ai_enrichment.usage.input_tokens,
        ai_enrichment.usage.output_tokens,
        ai_enrichment.usage.total_tokens,
        str(ai_enrichment.usage.budget_exhausted).lower(),
    )
    logger.info(
        "Collection complete documents=%d markets=%d macro=%d stories=%d "
        "official_events=%d market_events=%d macro_events=%d total_events=%d "
        "driver_candidates=%d",
        len(documents),
        len(markets),
        len(macro),
        len(story_consolidation.stories),
        len(official_events),
        len(market_events),
        len(macro_events),
        len(events),
        len(driver_candidates),
    )

    claim_map = {item.id: item for item in claims}
    evidence_map = {item.id: item for item in evidence}
    source_map = {item.id: item for item in documents}
    analyses = []
    for event in events[:6]:
        pack = EvidencePack(
            event=event.model_copy(deep=True),
            claims=[claim_map[item_id].model_copy(deep=True) for item_id in event.claim_ids],
            evidence=[
                evidence_map[item_id].model_copy(deep=True) for item_id in event.evidence_ids
            ],
            sources=[
                source_map[item_id].model_copy(deep=True) for item_id in event.source_document_ids
            ],
        )
        try:
            analyses.append(llm.analyze(pack))
        except Exception as exc:
            logger.exception("Optional AI analysis failed; deterministic report will continue")
            analyses.append(
                build_llm_provider(settings.model_copy(update={"ai_enabled": False})).analyze(pack)
            )
            provider_statuses.append(
                ProviderRunStatus(
                    provider=f"llm:{llm.name}",
                    status=ProviderStatus.DEGRADED,
                    item_count=0,
                    warnings=[f"{type(exc).__name__}: {exc}"],
                )
            )
    ai_status = _resolve_ai_status(
        settings,
        ai_enrichment,
        analyses,
        has_bundles=bool(generation_bundles),
        provider_available=generation_provider is not None,
    )

    store.save_documents(run_id, documents)
    store.save_stories(run_id, story_consolidation.stories)
    store.save_story_memberships(story_consolidation.memberships)
    store.save_evidence(run_id, evidence)
    store.save_claims(run_id, claims)
    store.save_events(run_id, events)
    store.save_driver_candidates(run_id, driver_candidates)
    store.save_markets(run_id, markets)
    store.save_c7_scores(run_id, c7_scores)
    store.save_market_impacts(run_id, c8_evaluation.records)

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
        official_event_count=len(official_events),
        provider_statuses=provider_statuses,
    )
    logger.info(
        "Report quality status=%s market_coverage=%.1f%% reasons=%s",
        quality.status.value,
        quality.market_coverage * 100,
        quality.reasons,
    )

    c9_selection = select_daily_stories(
        events=events,
        stories=story_consolidation.stories,
        documents=documents,
        scores=selected_c7_scores,
        view_id=selected_view.id,
    )
    briefing = store.load_daily_briefing(run_id, selected_view.id, c9_selection.rule_version)
    if briefing is None:
        briefing = compose_daily_brief(
            selection=c9_selection,
            run_id=run_id,
            report_date=report_date,
            view_name=selected_view.name,
            composed_at=started_at,
            ai_status=ai_status,
            quality=quality,
            document_count=len(documents),
            event_count=len(events),
            provider_statuses=provider_statuses,
            markets=markets,
            assets=universe["assets"],
            market_impacts=c8_evaluation.records,
            explanation_bundles=explanation_bundles,
            structured_explanations=structured_explanations,
        )
        store.save_daily_briefing(briefing)
    else:
        logger.info(
            "C9 immutable snapshot reused run_id=%s view=%s rule=%s",
            run_id,
            selected_view.id,
            c9_selection.rule_version,
        )
    logger.info("C9 daily briefing diagnostics=%s", briefing.diagnostics.model_dump())

    localized_content = None
    localization_model = settings.localization_model or settings.llm_model
    localization_diagnostics = LocalizationDiagnostics(
        enabled=False,
        locale=settings.localization_locale,
        requested_model=localization_model,
        status="disabled",
        request_count=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_seconds=0,
        snapshot_reused=False,
    )
    if settings.effective_localization_enabled and localization_model:
        localization_snapshot = store.load_localization_snapshot(
            briefing.id,
            settings.localization_locale,
            C91_RULE_VERSION,
            localization_model,
        )
        if localization_snapshot is not None:
            validate_localization_snapshot(localization_snapshot, briefing)
            localized_content = localization_snapshot.content
            localization_diagnostics = LocalizationDiagnostics(
                enabled=True,
                locale=localization_snapshot.locale,
                requested_model=localization_snapshot.requested_model,
                returned_model=localization_snapshot.returned_model,
                provider=localization_snapshot.provider,
                status="reused",
                request_count=0,
                input_tokens=localization_snapshot.usage.input_tokens,
                output_tokens=localization_snapshot.usage.output_tokens,
                total_tokens=localization_snapshot.usage.total_tokens,
                latency_seconds=localization_snapshot.latency_seconds,
                snapshot_reused=True,
            )
        else:
            localization_provider = generation_provider
            if (
                localization_provider is None
                or localization_model != settings.llm_model
                or settings.localization_timeout_seconds != settings.llm_timeout_seconds
            ):
                try:
                    localization_provider = build_generation_provider(
                        settings.model_copy(
                            update={
                                "llm_model": localization_model,
                                "llm_timeout_seconds": settings.localization_timeout_seconds,
                            }
                        )
                    )
                except LLMError as exc:
                    logger.warning(
                        "C9.1 localization provider unavailable error_type=%s",
                        type(exc).__name__,
                    )
                    localization_provider = None
            localization_result = localize_briefing(
                briefing,
                localization_provider,
                enabled=True,
                requested_model=localization_model,
                max_output_tokens=settings.ai_max_output_tokens_per_run,
            )
            localization_diagnostics = localization_result.diagnostics
            if localization_result.content is not None:
                localization_snapshot = build_localization_snapshot(
                    briefing,
                    localization_result,
                    created_at=started_at,
                )
                store.save_localization_snapshot(localization_snapshot)
                localized_content = localization_snapshot.content
    logger.info(
        "C9.1 localization diagnostics=%s",
        localization_diagnostics.model_dump(mode="json"),
    )

    is_demo = any(batch.is_demo for batch in batches)
    ai_test_data = any(item.is_test_data for item in analyses)
    context = report_context(
        briefing=briefing,
        data_mode=settings.data_mode,
        is_demo=is_demo,
        ai_test_data=ai_test_data,
        localized=localized_content,
    )
    renderer = HtmlReportRenderer()
    html = renderer.render(context)
    report_path, html_hash = renderer.write(html, settings.resolved_report_output_dir, report_date)
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

    subject = (
        f"Global Investment Radar — {report_date} 全球投资晨报"
        if localized_content is not None
        else f"Global Investment Radar — {report_date}"
    )
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
        ai_status=briefing.ai_status,
        quality_status=quality.status,
        documents=len(documents),
        events=len(events),
        official_events=len(official_events),
        market_events=len(market_events),
        macro_events=len(macro_events),
        driver_candidates=len(driver_candidates),
        markets=len(markets),
        macro=len(macro),
        email_status=email_status,
        elapsed_seconds=elapsed_seconds,
        provider_statuses=provider_statuses,
    )
    details = {
        "documents": len(documents),
        "events": len(events),
        "official_events": len(official_events),
        "market_events": len(market_events),
        "macro_events": len(macro_events),
        "driver_candidates": len(driver_candidates),
        "markets": len(markets),
        "macro": len(macro),
        "quality_status": quality.status.value,
        "report_path": str(report_path),
        "email_status": email_status,
        "elapsed_seconds": elapsed_seconds,
        "story_consolidation": story_consolidation.diagnostics,
        "c7": {
            "selected_view": selected_view.id,
            "rule_version": C7_RULE_VERSION,
            "diagnostics": c7_diagnostics,
        },
        "c8": c8_evaluation.diagnostics.model_dump(mode="json"),
        "c9": briefing.diagnostics.model_dump(mode="json"),
        "c91": localization_diagnostics.model_dump(mode="json"),
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
