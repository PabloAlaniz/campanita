"""campanita: notificaciones con las mismas reglas en cualquier stack.

El núcleo decide por qué canales sale un aviso y qué puede cambiar el usuario.
No conoce la base, el canal ni la superficie que muestra la bandeja: eso son
puertos (`campanita.ports`).
"""

from ._channels import effective_matrix, resolve_channels, validate_patch
from ._policy import Policy, parse_policy
from ._types import (
    INBOX,
    Cell,
    Change,
    InvalidPolicy,
    MatrixCell,
    Overrides,
    PatchRejected,
    RejectionReason,
)

#: La versión del spec que implementa este paquete.
SPEC_VERSION = "0"

__all__ = [
    "INBOX",
    "SPEC_VERSION",
    "Cell",
    "Change",
    "InvalidPolicy",
    "MatrixCell",
    "Overrides",
    "PatchRejected",
    "Policy",
    "RejectionReason",
    "effective_matrix",
    "parse_policy",
    "resolve_channels",
    "validate_patch",
]
