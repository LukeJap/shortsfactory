import semantic_edit


def test_semantic_ai_timeout_defaults_to_short_render_fallback(monkeypatch):
    monkeypatch.delenv(
        semantic_edit.SEMANTIC_AI_TIMEOUT_ENV,
        raising=False,
    )

    assert semantic_edit.semantic_ai_timeout_seconds() == 30.0


def test_semantic_ai_timeout_uses_environment_override(monkeypatch):
    monkeypatch.setenv(
        semantic_edit.SEMANTIC_AI_TIMEOUT_ENV,
        "7.5",
    )

    assert semantic_edit.semantic_ai_timeout_seconds() == 7.5


def test_semantic_ai_timeout_has_safe_minimum(monkeypatch):
    monkeypatch.setenv(
        semantic_edit.SEMANTIC_AI_TIMEOUT_ENV,
        "0",
    )

    assert semantic_edit.semantic_ai_timeout_seconds() == 1.0


def test_semantic_ai_preflight_uses_short_connectivity_timeout(monkeypatch):
    captured = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

    def fake_get(url, *, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(
        semantic_edit.requests,
        "get",
        fake_get,
    )

    assert semantic_edit.semantic_ai_preflight_warning() is None
    assert captured["url"].endswith("/api/tags")
    assert captured["timeout"] == 1.0


def test_call_ollama_uses_semantic_timeout(monkeypatch):
    captured = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": '{"proposed_cuts": []}'
            }

    def fake_post(
        url,
        *,
        json,
        timeout,
    ):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setenv(
        semantic_edit.SEMANTIC_AI_TIMEOUT_ENV,
        "6.25",
    )
    monkeypatch.setattr(
        semantic_edit.requests,
        "post",
        fake_post,
    )

    assert semantic_edit.call_ollama("hello") == {
        "proposed_cuts": []
    }
    assert captured["timeout"] == 6.25
    assert captured["json"]["prompt"] == "hello"
