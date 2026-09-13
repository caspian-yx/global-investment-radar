import json

from test_briefing_composition import _compose
from test_localization import _translated_payload

from radar.audience import DeliveryMode, RecipientProfile
from radar.localization import build_localization_payload, parse_localization_response
from radar.report import RecipientComposition, render_recipient_email


def _recipient(mode: DeliveryMode) -> RecipientProfile:
    from test_audience import _recipient as make_recipient

    return make_recipient(
        delivery_mode=mode, locale="en" if mode is DeliveryMode.ENGLISH else "zh-CN"
    )


def _localized(brief):
    payload = build_localization_payload(brief)
    return parse_localization_response(
        json.dumps(_translated_payload(brief), ensure_ascii=False), payload
    )


def test_english_chinese_and_bilingual_modes_have_truthful_primary_content() -> None:
    brief, _ = _compose()
    localized = _localized(brief)

    english_subject, english_html, english_locale = render_recipient_email(
        brief,
        localized,
        RecipientComposition(
            delivery_mode=DeliveryMode.ENGLISH, recipient=_recipient(DeliveryMode.ENGLISH)
        ),
    )
    chinese_subject, chinese_html, chinese_locale = render_recipient_email(
        brief,
        localized,
        RecipientComposition(
            delivery_mode=DeliveryMode.CHINESE, recipient=_recipient(DeliveryMode.CHINESE)
        ),
    )
    bilingual_subject, bilingual_html, bilingual_locale = render_recipient_email(
        brief,
        localized,
        RecipientComposition(
            delivery_mode=DeliveryMode.BILINGUAL, recipient=_recipient(DeliveryMode.BILINGUAL)
        ),
    )

    assert english_locale == "en"
    assert "全球投资雷达" not in english_html
    assert english_subject.endswith("2026-09-13")
    assert chinese_locale == "zh-CN"
    assert "全球投资雷达" in chinese_html
    assert "English reference" not in chinese_html
    assert "全球投资晨报" in chinese_subject
    assert bilingual_locale == "zh-CN"
    assert "全球投资雷达" in bilingual_html and "English reference" in bilingual_html
    assert bilingual_subject == chinese_subject


def test_notes_are_escaped_and_outside_canonical_briefing() -> None:
    brief, _ = _compose()
    composition = RecipientComposition(
        delivery_mode=DeliveryMode.ENGLISH,
        recipient=_recipient(DeliveryMode.ENGLISH),
        editorial_note="Global <priority>",
        personal_note='Alice & Bob "watch"',
    )
    _, html, _ = render_recipient_email(brief, None, composition)

    assert "Editor's Note" in html
    assert "Global &lt;priority&gt;" in html
    assert "Alice &amp; Bob &#34;watch&#34;" in html or "Alice &amp; Bob &#34;watch&#34;" in html
    assert "Global <priority>" not in html
    assert "editorial_note" not in brief.model_dump_json()


def test_localized_request_falls_back_to_english_without_false_metadata() -> None:
    brief, _ = _compose()
    _, html, locale = render_recipient_email(
        brief,
        None,
        RecipientComposition(
            delivery_mode=DeliveryMode.CHINESE, recipient=_recipient(DeliveryMode.CHINESE)
        ),
    )
    assert locale == "en"
    assert '<html lang="en">' in html
    assert "全球投资雷达" not in html
