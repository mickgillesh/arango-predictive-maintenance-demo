"""ARANGO_TLS_VERIFY parsing shared by python-arango, the AI Suite SDK and nb_helpers."""

import ssl
from pathlib import Path

import pytest

from backend.db import tls_verify


@pytest.mark.parametrize("raw", [None, "", "true", "TRUE", "1", "yes"])
def test_verify_defaults_on(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
    if raw is None:
        monkeypatch.delenv("ARANGO_TLS_VERIFY", raising=False)
    else:
        monkeypatch.setenv("ARANGO_TLS_VERIFY", raw)
    assert tls_verify() is True


@pytest.mark.parametrize("raw", ["false", "False", "0", "no", " false "])
def test_verify_off(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("ARANGO_TLS_VERIFY", raw)
    assert tls_verify() is False


def test_verify_ca_bundle_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("placeholder")
    monkeypatch.setenv("ARANGO_TLS_VERIFY", str(ca))
    assert tls_verify() == str(ca)


def test_verify_rejects_missing_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARANGO_TLS_VERIFY", "/no/such/ca.pem")
    with pytest.raises(ValueError, match="ARANGO_TLS_VERIFY"):
        tls_verify()


def test_nb_helpers_maps_ca_path_to_ssl_context(monkeypatch: pytest.MonkeyPatch) -> None:
    import certifi

    from notebooks.nb_helpers import _httpx_verify

    monkeypatch.setenv("ARANGO_TLS_VERIFY", certifi.where())
    assert isinstance(_httpx_verify(), ssl.SSLContext)
    monkeypatch.setenv("ARANGO_TLS_VERIFY", "false")
    assert _httpx_verify() is False
