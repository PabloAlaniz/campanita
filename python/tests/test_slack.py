"""El canal de Slack y su conexión. Ningún test sale a la red."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest

from campanita import Notification, Notifier, parse_catalog, parse_policy
from campanita.channels import (
    SlackChannel,
    SlackConnection,
    SlackOAuthError,
    escape_slack_text,
    exchange_slack_code,
    http_post,
    is_slack_webhook_url,
    parse_slack_oauth_response,
    render_slack,
    slack_authorize_url,
)
from campanita.stores import MemoryPreferenceStore, MemoryStore

CASES: dict[str, list[dict[str, Any]]] = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "slack.json").read_text(encoding="utf-8")
)
WEBHOOK = "https://hooks.slack.com/services/T0001/B0001/secreto123"


def notification(**changes: Any) -> Notification:
    base: dict[str, Any] = {
        "id": "n1",
        "recipient_id": "ana",
        "type": "nota",
        "category": "activity",
        "title": "Ana comentó",
        "body": "¿Va con IVA?",
        "action_url": "https://example.com/doc/7",
        "payload": {},
        "scope": None,
        "dedupe_key": None,
        "created_at": datetime(2030, 1, 1, tzinfo=UTC),
    }
    return Notification(**{**base, **changes})


class FakePost:
    """Un `POST` de mentira que anota lo que recibe."""

    def __init__(self, status: int = 200, body: bytes = b"ok", error: Exception | None = None) -> None:
        self.status, self.body, self.error = status, body, error
        self.calls: list[tuple[str, bytes, dict[str, str], float]] = []

    def __call__(
        self, url: str, data: bytes, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes]:
        self.calls.append((url, data, dict(headers), timeout))
        if self.error is not None:
            raise self.error
        return self.status, self.body


def send(channel: SlackChannel, item: Notification | None = None) -> Any:
    return asyncio.run(channel.send(item or notification()))


# --- los casos compartidos ------------------------------------------------------


@pytest.mark.parametrize("case", CASES["mensaje"], ids=lambda c: c["note"])
def test_mensaje_por_omision(case: dict[str, Any]) -> None:
    assert render_slack(notification(**case["notification"])) == {"text": case["text"]}


@pytest.mark.parametrize("case", CASES["webhooks"], ids=lambda c: c["note"])
def test_webhooks(case: dict[str, Any]) -> None:
    assert is_slack_webhook_url(case["url"]) is case["valid"]


@pytest.mark.parametrize("case", CASES["oauth"], ids=lambda c: c["note"])
def test_respuesta_de_la_conexion(case: dict[str, Any]) -> None:
    if "error" in case:
        with pytest.raises(SlackOAuthError) as caught:
            parse_slack_oauth_response(case["response"])
        assert caught.value.reason == case["error"]
    else:
        assert parse_slack_oauth_response(case["response"]) == SlackConnection(**case["connection"])


def test_escape() -> None:
    assert escape_slack_text("a & b < c > d &amp;") == "a &amp; b &lt; c &gt; d &amp;amp;"


# --- el canal -------------------------------------------------------------------


def test_envia_el_mensaje_al_webhook_de_la_persona() -> None:
    post = FakePost()
    asked: list[str] = []

    def resolve(recipient_id: str) -> str:
        asked.append(recipient_id)
        return WEBHOOK

    channel = SlackChannel(resolve_webhook=resolve, post=post, timeout=3.0)
    result = send(channel)
    assert channel.id == "slack"
    assert (result.outcome, result.error) == ("sent", None)
    assert asked == ["ana"]
    [(url, data, headers, timeout)] = post.calls
    assert url == WEBHOOK
    assert json.loads(data) == {"text": "*Ana comentó*\n¿Va con IVA?\n<https://example.com/doc/7>"}
    assert "¿" in data.decode("utf-8")
    assert headers == {"Content-Type": "application/json"}
    assert timeout == 3.0


def test_la_busqueda_puede_ser_asincronica() -> None:
    async def resolve(_: str) -> str:
        return WEBHOOK

    post = FakePost()
    assert send(SlackChannel(resolve_webhook=resolve, post=post)).outcome == "sent"
    assert len(post.calls) == 1


def test_sin_conexion_no_se_llama_a_slack() -> None:
    post = FakePost()
    result = send(SlackChannel(resolve_webhook=lambda _: None, post=post))
    assert result.outcome == "skipped"
    assert post.calls == []


@pytest.mark.parametrize(
    "url", ["https://example.com/services/T/B/a", "http://hooks.slack.com/services/T/B/a", "", 7]
)
def test_una_url_que_no_es_de_slack_no_se_usa(url: Any) -> None:
    post = FakePost()
    result = send(SlackChannel(resolve_webhook=lambda _: url, post=post))
    assert result.outcome == "skipped"
    assert post.calls == []


def test_si_la_busqueda_falla_es_failed() -> None:
    def resolve(_: str) -> str:
        raise RuntimeError(WEBHOOK)

    result = send(SlackChannel(resolve_webhook=resolve, post=FakePost()))
    assert result.outcome == "failed"
    assert result.error == "no se pudo buscar la conexión de Slack: RuntimeError"


@pytest.mark.parametrize(
    ("status", "body", "outcome", "error"),
    [
        (404, b"no_service", "expired", "Slack 404 no_service"),
        (410, b"channel_is_archived\n", "expired", "Slack 410 channel_is_archived"),
        (400, b"invalid_payload", "failed", "Slack 400 invalid_payload"),
        (403, b"action_prohibited", "failed", "Slack 403 action_prohibited"),
        (500, b"<html>Algo se rompi\xc3\xb3</html>", "failed", "Slack 500"),
        (429, b"", "failed", "Slack 429"),
        (302, b"", "failed", "Slack 302"),
        (404, b"\xff\xfe", "expired", "Slack 404"),
    ],
)
def test_respuestas_de_slack(status: int, body: bytes, outcome: str, error: str) -> None:
    result = send(SlackChannel(resolve_webhook=lambda _: WEBHOOK, post=FakePost(status, body)))
    assert (result.outcome, result.error) == (outcome, error)


def test_un_error_de_red_no_repite_la_url() -> None:
    post = FakePost(error=OSError(f"no se pudo conectar a {WEBHOOK}"))
    result = send(SlackChannel(resolve_webhook=lambda _: WEBHOOK, post=post))
    assert (result.outcome, result.error) == ("failed", "Slack: OSError")


def test_un_mensaje_propio() -> None:
    post = FakePost()
    channel = SlackChannel(
        resolve_webhook=lambda _: WEBHOOK,
        render=lambda item: {"text": item.title, "unfurl_links": False},
        post=post,
        id="slack-equipo",
    )
    assert send(channel).outcome == "sent"
    assert channel.id == "slack-equipo"
    assert json.loads(post.calls[0][1]) == {"text": "Ana comentó", "unfurl_links": False}


def test_si_el_mensaje_propio_falla_es_failed_y_no_se_envia() -> None:
    post = FakePost()

    def render(_: Notification) -> dict[str, Any]:
        raise KeyError("x")

    result = send(SlackChannel(resolve_webhook=lambda _: WEBHOOK, render=render, post=post))
    assert (result.outcome, result.error) == ("failed", "no se pudo armar el mensaje: KeyError")
    assert post.calls == []

    result = send(
        SlackChannel(resolve_webhook=lambda _: WEBHOOK, render=lambda _: {"text": object()}, post=post)
    )
    assert (result.outcome, result.error) == ("failed", "no se pudo armar el mensaje: TypeError")
    assert post.calls == []


def test_de_punta_a_punta_con_el_notifier() -> None:
    policy = parse_policy(
        {"activity": {"inapp": {"default": True, "locked": True}, "slack": {"default": True}}}
    )
    catalog = parse_catalog(
        {"nota": {"category": "activity", "title": "{author} comentó", "body": "{text}"}}, policy
    )
    post = FakePost()
    webhooks = {"ana": WEBHOOK}
    notifier = Notifier(
        policy=policy,
        catalog=catalog,
        store=MemoryStore(),
        preferences=MemoryPreferenceStore(),
        channels=[SlackChannel(resolve_webhook=webhooks.get, post=post)],
    )

    async def run() -> tuple[Any, Any]:
        first = await notifier.notify(
            {"type": "nota", "recipient_id": "ana", "payload": {"author": "Beto", "text": "<hola>"}}
        )
        second = await notifier.notify(
            {"type": "nota", "recipient_id": "beto", "payload": {"author": "Ana", "text": "x"}}
        )
        return first, second

    first, second = asyncio.run(run())
    assert first.deliveries["slack"].outcome == "sent"
    assert second.deliveries["slack"].outcome == "skipped"
    assert [json.loads(call[1]) for call in post.calls] == [{"text": "*Beto comentó*\n&lt;hola&gt;"}]


# --- el POST por omisión, contra un servidor local --------------------------------


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[str, dict[str, str], bytes]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).seen.append((self.path, dict(self.headers), body))
        if self.path == "/ok":
            self._answer(200, b"ok")
        elif self.path == "/gone":
            self._answer(404, b"no_service")
        else:
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _answer(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_post_por_omision(server: str) -> None:
    assert http_post(f"{server}/ok", b'{"text":"x"}', {"Content-Type": "application/json"}, 5.0) == (
        200,
        b"ok",
    )
    path, headers, body = _Handler.seen[0]
    assert (path, body, headers["Content-Type"]) == ("/ok", b'{"text":"x"}', "application/json")


def test_post_por_omision_devuelve_el_error_sin_lanzar(server: str) -> None:
    assert http_post(f"{server}/gone", b"{}", {}, 5.0) == (404, b"no_service")


def test_post_por_omision_no_sigue_redirecciones(server: str) -> None:
    status, _ = http_post(f"{server}/redirect", b"{}", {}, 5.0)
    assert status == 302
    assert [path for path, _, _ in _Handler.seen] == ["/redirect"]


# --- conectar -------------------------------------------------------------------


def test_url_de_autorizacion() -> None:
    url = slack_authorize_url(
        client_id="123.456", redirect_uri="https://app.example.com/slack/vuelta?x=1", state="a b&c"
    )
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "slack.com", "/oauth/v2/authorize")
    assert parse_qs(parts.query, strict_parsing=True) == {
        "client_id": ["123.456"],
        "scope": ["incoming-webhook"],
        "redirect_uri": ["https://app.example.com/slack/vuelta?x=1"],
        "state": ["a b&c"],
    }


@pytest.mark.parametrize("missing", ["client_id", "redirect_uri", "state"])
def test_url_de_autorizacion_pide_todo(missing: str) -> None:
    options = {"client_id": "1", "redirect_uri": "https://app.example.com/v", "state": "s"}
    with pytest.raises(ValueError, match=missing):
        slack_authorize_url(**{**options, missing: ""})


OAUTH = {
    "client_id": "123.456",
    "client_secret": "s3creto",
    "code": "c0digo",
    "redirect_uri": "https://app.example.com/v",
}
GRANTED = json.dumps(CASES["oauth"][0]["response"]).encode()


def test_cambia_el_codigo_por_la_conexion() -> None:
    post = FakePost(200, GRANTED)
    connection = asyncio.run(exchange_slack_code(**OAUTH, post=post, timeout=4.0))
    assert connection == SlackConnection(**CASES["oauth"][0]["connection"])
    [(url, data, headers, timeout)] = post.calls
    assert url == "https://slack.com/api/oauth.v2.access"
    assert parse_qs(data.decode("ascii"), strict_parsing=True) == {
        key: [value] for key, value in OAUTH.items()
    }
    assert headers == {"Content-Type": "application/x-www-form-urlencoded"}
    assert timeout == 4.0


@pytest.mark.parametrize(
    ("post", "reason"),
    [
        (FakePost(200, b'{"ok":false,"error":"invalid_code"}'), "invalid_code"),
        (FakePost(500, GRANTED), "http"),
        (FakePost(302, GRANTED), "http"),
        (FakePost(200, b"<html>"), "invalid_response"),
        (FakePost(error=OSError("s3creto c0digo")), "network"),
    ],
)
def test_la_conexion_falla(post: FakePost, reason: str) -> None:
    with pytest.raises(SlackOAuthError) as caught:
        asyncio.run(exchange_slack_code(**OAUTH, post=post))
    assert caught.value.reason == reason
    assert str(caught.value) == f"no se pudo conectar con Slack: {reason}"
    assert caught.value.__context__ is None or caught.value.__suppress_context__
    assert "s3creto" not in repr(caught.value) and "c0digo" not in repr(caught.value)


@pytest.mark.parametrize("missing", list(OAUTH))
def test_la_conexion_pide_todo(missing: str) -> None:
    post = FakePost(200, GRANTED)
    with pytest.raises(ValueError, match=missing):
        asyncio.run(exchange_slack_code(**{**OAUTH, missing: ""}, post=post))
    assert post.calls == []


def test_la_conexion_no_muestra_el_webhook() -> None:
    connection = parse_slack_oauth_response(CASES["oauth"][0]["response"])
    assert "hooks.slack.com" not in repr(connection)
    assert "hooks.slack.com" not in str(connection)
