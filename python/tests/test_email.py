"""El canal de email y sus transportes. Ningún test sale a la red."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import shutil
import ssl
import subprocess
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage as MimeMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from campanita import Notification, Notifier, parse_catalog, parse_policy
from campanita._inbox import from_ms
from campanita.channels import (
    EmailChannel,
    EmailMessage,
    HttpEmailTransport,
    RenderedEmail,
    ResendTransport,
    SmtpTransport,
    is_valid_address,
    render_email,
)
from campanita.ports import DeliveryResult
from campanita.stores import MemoryPreferenceStore, MemoryStore

VECTORS = Path(__file__).resolve().parents[2] / "spec" / "vectors" / "email"


def load(name: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads((VECTORS / name).read_text(encoding="utf-8"))["cases"]
    return cases


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


# --- lo que está en el spec ---------------------------------------------------


@pytest.mark.parametrize("case", load("mensaje.json"), ids=lambda c: c["note"])
def test_mensaje_por_omision(case: dict[str, Any]) -> None:
    wire = dict(case["notification"])
    created, read = wire.pop("created_at_ms"), wire.pop("read_at_ms")
    rendered = render_email(Notification(**wire, created_at=from_ms(created), read_at=read))
    assert {"subject": rendered.subject, "text": rendered.text} == case["expect"]
    assert rendered.html is None


@pytest.mark.parametrize("case", load("direcciones.json"), ids=lambda c: c["note"])
def test_direcciones(case: dict[str, Any]) -> None:
    assert is_valid_address(case["address"]) is case["valid"]


# --- el canal -----------------------------------------------------------------


class Recorder:
    """Un transporte que anota lo que le piden mandar."""

    def __init__(self, result: DeliveryResult | Exception | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self._result = result or DeliveryResult("sent", provider_message_id="m-1")

    async def send(self, message: EmailMessage) -> DeliveryResult:
        self.sent.append(message)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def channel(transport: Any, addresses: dict[str, str | None] | None = None, **options: Any) -> EmailChannel:
    book = {"ana": "ana@example.com"} if addresses is None else addresses
    return EmailChannel(transport=transport, sender="avisos@example.com", resolve_address=book.get, **options)


def test_el_canal_busca_la_direccion_arma_el_mensaje_y_lo_manda() -> None:
    transport = Recorder()
    result = asyncio.run(channel(transport, sender_name="Avisos").send(notification()))
    assert result == DeliveryResult("sent", provider_message_id="m-1")
    assert transport.sent == [
        EmailMessage(
            to="ana@example.com",
            sender="avisos@example.com",
            sender_name="Avisos",
            subject="Ana comentó",
            text="¿Va con IVA?\n\nhttps://example.com/doc/7",
            idempotency_key="n1",
        )
    ]
    assert transport.sent[0].formatted_sender == "Avisos <avisos@example.com>"


def test_el_canal_se_llama_email_salvo_que_se_le_ponga_otro_nombre() -> None:
    assert channel(Recorder()).id == "email"
    assert channel(Recorder(), id="email-facturacion").id == "email-facturacion"


def test_quien_no_tiene_direccion_se_omite() -> None:
    transport = Recorder()
    result = asyncio.run(channel(transport, {"ana": None}).send(notification()))
    assert (result.outcome, transport.sent) == ("skipped", [])
    assert asyncio.run(channel(transport, {}).send(notification())).outcome == "skipped"


@pytest.mark.parametrize(
    "address", ["ana@example.com\nBcc: x@example.com", "Ana <ana@example.com>", "sin-arroba", ""]
)
def test_una_direccion_invalida_se_omite_y_no_llega_al_transporte(address: str) -> None:
    transport = Recorder()
    result = asyncio.run(channel(transport, {"ana": address}).send(notification()))
    assert (result.outcome, transport.sent) == ("skipped", [])


def test_la_direccion_se_puede_buscar_con_una_funcion_async() -> None:
    async def lookup(recipient_id: str) -> str | None:
        await asyncio.sleep(0)
        return f"{recipient_id}@example.com"

    transport = Recorder()
    made = EmailChannel(transport=transport, sender="avisos@example.com", resolve_address=lookup)
    assert asyncio.run(made.send(notification())).outcome == "sent"
    assert transport.sent[0].to == "ana@example.com"


def test_si_buscar_la_direccion_falla_es_una_entrega_fallida() -> None:
    def lookup(recipient_id: str) -> str:
        raise ConnectionError("clave=secreta")

    made = EmailChannel(transport=Recorder(), sender="avisos@example.com", resolve_address=lookup)
    result = asyncio.run(made.send(notification()))
    assert result.outcome == "failed"
    assert "secreta" not in (result.error or "")


def test_un_transporte_que_lanza_es_una_entrega_fallida() -> None:
    result = asyncio.run(channel(Recorder(RuntimeError("sin conexión"))).send(notification()))
    assert result == DeliveryResult("failed", error="RuntimeError: sin conexión")


def test_un_render_propio_se_respeta_y_su_asunto_no_puede_partir_la_cabecera() -> None:
    def render(item: Notification) -> RenderedEmail:
        return RenderedEmail(
            subject=f"[{item.category}]\r\nBcc: x@example.com", text="texto", html="<b>hola</b>"
        )

    transport = Recorder()
    asyncio.run(channel(transport, render=render).send(notification()))
    assert transport.sent[0].subject == "[activity] Bcc: x@example.com"
    assert (transport.sent[0].text, transport.sent[0].html) == ("texto", "<b>hola</b>")


def test_el_remitente_se_valida_al_armar_el_canal() -> None:
    for sender in ("", "sin-arroba", "Avisos <avisos@example.com>", "a@b\nBcc: c@d"):
        with pytest.raises(ValueError, match="sender"):
            EmailChannel(transport=Recorder(), sender=sender, resolve_address=lambda _: None)
    for name in ("", "  ", "Avisos\nBcc: x", "Avisos <x>", 'Avi"sos'):
        with pytest.raises(ValueError, match="sender_name"):
            channel(Recorder(), sender_name=name)


# --- SMTP: un servidor falso dentro del test ------------------------------------


@dataclass
class FakeSmtp:
    """Lo mínimo de SMTP para recibir un mensaje, anotando lo que pasa."""

    credentials: tuple[str, str] | None = None
    reject_recipient: bool = False
    tls: ssl.SSLContext | None = None
    implicit_tls: bool = False
    hang: bool = False
    port: int = 0
    messages: list[MimeMessage] = field(default_factory=list)
    envelopes: list[tuple[str, str]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    authenticated: bool = False
    secure_when_authenticated: bool | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def say(line: str) -> None:
            writer.write(line.encode() + b"\r\n")
            await writer.drain()

        secure = self.implicit_tls
        sender = recipient = ""
        try:
            if self.hang:
                await asyncio.sleep(30)
            await say("220 falso ESMTP")
            while line := (await reader.readline()).decode().rstrip("\r\n"):
                command = line.split(" ", 1)[0].upper()
                self.log.append("AUTH …" if command == "AUTH" else line)
                if command in ("EHLO", "HELO"):
                    extras = ["AUTH PLAIN"] if self.credentials else []
                    if self.tls is not None and not secure:
                        extras.append("STARTTLS")
                    lines = ["falso", *extras]
                    for index, extra in enumerate(lines):
                        await say(f"250{'-' if index < len(lines) - 1 else ' '}{extra}")
                elif command == "STARTTLS" and self.tls is not None:
                    await say("220 adelante")
                    await writer.start_tls(self.tls)
                    secure = True
                elif command == "AUTH":
                    _, user, password = base64.b64decode(line.split(" ")[2]).decode().split("\x00")
                    self.secure_when_authenticated = secure
                    self.authenticated = (user, password) == self.credentials
                    await say("235 ok" if self.authenticated else "535 5.7.8 credenciales inválidas")
                elif command == "MAIL":
                    if self.credentials and not self.authenticated:
                        await say("530 5.7.0 hace falta autenticarse")
                        continue
                    sender = line.split(":", 1)[1].split(">")[0].lstrip("<")
                    await say("250 ok")
                elif command == "RCPT":
                    recipient = line.split(":", 1)[1].split(">")[0].lstrip("<")
                    await say("550 5.1.1 no existe esa casilla" if self.reject_recipient else "250 ok")
                elif command == "DATA":
                    await say("354 termine con un punto")
                    data = await reader.readuntil(b"\r\n.\r\n")
                    # El cliente duplica el punto de las líneas que empiezan con punto; el
                    # servidor lo saca (RFC 5321, 4.5.2).
                    data = (b"\r\n" + data).replace(b"\r\n..", b"\r\n.")[2:]
                    self.messages.append(message_from_bytes(data[:-5], policy=policy.default))  # type: ignore[arg-type]
                    self.envelopes.append((sender, recipient))
                    await say("250 recibido")
                elif command == "QUIT":
                    await say("221 chau")
                    break
                else:
                    await say("250 ok")
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            pass
        finally:
            writer.close()


@contextlib.asynccontextmanager
async def smtp_server(**options: Any) -> AsyncIterator[FakeSmtp]:
    fake = FakeSmtp(**options)
    server = await asyncio.start_server(
        fake.handle, "127.0.0.1", 0, ssl=fake.tls if fake.implicit_tls else None
    )
    fake.port = server.sockets[0].getsockname()[1]
    try:
        yield fake
    finally:
        server.close()


MESSAGE = EmailMessage(
    to="ana@example.com",
    sender="avisos@example.com",
    sender_name="Avisos Ñandú",
    subject="Ana comentó — ¿revisás? 🔔",
    text="Línea uno\n\nhttps://example.com/doc/7\n.\nuna línea que empieza con punto",
)


def test_smtp_manda_el_mensaje() -> None:
    async def run() -> None:
        async with smtp_server() as server:
            result = await SmtpTransport("127.0.0.1", port=server.port, security="none").send(MESSAGE)
            assert result.outcome == "sent"
            (received,) = server.messages
            assert server.envelopes == [("avisos@example.com", "ana@example.com")]
            assert received["To"] == "ana@example.com"
            assert received["Subject"] == "Ana comentó — ¿revisás? 🔔"
            assert received["From"] == "Avisos Ñandú <avisos@example.com>"
            assert received["Message-ID"] == result.provider_message_id
            assert received["Message-ID"].endswith("@example.com>")
            assert received["Date"]
            # En el cable los saltos son CRLF, y la línea que empieza con punto llega entera.
            assert received.get_content().replace("\r\n", "\n").rstrip("\n") == MESSAGE.text
            assert received.get_content_type() == "text/plain"

    asyncio.run(run())


def test_smtp_con_html_manda_las_dos_versiones() -> None:
    async def run() -> None:
        async with smtp_server() as server:
            message = EmailMessage(
                to="ana@example.com", sender="a@example.com", subject="s", text="plano", html="<b>rico</b>"
            )
            assert (
                await SmtpTransport("127.0.0.1", port=server.port, security="none").send(message)
            ).outcome == "sent"
            (received,) = server.messages
            assert received.get_content_type() == "multipart/alternative"
            assert [part.get_content_type() for part in received.iter_parts()] == ["text/plain", "text/html"]

    asyncio.run(run())


def test_smtp_destinatario_rechazado_es_una_entrega_fallida() -> None:
    async def run() -> None:
        async with smtp_server(reject_recipient=True) as server:
            result = await SmtpTransport("127.0.0.1", port=server.port, security="none").send(MESSAGE)
            assert result.outcome == "failed"
            assert result.error == "SMTP 550: el servidor rechazó al destinatario"
            assert server.messages == []

    asyncio.run(run())


def test_smtp_servidor_caido_es_una_entrega_fallida() -> None:
    async def run() -> None:
        async with smtp_server() as server:
            port = server.port
        result = await SmtpTransport("127.0.0.1", port=port, security="none", timeout=2).send(MESSAGE)
        assert result == DeliveryResult("failed", error="SMTP: ConnectionRefusedError")

    asyncio.run(run())


def test_smtp_tiempo_agotado_es_una_entrega_fallida() -> None:
    async def run() -> None:
        async with smtp_server(hang=True) as server:
            result = await SmtpTransport("127.0.0.1", port=server.port, security="none", timeout=0.3).send(
                MESSAGE
            )
            assert result.outcome == "failed"
            # Según la versión de Python, `smtplib` lo informa como tiempo agotado o como desconexión.
            assert result.error in ("SMTP: TimeoutError", "SMTP: SMTPServerDisconnected")

    asyncio.run(run())


def test_smtp_opciones_invalidas() -> None:
    with pytest.raises(ValueError, match="security"):
        SmtpTransport("localhost", security="ssl")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="van juntos"):
        SmtpTransport("localhost", username="u")
    with pytest.raises(ValueError, match="sin cifrar"):
        SmtpTransport("localhost", username="u", password="p", security="none")


@pytest.fixture(scope="module")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """Un certificado de prueba para 127.0.0.1: contexto del servidor y del cliente."""
    if shutil.which("openssl") is None:
        pytest.skip("hace falta openssl para probar TLS")
    folder = tmp_path_factory.mktemp("tls")
    key, cert = folder / "key.pem", folder / "cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(key), "-out", str(cert)],
        check=True,
        capture_output=True,
    )  # fmt: skip
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert, key)
    client = ssl.create_default_context(cafile=str(cert))
    return server, client


@pytest.mark.parametrize("security", ["starttls", "tls"])
def test_smtp_con_tls_se_autentica_recien_con_la_conexion_cifrada(
    security: str, certificate: tuple[ssl.SSLContext, ssl.SSLContext]
) -> None:
    server_context, client_context = certificate

    async def run() -> None:
        options = {
            "credentials": ("usuario", "clave-secreta"),
            "tls": server_context,
            "implicit_tls": security == "tls",
        }
        async with smtp_server(**options) as server:
            transport = SmtpTransport(
                "127.0.0.1", port=server.port, username="usuario", password="clave-secreta",
                security=security, ssl_context=client_context,  # type: ignore[arg-type]
            )  # fmt: skip
            result = await transport.send(MESSAGE)
            assert result.outcome == "sent", result.error
            assert server.secure_when_authenticated is True
            assert len(server.messages) == 1

    asyncio.run(run())


def test_smtp_no_confia_en_un_certificado_desconocido(
    certificate: tuple[ssl.SSLContext, ssl.SSLContext],
) -> None:
    server_context, _ = certificate

    async def run() -> None:
        async with smtp_server(tls=server_context, implicit_tls=True) as server:
            # Sin `ssl_context` se verifica contra las autoridades del sistema,
            # que no conocen este certificado.
            result = await SmtpTransport("127.0.0.1", port=server.port, security="tls", timeout=3).send(
                MESSAGE
            )
            assert result == DeliveryResult("failed", error="SMTP: SSLCertVerificationError")
            assert server.messages == []

    asyncio.run(run())


def test_smtp_si_el_servidor_no_ofrece_tls_no_manda_la_clave(
    certificate: tuple[ssl.SSLContext, ssl.SSLContext],
) -> None:
    async def run() -> None:
        async with smtp_server(credentials=("usuario", "clave-secreta")) as server:  # sin STARTTLS
            transport = SmtpTransport(
                "127.0.0.1", port=server.port, username="usuario", password="clave-secreta"
            )
            result = await transport.send(MESSAGE)
            assert result.outcome == "failed"
            assert server.secure_when_authenticated is None, "se intentó autenticar sin cifrar"
            assert not any(line.startswith("AUTH") for line in server.log)

    asyncio.run(run())


def test_smtp_la_clave_no_aparece_en_el_error(certificate: tuple[ssl.SSLContext, ssl.SSLContext]) -> None:
    server_context, client_context = certificate

    async def run() -> None:
        async with smtp_server(credentials=("usuario", "la-buena"), tls=server_context) as server:
            transport = SmtpTransport(
                "127.0.0.1", port=server.port, username="usuario", password="clave-equivocada",
                ssl_context=client_context,
            )  # fmt: skip
            result = await transport.send(MESSAGE)
            assert result.outcome == "failed"
            assert result.error is not None and result.error.startswith("SMTP 535")
            assert "clave-equivocada" not in result.error and "usuario" not in result.error
            assert server.messages == []

    asyncio.run(run())


# --- HTTP: un servidor local ------------------------------------------------------


@dataclass
class FakeHttp:
    status: int = 200
    body: bytes = b'{"id": "msg_123"}'
    location: str | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)
    url: str = ""


@contextlib.contextmanager
def http_server(**options: Any) -> Iterator[FakeHttp]:
    fake = FakeHttp(**options)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            fake.requests.append(
                {"path": self.path, "headers": dict(self.headers.items()), "body": self.rfile.read(length)}
            )
            self.send_response(fake.status)
            if fake.location:
                self.send_header("Location", fake.location)
            self.send_header("Content-Length", str(len(fake.body)))
            self.end_headers()
            self.wfile.write(fake.body)

        # Una redirección 302 seguida llega como GET: también se anota.
        do_GET = do_POST

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()


def test_http_manda_el_mensaje_como_json() -> None:
    with http_server() as server:
        transport = HttpEmailTransport(f"{server.url}/v1/send", headers={"X-Api-Key": "k-1"})
        result = asyncio.run(transport.send(MESSAGE))
    assert result == DeliveryResult("sent")
    (request,) = server.requests
    assert request["path"] == "/v1/send"
    assert request["headers"]["X-Api-Key"] == "k-1"
    assert request["headers"]["Content-Type"] == "application/json"
    assert json.loads(request["body"]) == {
        "from": "Avisos Ñandú <avisos@example.com>",
        "to": "ana@example.com",
        "subject": MESSAGE.subject,
        "text": MESSAGE.text,
    }


def test_http_el_cuerpo_las_cabeceras_y_el_identificador_se_pueden_adaptar() -> None:
    with http_server(body=b'{"data": {"message_id": "abc"}}') as server:
        transport = HttpEmailTransport(
            server.url,
            build_body=lambda m: {"destinatario": m.to, "asunto": m.subject, "contenido": {"texto": m.text}},
            build_headers=lambda m: {"X-Mensaje": m.idempotency_key or ""},
            read_id=lambda response: response["data"]["message_id"],
        )
        message = EmailMessage(
            to="ana@example.com", sender="a@example.com", subject="s", text="t", idempotency_key="n9"
        )
        result = asyncio.run(transport.send(message))
    assert result == DeliveryResult("sent", provider_message_id="abc")
    assert json.loads(server.requests[0]["body"]) == {
        "destinatario": "ana@example.com",
        "asunto": "s",
        "contenido": {"texto": "t"},
    }
    assert server.requests[0]["headers"]["X-Mensaje"] == "n9"


def test_http_con_html() -> None:
    with http_server() as server:
        message = EmailMessage(
            to="ana@example.com", sender="a@example.com", subject="s", text="t", html="<b>h</b>"
        )
        asyncio.run(HttpEmailTransport(server.url).send(message))
    assert json.loads(server.requests[0]["body"])["html"] == "<b>h</b>"


@pytest.mark.parametrize("status", [400, 401, 422, 429, 500, 503])
def test_http_una_respuesta_que_no_es_2xx_es_una_entrega_fallida(status: int) -> None:
    with http_server(status=status, body=b'{"error": "clave k-secreta invalida"}') as server:
        result = asyncio.run(HttpEmailTransport(server.url, headers={"X-Api-Key": "k-secreta"}).send(MESSAGE))
    assert result == DeliveryResult("failed", error=f"HTTP {status}")


def test_http_una_respuesta_que_no_es_json_igual_es_enviado() -> None:
    with http_server(body=b"ok") as server:
        result = asyncio.run(HttpEmailTransport(server.url, read_id=lambda r: r["id"]).send(MESSAGE))
    assert result == DeliveryResult("sent")


def _read_reply(response: Any) -> DeliveryResult:
    if response["ok"]:
        return DeliveryResult("sent", provider_message_id=response.get("id"))
    if response.get("code") == "blocked":
        return DeliveryResult("skipped", error="blocked")
    return DeliveryResult("failed", error=response.get("code"))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"ok": true, "id": "m1"}', DeliveryResult("sent", provider_message_id="m1")),
        (b'{"ok": false, "code": "limit"}', DeliveryResult("failed", error="limit")),
        (b'{"ok": false, "code": "blocked"}', DeliveryResult("skipped", error="blocked")),
        (b"ok", DeliveryResult("failed", error="respuesta ilegible")),
        (b"{}", DeliveryResult("failed", error="respuesta ilegible")),
    ],
)
def test_http_el_resultado_en_el_cuerpo_de_un_2xx(body: bytes, expected: DeliveryResult) -> None:
    with http_server(body=body) as server:
        transport = HttpEmailTransport(server.url, read_result=_read_reply, read_id=lambda r: "no-se-usa")
        assert asyncio.run(transport.send(MESSAGE)) == expected


@pytest.mark.parametrize("outcome", ["silenced", "expired", "otro"])
def test_http_el_resultado_del_cuerpo_solo_vale_sent_failed_o_skipped(outcome: str) -> None:
    with http_server(body=b"{}") as server:
        transport = HttpEmailTransport(server.url, read_result=lambda r: DeliveryResult(outcome))  # type: ignore[arg-type]
        assert asyncio.run(transport.send(MESSAGE)) == DeliveryResult("failed", error="respuesta ilegible")


def test_http_con_read_result_un_no_2xx_sigue_siendo_fallido() -> None:
    with http_server(status=500, body=b'{"ok": true}') as server:
        transport = HttpEmailTransport(server.url, read_result=_read_reply)
        assert asyncio.run(transport.send(MESSAGE)) == DeliveryResult("failed", error="HTTP 500")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_http_no_sigue_redirecciones_para_no_reenviar_las_credenciales(status: int) -> None:
    with http_server() as other, http_server(status=status, location="") as server:
        server.location = other.url + "/robado"
        transport = HttpEmailTransport(server.url, headers={"Authorization": "Bearer k"})
        result = asyncio.run(transport.send(MESSAGE))
        assert result == DeliveryResult("failed", error=f"HTTP {status}")
        assert other.requests == [], "el pedido, con sus credenciales, llegó a otro servidor"


def test_http_servidor_caido_es_una_entrega_fallida() -> None:
    with http_server() as server:
        url = server.url
    result = asyncio.run(HttpEmailTransport(url, timeout=2).send(MESSAGE))
    assert result.outcome == "failed"
    assert result.error == "HTTP: URLError"


def test_http_la_url_se_valida() -> None:
    for url in ("file:///etc/passwd", "ftp://example.com", "example.com/send", ""):
        with pytest.raises(ValueError, match="url"):
            HttpEmailTransport(url)


def test_resend_arma_el_pedido_de_su_api() -> None:
    with http_server() as server:
        transport = ResendTransport("re_clave", base_url=server.url)
        message = EmailMessage(
            to="ana@example.com", sender="avisos@example.com", sender_name="Avisos", subject="s", text="t",
            idempotency_key="n1",
        )  # fmt: skip
        result = asyncio.run(transport.send(message))
    assert result == DeliveryResult("sent", provider_message_id="msg_123")
    (request,) = server.requests
    assert request["path"] == "/emails"
    assert request["headers"]["Authorization"] == "Bearer re_clave"
    assert request["headers"]["Idempotency-Key"] == "n1"
    assert json.loads(request["body"]) == {
        "from": "Avisos <avisos@example.com>",
        "to": ["ana@example.com"],
        "subject": "s",
        "text": "t",
    }


def test_resend_la_clave_no_aparece_en_el_error() -> None:
    with http_server(status=403, body=b'{"message": "re_clave no sirve"}') as server:
        result = asyncio.run(ResendTransport("re_clave", base_url=server.url).send(MESSAGE))
    assert result == DeliveryResult("failed", error="HTTP 403")
    with pytest.raises(ValueError, match="api_key"):
        ResendTransport("")


# --- de punta a punta ---------------------------------------------------------------


def test_de_un_evento_a_un_mail_recibido() -> None:
    async def run() -> None:
        async with smtp_server() as server:
            policy_ = parse_policy(
                {
                    "security": {
                        "inapp": {"default": True, "locked": True},
                        "email": {"default": True, "locked": True},
                    },
                    "activity": {"inapp": {"default": True, "locked": True}, "email": {"default": False}},
                }
            )
            catalog = parse_catalog(
                {
                    "login.new": {"category": "security", "title": "Nuevo inicio de sesión desde {device}"},
                    "comment.new": {"category": "activity", "title": "{author} comentó"},
                },
                policy_,
            )
            addresses = {"ana": "ana@example.com"}
            store = MemoryStore()
            notifier = Notifier(
                policy=policy_,
                catalog=catalog,
                store=store,
                preferences=MemoryPreferenceStore(),
                channels=[
                    EmailChannel(
                        transport=SmtpTransport("127.0.0.1", port=server.port, security="none"),
                        sender="avisos@example.com",
                        resolve_address=addresses.get,
                    )
                ],
            )
            login = {
                "type": "login.new",
                "payload": {"device": "Firefox"},
                "action_url": "https://example.com/sesiones",
            }

            sent = await notifier.notify({**login, "recipient_id": "ana"})
            assert sent.deliveries["email"].outcome == "sent"
            (received,) = server.messages
            assert received["To"] == "ana@example.com"
            assert received["Subject"] == "Nuevo inicio de sesión desde Firefox"
            assert received.get_content().strip() == "https://example.com/sesiones"

            quiet = await notifier.notify(
                {"type": "comment.new", "recipient_id": "ana", "payload": {"author": "Beto"}}
            )
            assert dict(quiet.deliveries) == {}, "la actividad no sale por email salvo que la persona lo pida"

            nobody = await notifier.notify({**login, "recipient_id": "beto"})
            assert nobody.created and nobody.deliveries["email"].outcome == "skipped"
            assert len(server.messages) == 1
            assert await store.count_unread("beto") == 1, (
                "el aviso queda en la bandeja aunque no haya a dónde mandarlo"
            )

    asyncio.run(run())
