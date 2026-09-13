import html

from radar.admin import AudienceAdminHandler, authorization_header


def test_admin_auth_uses_operator_basic_token_and_constant_safe_comparison() -> None:
    token = "test-admin-token"
    assert AudienceAdminHandler.is_authorized(authorization_header("operator", token), token)
    assert not AudienceAdminHandler.is_authorized(authorization_header("operator", "wrong"), token)
    assert not AudienceAdminHandler.is_authorized(authorization_header("other", token), token)
    assert not AudienceAdminHandler.is_authorized("not-basic", token)


def test_admin_html_escapes_untrusted_values() -> None:
    assert html.escape("<script>alert(1)</script>") in AudienceAdminHandler.render_value(
        "<script>alert(1)</script>"
    )
