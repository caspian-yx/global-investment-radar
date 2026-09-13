from __future__ import annotations

import json

import pytest
from test_briefing_composition import _compose
from test_c8_storage import _record

from radar.llm import FakeLLMProvider, LLMResponse
from radar.localization import (
    build_localization_payload,
    build_localization_prompt,
    localize_briefing,
    parse_localization_response,
)


def _translated_payload(brief):
    payload = build_localization_payload(brief)
    return {
        "locale": payload.locale,
        "briefing_id": payload.briefing_id,
        "briefing_rule_version": payload.briefing_rule_version,
        "overnight": [
            {"key": item.key, "text": f"中文摘要：{item.text}"} for item in payload.overnight
        ],
        "primary_stories": [
            {
                "key": item.key,
                "headline": f"中文标题：{item.headline}",
                "why_it_matters": f"中文说明：{item.why_it_matters}",
            }
            for item in payload.primary_stories
        ],
        "market_reactions": [
            {
                "key": item.key,
                "summary": item.summary,
                "uncertainty": item.uncertainty,
            }
            for item in payload.market_reactions
        ],
        "under_radar": [
            {
                "key": item.key,
                "headline": f"中文背景：{item.headline}",
                "note": f"中文备注：{item.note}",
            }
            for item in payload.under_radar
        ],
        "watch_today": [
            {"key": item.key, "text": f"中文关注：{item.text}"}
            for item in payload.watch_today
        ],
        "market_names": [
            {"key": item.key, "text": f"中文市场：{item.text}"}
            for item in payload.market_names
        ],
    }


def test_payload_has_stable_keys_and_prompt_contains_only_bounded_context() -> None:
    brief, _ = _compose()
    payload = build_localization_payload(brief)

    assert payload.briefing_id == brief.id
    assert [item.key for item in payload.primary_stories] == [
        f"story:{item.event_id}" for item in brief.primary_stories
    ]
    assert [item.key for item in payload.watch_today] == [
        f"watch:{item.event_id}" for item in brief.watch_today
    ]
    prompt = build_localization_prompt(payload)
    assert "You are the Chinese localization editor" in prompt
    assert "Do not use external knowledge" in prompt
    assert "article body" not in prompt.casefold()


def test_strict_response_accepts_exact_key_closure_and_order() -> None:
    brief, _ = _compose()
    payload = build_localization_payload(brief)

    result = parse_localization_response(
        json.dumps(_translated_payload(brief), ensure_ascii=False), payload
    )

    assert result.locale == "zh-CN"
    assert [item.key for item in result.primary_stories] == [
        item.key for item in payload.primary_stories
    ]


@pytest.mark.parametrize(
    "mutation_name",
    [
        "missing",
        "unknown",
        "reordered",
    ],
)
def test_strict_response_rejects_missing_unknown_or_reordered_keys(mutation_name: str) -> None:
    brief, _ = _compose()
    payload = build_localization_payload(brief)
    data = _translated_payload(brief)
    if mutation_name == "missing":
        data["primary_stories"].pop()
    elif mutation_name == "unknown":
        data["primary_stories"].append(
            {"key": "story:unknown", "headline": "x", "why_it_matters": "y"}
        )
    else:
        data["primary_stories"][0], data["primary_stories"][1] = (
            data["primary_stories"][1],
            data["primary_stories"][0],
        )

    with pytest.raises(ValueError, match="key|coverage|order"):
        parse_localization_response(json.dumps(data, ensure_ascii=False), payload)


def test_strict_response_rejects_extra_fields_and_json_wrappers() -> None:
    brief, _ = _compose()
    payload = build_localization_payload(brief)
    data = _translated_payload(brief)
    data["unexpected"] = "not authorized"

    with pytest.raises(ValueError):
        parse_localization_response(json.dumps(data, ensure_ascii=False), payload)
    with pytest.raises(ValueError, match="JSON|fence"):
        parse_localization_response(f"```json\n{json.dumps(data)}\n```", payload)
    with pytest.raises(ValueError, match="JSON|prose"):
        parse_localization_response("Here is the translation:\n" + json.dumps(data), payload)


def test_duplicate_json_keys_are_rejected() -> None:
    brief, _ = _compose()
    payload = build_localization_payload(brief)
    data = _translated_payload(brief)
    raw = json.dumps(data, ensure_ascii=False).replace(
        '"locale": "zh-CN",', '"locale": "zh-CN", "locale": "en-US",', 1
    )

    with pytest.raises(ValueError, match="duplicate"):
        parse_localization_response(raw, payload)


def test_factual_causality_and_advice_mutations_are_rejected() -> None:
    brief, _ = _compose()
    payload = build_localization_payload(brief)
    data = _translated_payload(brief)
    data["primary_stories"][0]["headline"] = (
        "SEC Charges Founder in 2027 alleged $160 million scheme"
    )
    data["primary_stories"][0]["why_it_matters"] = "建议买入，因为该消息推动市场上涨。"

    with pytest.raises(ValueError, match="factual|advice|causal"):
        parse_localization_response(json.dumps(data, ensure_ascii=False), payload, brief)


def _brief_with_headline(headline: str):
    brief, _ = _compose()
    first = brief.primary_stories[0].model_copy(update={"headline": headline})
    return brief.model_copy(update={"primary_stories": (first, *brief.primary_stories[1:])})


def test_natural_chinese_date_and_usd_units_preserve_canonical_facts() -> None:
    brief = _brief_with_headline("June 2026: $16 million; +0.86%.")
    data = _translated_payload(brief)
    data["primary_stories"][0]["headline"] = "2026年6月：1600万美元；上涨0.86%。"

    parsed = parse_localization_response(
        json.dumps(data, ensure_ascii=False), build_localization_payload(brief), brief
    )

    assert parsed.primary_stories[0].headline == "2026年6月：1600万美元；上涨0.86%。"


@pytest.mark.parametrize(
    "translated",
    [
        "2027年6月：1600万美元；上涨0.86%。",
        "2026年7月：1600万美元；上涨0.86%。",
        "2026年6月：1.6亿美元；上涨0.86%。",
        "2026年6月：1600万美元；上涨1.86%。",
        "2026年6月：1600万美元；下跌-0.86%。",
        "2026年6月：1600万美元；上涨0.86%，另有3亿美元。",
    ],
)
def test_natural_chinese_fact_mutations_are_rejected(translated: str) -> None:
    brief = _brief_with_headline("June 2026: $16 million; +0.86%.")
    data = _translated_payload(brief)
    data["primary_stories"][0]["headline"] = translated

    with pytest.raises(ValueError, match="factual|date|currency|percentage|numeric"):
        parse_localization_response(
            json.dumps(data, ensure_ascii=False), build_localization_payload(brief), brief
        )


def test_iso_date_can_be_localized_without_changing_day() -> None:
    brief = _brief_with_headline("Release dated 2026-06-12")
    data = _translated_payload(brief)
    data["primary_stories"][0]["headline"] = "发布日期为2026年6月12日"

    parse_localization_response(
        json.dumps(data, ensure_ascii=False), build_localization_payload(brief), brief
    )
    data["primary_stories"][0]["headline"] = "发布日期为2026年6月13日"
    with pytest.raises(ValueError, match="date|factual"):
        parse_localization_response(
            json.dumps(data, ensure_ascii=False), build_localization_payload(brief), brief
        )


def test_abbreviated_english_month_date_can_be_localized() -> None:
    brief = _brief_with_headline("Release dated Sep. 12, 2026")
    data = _translated_payload(brief)
    data["primary_stories"][0]["headline"] = "发布日期为2026年9月12日"

    parse_localization_response(
        json.dumps(data, ensure_ascii=False), build_localization_payload(brief), brief
    )


def test_currency_and_explicit_ticker_survive_natural_chinese_localization() -> None:
    brief = _brief_with_headline("€16 million; $NVDA")
    data = _translated_payload(brief)
    data["primary_stories"][0]["headline"] = "1600万欧元；$NVDA"

    parse_localization_response(
        json.dumps(data, ensure_ascii=False), build_localization_payload(brief), brief
    )


@pytest.mark.parametrize(
    "translated",
    ["1600万美元；$NVDA", "1600万英镑；$NVDA", "1600万欧元；$AMD"],
)
def test_currency_or_explicit_ticker_mutation_is_rejected(translated: str) -> None:
    brief = _brief_with_headline("€16 million; $NVDA")
    data = _translated_payload(brief)
    data["primary_stories"][0]["headline"] = translated

    with pytest.raises(ValueError, match="currency|symbol|numeric|factual"):
        parse_localization_response(
            json.dumps(data, ensure_ascii=False), build_localization_payload(brief), brief
        )


def test_c8_uncertainty_cannot_be_dropped_from_localized_reaction() -> None:
    base, _ = _compose()
    brief, _ = _compose(market_impacts=(_record(event_id=base.primary_stories[0].event_id),))
    reaction = brief.market_reactions[0].model_copy(
        update={"uncertainty": "Attribution remains uncertain because other Stories are plausible."}
    )
    brief = brief.model_copy(update={"market_reactions": (reaction,)})
    data = _translated_payload(brief)
    data["market_reactions"][0]["uncertainty"] = None

    with pytest.raises(ValueError, match="uncertainty"):
        parse_localization_response(
            json.dumps(data, ensure_ascii=False), build_localization_payload(brief), brief
        )


def test_localization_success_uses_one_request_and_records_provider_metadata() -> None:
    brief, _ = _compose()
    provider = FakeLLMProvider(
        response=LLMResponse(
            content=json.dumps(_translated_payload(brief), ensure_ascii=False),
            provider="fake",
            requested_model="model-requested",
            returned_model="model-returned",
            latency_seconds=0.4,
        )
    )

    result = localize_briefing(
        brief,
        provider,
        enabled=True,
        requested_model="model-requested",
    )

    assert result.content is not None
    assert result.diagnostics.status == "success"
    assert result.diagnostics.request_count == 1
    assert result.diagnostics.returned_model == "model-returned"
    assert len(provider.requests) == 1


def test_deepseek_alias_routing_is_accepted_without_rewriting_model_names() -> None:
    brief, _ = _compose()
    response = LLMResponse(
        content=json.dumps(_translated_payload(brief), ensure_ascii=False),
        provider="deepseek",
        requested_model="deepseek-v4-flash",
        returned_model="deepseek-flash",
        latency_seconds=0.2,
    )

    result = localize_briefing(
        brief,
        FakeLLMProvider(response=response),
        enabled=True,
        requested_model="deepseek-v4-flash",
    )

    assert result.content is not None
    assert result.diagnostics.status == "success"
    assert result.diagnostics.requested_model == "deepseek-v4-flash"
    assert result.diagnostics.returned_model == "deepseek-flash"


def test_provider_model_mismatch_fails_closed_without_persistable_content() -> None:
    brief, _ = _compose()
    provider = FakeLLMProvider(
        response=LLMResponse(
            content=json.dumps(_translated_payload(brief), ensure_ascii=False),
            provider="fake",
            requested_model="different-model",
            latency_seconds=0,
        )
    )

    result = localize_briefing(
        brief,
        provider,
        enabled=True,
        requested_model="model-requested",
    )

    assert result.content is None
    assert result.diagnostics.status == "failed"
    assert result.diagnostics.failure_category == "identity"
    assert len(provider.requests) == 1


def test_disabled_localization_makes_no_model_call() -> None:
    brief, _ = _compose()
    provider = FakeLLMProvider()

    result = localize_briefing(
        brief,
        provider,
        enabled=False,
        requested_model="model-requested",
    )

    assert result.content is None
    assert result.diagnostics.status == "disabled"
    assert result.diagnostics.request_count == 0
    assert provider.requests == []


@pytest.mark.parametrize(
    "response_text",
    ["not json", "```json\n{}\n```"],
)
def test_invalid_localization_response_fails_without_a_repair_request(response_text: str) -> None:
    brief, _ = _compose()
    provider = FakeLLMProvider(
        response=LLMResponse(
            content=response_text,
            provider="fake",
            requested_model="model-requested",
            latency_seconds=0,
        )
    )

    result = localize_briefing(
        brief,
        provider,
        enabled=True,
        requested_model="model-requested",
    )

    assert result.content is None
    assert result.diagnostics.status == "failed"
    assert result.diagnostics.request_count == 1
    assert len(provider.requests) == 1
