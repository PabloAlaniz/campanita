"""Canales de entrega incluidos.

Un canal nombra lo que adapta (email, SMTP, un proveedor) y vive acá, fuera del
núcleo. Todos cumplen `campanita.ports.DeliveryChannel`.
"""

from .email import (
    AddressResolver,
    EmailChannel,
    EmailMessage,
    EmailTransport,
    NotificationAddressResolver,
    RenderedEmail,
    is_valid_address,
    render_email,
)
from .http import HttpEmailTransport, ResendTransport, default_body
from .slack import (
    HttpPost,
    NotificationWebhookResolver,
    SlackChannel,
    SlackConnection,
    SlackOAuthError,
    WebhookResolver,
    escape_slack_text,
    exchange_slack_code,
    http_post,
    is_slack_webhook_url,
    parse_slack_oauth_response,
    render_slack,
    slack_authorize_url,
)
from .smtp import SmtpTransport

__all__ = [
    "AddressResolver",
    "EmailChannel",
    "EmailMessage",
    "EmailTransport",
    "HttpEmailTransport",
    "HttpPost",
    "NotificationAddressResolver",
    "NotificationWebhookResolver",
    "RenderedEmail",
    "ResendTransport",
    "SlackChannel",
    "SlackConnection",
    "SlackOAuthError",
    "SmtpTransport",
    "WebhookResolver",
    "default_body",
    "escape_slack_text",
    "exchange_slack_code",
    "http_post",
    "is_slack_webhook_url",
    "is_valid_address",
    "parse_slack_oauth_response",
    "render_email",
    "render_slack",
    "slack_authorize_url",
]
