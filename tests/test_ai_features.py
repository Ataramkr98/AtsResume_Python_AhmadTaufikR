import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.applications.models import Application
from apps.audit.models import AuditLog
from apps.candidates.matching import calculate_match
from apps.common import ai_gateway as gateway


@pytest.fixture
def ai_settings(settings):
    settings.AI_NARRATIVE_SCORES = True
    settings.AI_BASE_URL = "https://api.groq.com/openai/v1"
    settings.AI_API_KEY = "unit-test-secret"
    settings.AI_MODEL_DEFAULT = "openai/gpt-oss-20b"
    settings.AI_MODEL_CHAT = ""
    settings.AI_MODEL_ANALYZE = ""
    settings.AI_MAX_RETRIES = 1
    gateway._CACHE.clear()
    yield settings
    gateway._CACHE.clear()


def provider_response(content, finish="stop"):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({
        "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": finish}]
    }).encode()
    return response


def test_groq_request_is_cached_and_invalidated_by_profile_or_model(ai_settings):
    with patch.object(gateway.urllib.request, "urlopen", return_value=provider_response({"summary": "Python matches."})) as send:
        messages = [{"role": "user", "content": "JSON: Python"}]
        assert gateway.chat_json(messages)["summary"] == "Python matches."
        assert gateway.chat_json(messages)["summary"] == "Python matches."
        assert send.call_count == 1
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        assert payload["max_completion_tokens"] == 1200
        assert payload["reasoning_effort"] == "low"
        assert payload["include_reasoning"] is False
        assert request.get_header("User-agent") == "PortfolioAI/1.0"
        gateway.chat_json([{"role": "user", "content": "JSON: Docker"}])
        ai_settings.AI_MODEL_DEFAULT = "openai/gpt-oss-120b"
        gateway.chat_json(messages)
        assert send.call_count == 3


def test_transient_provider_error_retries_once(ai_settings):
    error = urllib.error.HTTPError("https://provider.invalid", 503, "unavailable", {}, io.BytesIO())
    with patch.object(gateway.urllib.request, "urlopen", side_effect=[error, provider_response({"summary": "Recovered."})]) as send, patch.object(gateway.time, "sleep"):
        assert gateway.chat_json([])["summary"] == "Recovered."
        assert send.call_count == 2


def test_auth_failure_is_safe_and_backs_off(ai_settings):
    error = urllib.error.HTTPError("https://provider.invalid", 401, "unit-test-secret", {}, io.BytesIO(b"unit-test-secret"))
    with patch.object(gateway.urllib.request, "urlopen", side_effect=error) as send:
        for _ in range(2):
            with pytest.raises(gateway.AiGatewayError) as caught:
                gateway.chat_json([])
            assert "unit-test-secret" not in str(caught.value)
        assert send.call_count == 1


@pytest.mark.parametrize("content,finish", [([], "stop"), (None, "stop"), ({}, "stop"), ({"summary": "cut off"}, "length")])
def test_invalid_provider_results_are_rejected(ai_settings, content, finish):
    with patch.object(gateway.urllib.request, "urlopen", return_value=provider_response(content, finish)):
        with pytest.raises(gateway.AiGatewayError):
            gateway.chat_json([])


@pytest.mark.parametrize("content", [{"summary": []}, {"summary": " "}, {"other": "unexpected"}])
def test_missing_narrative_text_is_rejected_before_success_caching(ai_settings, content):
    with patch.object(gateway.urllib.request, "urlopen", return_value=provider_response(content)):
        with pytest.raises(gateway.AiGatewayError):
            gateway.chat_json([], required_key="summary")


@pytest.mark.django_db
def test_summary_uses_actual_overlap_without_changing_score(demo, ai_settings):
    application = Application.objects.filter(organization=demo).select_related("job", "candidate").first()
    ai_settings.AI_NARRATIVE_SCORES = False
    expected, components, explanation = calculate_match(application)
    ai_settings.AI_NARRATIVE_SCORES = True
    with patch("apps.candidates.matching.chat_json", return_value={"summary": "Review the listed requirements."}) as chat:
        actual, actual_components, result = calculate_match(application)
    assert actual == expected
    assert actual_components == components
    data = json.loads(chat.call_args.args[0][1]["content"])
    assert data["matched_skills"] == explanation["matched"]
    assert data["missing_skills"] == explanation["missing"]
    assert "email" not in data and "phone" not in data
    assert result["ai_status"] == "available"


@pytest.mark.django_db
def test_refresh_persists_and_displays_escaped_summary(client, demo, ai_settings):
    application = Application.objects.filter(organization=demo).first()
    path = reverse("application-match-refresh", kwargs={"organization_slug": demo.slug, "application_id": application.id})
    assert client.get(path).status_code == 405
    with patch("apps.candidates.matching.chat_json", return_value={"summary": "Python overlap <script>unsafe</script>"}):
        response = client.post(path, follow=True)
    assert response.status_code == 200
    assert b"data-ai-summary" in response.content
    assert b"Python overlap &lt;script&gt;unsafe&lt;/script&gt;" in response.content
    assert b"Python overlap <script>" not in response.content
    application.refresh_from_db()
    assert application.match_score.explanation["ai_status"] == "available"
    assert AuditLog.objects.filter(action="application.match_refreshed", resource_id=str(application.id)).exists()


@pytest.mark.django_db
def test_refresh_keeps_score_and_shows_fallback_on_provider_failure(client, demo, ai_settings):
    application = Application.objects.filter(organization=demo).first()
    path = reverse("application-match-refresh", kwargs={"organization_slug": demo.slug, "application_id": application.id})
    with patch("apps.candidates.matching.chat_json", side_effect=gateway.AiGatewayError("Unavailable")):
        response = client.post(path, follow=True)
    assert response.status_code == 200
    assert b"AI summary is temporarily unavailable" in response.content
    application.refresh_from_db()
    assert application.match_score.total_score is not None


@pytest.mark.django_db
def test_read_only_role_cannot_trigger_ai(client, demo, ai_settings):
    interviewer = OrganizationMembership.objects.filter(organization=demo, role=OrganizationMembership.Role.INTERVIEWER).first()
    application = Application.objects.filter(organization=demo).first()
    client.force_login(interviewer.user)
    path = reverse("application-match-refresh", kwargs={"organization_slug": demo.slug, "application_id": application.id})
    with patch("apps.candidates.matching.chat_json") as chat:
        assert client.post(path).status_code == 404
        chat.assert_not_called()
