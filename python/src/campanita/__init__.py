"""campanita: notificaciones con las mismas reglas en cualquier stack.

El núcleo decide por qué canales sale un aviso y qué puede cambiar el usuario.
No conoce la base, el canal ni la superficie que muestra la bandeja: eso son
puertos (`campanita.ports`).
"""

from ._catalog import Catalog, Draft, TypeSpec, build_notification, parse_catalog
from ._channels import effective_matrix, resolve_channels, validate_patch
from ._policy import Policy, parse_policy
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
    "Catalog",
    "Cell",
    "Change",
    "Draft",
    "EventReason",
    "EventRejected",
    "InvalidCatalog",
    "InvalidPolicy",
    "InvalidTemplate",
    "MatrixCell",
    "Overrides",
    "PatchRejected",
    "Policy",
    "RejectionReason",
    "TemplateError",
    "TemplateReason",
    "TypeSpec",
    "build_notification",
    "effective_matrix",
    "parse_catalog",
    "parse_policy",
    "render_template",
    "resolve_channels",
    "validate_patch",
]
