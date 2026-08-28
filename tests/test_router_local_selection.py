from argus.llm.groq_client import GroqClient
from argus.llm.ollama_client import OllamaClient
from argus.llm.router import ModelRouter


def test_uses_groq_as_local_when_configured(monkeypatch):
    monkeypatch.setattr("argus.llm.router.settings.groq_api_key", "fake-key")
    router = ModelRouter()
    assert isinstance(router.local, GroqClient)


def test_offline_fallback_is_always_ollama_even_when_local_is_groq(monkeypatch):
    monkeypatch.setattr("argus.llm.router.settings.groq_api_key", "fake-key")
    router = ModelRouter()
    assert isinstance(router.offline_fallback, OllamaClient)


def test_offline_fallback_reuses_local_instance_when_local_is_already_ollama(monkeypatch):
    monkeypatch.setattr("argus.llm.router.settings.groq_api_key", "")
    router = ModelRouter()
    assert isinstance(router.local, OllamaClient)
    assert router.offline_fallback is router.local


def test_groq_client_reports_unavailable_without_api_key(monkeypatch):
    monkeypatch.setattr("argus.llm.groq_client.settings.groq_api_key", "")
    client = GroqClient()
    assert client.is_available() is False


def test_groq_client_reports_available_with_api_key(monkeypatch):
    monkeypatch.setattr("argus.llm.groq_client.settings.groq_api_key", "fake-key")
    client = GroqClient()
    assert client.is_available() is True
