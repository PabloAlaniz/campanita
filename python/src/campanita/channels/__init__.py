"""Canales de entrega incluidos.

Un canal nombra lo que adapta (email, SMTP, un proveedor) y vive acá, fuera del
núcleo. Todos cumplen `campanita.ports.DeliveryChannel`.
"""

from .email import (
    AddressResolver,
    EmailChannel,
    EmailMessage,
    EmailTransport,
    RenderedEmail,
    is_valid_address,
    render_email,
)
from .http import HttpEmailTransport, ResendTransport, default_body
from .smtp import SmtpTransport

__all__ = [
    "AddressResolver",
    "EmailChannel",
    "EmailMessage",
    "EmailTransport",
    "HttpEmailTransport",
    "RenderedEmail",
    "ResendTransport",
    "SmtpTransport",
    "default_body",
    "is_valid_address",
    "render_email",
]
