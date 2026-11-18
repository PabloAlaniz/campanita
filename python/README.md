# campanita (Python)

El núcleo de [campanita](https://github.com/PabloAlaniz/campanita) en Python: decide por qué
canales sale un aviso, qué puede cambiar el usuario y cómo se arma un aviso. Sin dependencias.

> **Borrador.** Implementa la versión 0 del spec, que puede cambiar. Todavía no hay adaptadores
> de base ni de canal.

```python
from campanita import parse_policy, resolve_channels, effective_matrix, validate_patch

policy = parse_policy(
    {
        "security": {
            "inapp": {"default": True, "locked": True},
            "email": {"default": True, "locked": True},
        },
        "activity": {
            "inapp": {"default": True, "locked": True},
            "email": {"default": False},
            "push": {"default": False},
        },
    }
)

disponibles = {"email"}  # lo que esta instalación puede entregar
overrides = {"activity": {"email": True}}  # lo que cambió la persona

resolve_channels(policy, "activity", overrides, disponibles)  # frozenset({'email'})
resolve_channels(policy, "security", {"security": {"email": False}}, disponibles)
# frozenset({'email'}): seguridad está bloqueada, el override no la apaga

effective_matrix(policy, overrides, disponibles)["activity"]["push"]
# MatrixCell(enabled=False, locked=True): push todavía no está disponible

validate_patch(policy, {"activity": {"email": False}}, disponibles)
# (Change(category='activity', channel='email', enabled=False),)
validate_patch(policy, {"security": {"email": False}}, disponibles)
# PatchRejected: cambio de preferencias rechazado en security.email: la celda está bloqueada
```

Para armar un aviso a partir de un evento:

```python
from campanita import parse_catalog, build_notification, EventRejected

catalog = parse_catalog(
    {
        "comments.new": {
            "category": "activity",
            "title": "{author} comentó en {document}",
            "body": "{excerpt}",
        },
    },
    policy,
)

draft = build_notification(
    catalog,
    {
        "type": "comments.new",
        "recipient_id": "user-7",
        "payload": {"author": "Ana", "document": "Presupuesto", "excerpt": "¿Va con IVA?"},
        "dedupe_key": "comment-381",
    },
)
draft.title  # 'Ana comentó en Presupuesto'
draft.category  # 'activity'

build_notification(catalog, {"type": "comments.new", "recipient_id": "user-7", "payload": {}})
# EventRejected: evento rechazado: falta una variable (author)
```

Las plantillas sólo reemplazan `{variables}` por texto o enteros. No hay formatos ni expresiones:
lo que hacen es igual en todos los lenguajes.

## La bandeja

Un almacén guarda los avisos y contesta la bandeja. Hay dos incluidos, y los dos pasan las mismas
secuencias del spec:

```python
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4
from campanita import FeedQuery
from campanita.stores import MemoryStore, SqliteStore

store = SqliteStore(sqlite3.connect("avisos.db", isolation_level=None))
store.create_schema()  # o MemoryStore(), sin base

added = await store.add(draft, id=str(uuid4()), created_at=datetime.now(UTC))
added.created  # False si esa persona ya tenía un aviso con la misma dedupe_key

page = await store.list_feed(FeedQuery("user-7", limit=20))
if page.next_cursor:
    older = await store.list_feed(FeedQuery("user-7", limit=20, cursor=page.next_cursor))

await store.count_unread("user-7", cap=100)
await store.mark_read(page.items[0].id, "user-7", datetime.now(UTC))
await store.mark_all_read("user-7", before=page.items[0].created_at, read_at=datetime.now(UTC))
```

`SqliteStore` usa `sqlite3` de la biblioteca estándar y bloquea mientras corre cada consulta:
alcanza para aplicaciones chicas, herramientas y tests.

Para otra base, escribí un adaptador que cumpla `campanita.ports.NotificationStore` y probalo con
`campanita.conformance.run_store_vector`: ver [`docs/puertos.md`](../docs/puertos.md).

## Las preferencias guardadas

Lo que cambió cada persona va a un almacén de preferencias. El recorrido completo de una pantalla
de preferencias es validar, guardar y volver a leer:

```python
from campanita import effective_matrix, validate_patch
from campanita.stores import MemoryPreferenceStore  # o SqlitePreferenceStore(conexión)

preferences = MemoryPreferenceStore()

changes = validate_patch(policy, {"activity": {"email": True}}, disponibles)  # rechaza lo bloqueado
await preferences.save_changes("user-7", changes)

overrides = await preferences.get_overrides("user-7")
effective_matrix(policy, overrides, disponibles)  # lo que muestra la pantalla
resolve_channels(policy, "activity", overrides, disponibles)  # por dónde sale el próximo aviso
```

El puerto de canales de entrega (`campanita.ports.DeliveryChannel`) es un borrador: el spec
todavía no lo cubre.

## Desarrollo

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check .
```

Los tests corren los vectores de `../spec/vectors/`, los mismos que corre el paquete de
TypeScript.
