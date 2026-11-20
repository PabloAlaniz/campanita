"""La bandeja por HTTP: el contrato de la sección "La bandeja por HTTP" del spec.

`InboxApp` es una aplicación ASGI 3, sin dependencias, que se monta bajo un prefijo:

    app.mount("/api/avisos", InboxApp(store=store, resolve_recipient=quien_pide))

Los ayudantes del cable (`encode_cursor`, `to_iso`, `item_to_wire`…) dan la misma
forma que cualquier otro adaptador del contrato, en cualquier lenguaje.
"""

from ._app import InboxApp
from ._wire import (
    MAX_CURSOR_MS,
    decode_cursor,
    decode_cursor_parts,
    encode_cursor,
    from_iso,
    item_to_wire,
    to_iso,
)

__all__ = [
    "MAX_CURSOR_MS",
    "InboxApp",
    "decode_cursor",
    "decode_cursor_parts",
    "encode_cursor",
    "from_iso",
    "item_to_wire",
    "to_iso",
]
