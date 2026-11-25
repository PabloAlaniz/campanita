"""Transporte de email por SMTP, con la biblioteca estándar."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import TYPE_CHECKING, Literal

from ..ports import DeliveryResult

if TYPE_CHECKING:
    from .email import EmailMessage

Security = Literal["starttls", "tls", "none"]
_DEFAULT_PORTS: dict[Security, int] = {"starttls": 587, "tls": 465, "none": 25}


class SmtpTransport:
    """Manda por un servidor SMTP.

    `security`:
      - `starttls` (por omisión): conecta en claro y pasa a TLS antes de autenticarse;
      - `tls`: TLS desde el primer byte;
      - `none`: sin cifrar. Sólo para un servidor local o de pruebas.

    El certificado del servidor se verifica salvo que se pase otro `ssl_context`.
    Cada envío abre una conexión y corre en un hilo aparte, así que no bloquea.
    """

    def __init__(  # noqa: PLR0913 — dependencias inyectadas, solo por nombre
        self,
        host: str,
        *,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        security: Security = "starttls",
        timeout: float = 10.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if security not in _DEFAULT_PORTS:
            raise ValueError("`security` tiene que ser `starttls`, `tls` o `none`")
        if (username is None) != (password is None):
            raise ValueError("`username` y `password` van juntos")
        if username is not None and security == "none":
            raise ValueError("no se manda una contraseña por una conexión sin cifrar")
        self._host = host
        self._port = _DEFAULT_PORTS[security] if port is None else port
        self._username = username
        self._password = password
        self._security: Security = security
        self._timeout = timeout
        self._ssl_context = ssl_context

    async def send(self, message: EmailMessage) -> DeliveryResult:
        return await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> DeliveryResult:
        try:
            mime = _to_mime(message)
            with self._connect() as server:
                if self._username is not None and self._password is not None:
                    server.login(self._username, self._password)
                server.send_message(mime)
        except smtplib.SMTPResponseException as error:
            # El texto del servidor puede ser largo; nunca incluye la contraseña.
            reply = error.smtp_error
            text = reply.decode("utf-8", "replace") if isinstance(reply, bytes) else str(reply)
            return DeliveryResult("failed", error=f"SMTP {error.smtp_code}: {text[:200]}")
        except smtplib.SMTPRecipientsRefused as error:
            code = next(iter(error.recipients.values()))[0] if error.recipients else 0
            return DeliveryResult("failed", error=f"SMTP {code}: el servidor rechazó al destinatario")
        except (smtplib.SMTPException, OSError, ValueError) as error:
            # Sólo el tipo: el mensaje de estos errores no lo controlamos.
            return DeliveryResult("failed", error=f"SMTP: {type(error).__name__}")
        return DeliveryResult("sent", provider_message_id=mime["Message-ID"])

    def _connect(self) -> smtplib.SMTP:
        context = self._ssl_context or ssl.create_default_context()
        if self._security == "tls":
            return smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=context)
        server = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        if self._security == "starttls":
            try:
                server.starttls(context=context)
            except BaseException:
                server.close()
                raise
        return server


def _to_mime(message: EmailMessage) -> MimeMessage:
    # `email.message` codifica lo que no es ASCII y rechaza cabeceras con saltos de línea.
    mime = MimeMessage()
    mime["From"] = formataddr((message.sender_name, message.sender))
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime["Date"] = formatdate(localtime=False)
    mime["Message-ID"] = make_msgid(domain=message.sender.partition("@")[2])
    mime.set_content(message.text)
    if message.html is not None:
        mime.add_alternative(message.html, subtype="html")
    return mime
