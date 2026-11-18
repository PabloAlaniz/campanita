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

Los puertos (`campanita.ports`) son un borrador: fijan la forma que van a tener la base, los
canales de entrega y las preferencias, pero el spec todavía no los cubre.

## Desarrollo

```bash
uv sync
uv run pytest
uv run mypy
uv run ruff check .
```

Los tests corren los vectores de `../spec/vectors/`, los mismos que corre el paquete de
TypeScript.
