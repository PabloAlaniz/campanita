"""campanita: notificaciones con las mismas reglas en cualquier stack.

El núcleo decide por qué canales sale un aviso y qué puede cambiar el usuario.
No conoce la base, el canal ni la superficie que muestra la bandeja: eso son
puertos (`campanita.ports`).
"""

from ._catalog import Catalog, Draft, TypeSpec, build_notification, parse_catalog
from ._channels import effective_matrix, resolve_channels, validate_patch
from ._inbox import AddResult, Cursor, FeedPage, FeedQuery, Notification, StoreError, StoreReason
from ._notify import InvalidChannels, Notifier, NotifyResult
from ._policy import Policy, parse_policy
from ._quiet import InvalidQuietHours, QuietHours, is_quiet, local_minute, parse_quiet_hours
from ._templates import render_template
from ._types import (
    INBOX,
    Cell,
    Change,
    EventReason,
    EventRejected,
    InvalidCatalog,
    InvalidPolicy,
    InvalidTemplate,
    MatrixCell,
    Overrides,
    PatchRejected,
    RejectionReason,
    TemplateError,
    TemplateReason,
)

#: La versión del spec que implementa este paquete.
SPEC_VERSION = "0"

__all__ = [
    "INBOX",
    "SPEC_VERSION",
    "AddResult",
    "Catalog",
    "Cell",
    "Change",
    "Cursor",
    "Draft",
    "EventReason",
    "EventRejected",
    "FeedPage",
    "FeedQuery",
    "InvalidCatalog",
    "InvalidChannels",
    "InvalidPolicy",
    "InvalidQuietHours",
    "InvalidTemplate",
    "MatrixCell",
    "Notification",
    "Notifier",
    "NotifyResult",
    "Overrides",
    "PatchRejected",
    "Policy",
    "QuietHours",
    "RejectionReason",
    "StoreError",
    "StoreReason",
    "TemplateError",
    "TemplateReason",
    "TypeSpec",
    "build_notification",
    "effective_matrix",
    "is_quiet",
    "local_minute",
    "parse_catalog",
    "parse_policy",
    "parse_quiet_hours",
    "render_template",
    "resolve_channels",
    "validate_patch",
]
