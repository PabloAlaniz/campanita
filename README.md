# campanita

Notificaciones con las mismas reglas en cualquier stack.

> **Borrador.** Hoy existe el núcleo en Python y TypeScript: decide por qué canales sale un
> aviso, qué puede cambiar la persona, cómo se arma un aviso a partir de un evento y cómo se
> guarda y se lee la bandeja. Todavía no hay adaptadores de canal. La
> [hoja de ruta](docs/hoja-de-ruta.md) dice qué viene.

Casi todo producto termina con una campanita: una bandeja de avisos, una pantalla de preferencias
y uno o dos canales para avisar afuera. Y casi siempre se escribe de nuevo, atado a una base y a
un proveedor. `campanita` separa las reglas de todo lo demás:

- **Una política.** Qué sale por dónde, y qué puede apagar la persona, es un dato y no código
  repartido.
- **Cualquier base, cualquier canal, cualquier bandeja.** El núcleo no los conoce: son puertos, y
  cada uno se resuelve con un adaptador.
- **El mismo comportamiento en cada lenguaje.** Hay una [especificación](spec/SPEC.md) y vectores
  de conformidad compartidos; el paquete de Python y el de TypeScript corren los mismos casos.

## Las dos reglas que no se negocian

1. **La bandeja siempre recibe.** `inapp` no es un canal opcional: la notificación guardada *es*
   la entrega in-app. Una política que no lo cumple se rechaza al cargarla.
2. **Lo bloqueado no se apaga.** Si la política bloquea un canal para una categoría (el email de
   los avisos de seguridad), ningún cambio del usuario lo saca, ni siquiera uno guardado de antes.

## Cómo se usa

Una política, con la forma del spec:

```json
{
  "security": {
    "inapp": { "default": true, "locked": true },
    "email": { "default": true, "locked": true }
  },
  "activity": {
    "inapp": { "default": true, "locked": true },
    "email": { "default": false },
    "push": { "default": false }
  }
}
```

En Python:

```python
from campanita import parse_policy, resolve_channels, validate_patch

policy = parse_policy(raw)
disponibles = {"email"}                      # lo que esta instalación puede entregar
overrides = {"activity": {"email": True}}    # lo que cambió la persona

resolve_channels(policy, "activity", overrides, disponibles)   # frozenset({'email'})
validate_patch(policy, {"security": {"email": False}}, disponibles)
# PatchRejected: cambio de preferencias rechazado en security.email: la celda está bloqueada
```

En TypeScript:

```ts
import { parsePolicy, resolveChannels, validatePatch } from 'campanita';

const policy = parsePolicy(raw);
resolveChannels(policy, 'activity', { activity: { email: true } }, ['email']); // ['email']
validatePatch(policy, { security: { email: false } }, ['email']);
// PatchRejectedError: cambio de preferencias rechazado en security.email: la celda está bloqueada
```

Quien emite un aviso manda un tipo y sus datos. La categoría y el texto salen del catálogo:

```python
from campanita import parse_catalog, build_notification

catalog = parse_catalog({
    "comments.new": {"category": "activity", "title": "{author} comentó en {document}"},
}, policy)

build_notification(catalog, {
    "type": "comments.new",
    "recipient_id": "user-7",
    "payload": {"author": "Ana", "document": "Presupuesto"},
    "dedupe_key": "comment-381",
})
# Draft(recipient_id='user-7', type='comments.new', category='activity',
#       title='Ana comentó en Presupuesto', body=None, ...)
```

El aviso se guarda en un almacén, que puede ser cualquier base. La bandeja se lee de ahí:

```python
from datetime import UTC, datetime
from uuid import uuid4
from campanita import FeedQuery
from campanita.stores import MemoryStore

store = MemoryStore()
await store.add(draft, id=str(uuid4()), created_at=datetime.now(UTC))

page = await store.list_feed(FeedQuery("user-7", limit=20))
await store.count_unread("user-7", cap=100)
await store.mark_read(page.items[0].id, "user-7", datetime.now(UTC))
```

Más detalle en [`python/README.md`](python/README.md) y [`ts/README.md`](ts/README.md).

## Qué hay en el repo

| Carpeta | Qué es |
|---|---|
| [`spec/`](spec/SPEC.md) | La especificación y los vectores de conformidad |
| [`python/`](python/README.md) | El núcleo en Python, sin dependencias |
| [`ts/`](ts/README.md) | El núcleo en TypeScript, sin dependencias |
| [`tools/`](tools/gen_vectors.py) | El generador de vectores, una implementación independiente |
| [`docs/`](docs/principios.md) | Los principios, los puertos y la hoja de ruta |

## Qué no es

No es una plataforma de notificaciones con flujos, plantillas visuales y decenas de proveedores.
Si buscás eso, [Novu](https://github.com/novuhq/novu) y
[Notifo](https://github.com/notifo-io/notifo) son proyectos más grandes y más completos.
`campanita` apunta a lo contrario: un núcleo chico que se lee entero y se embebe en lo que ya
tenés.

## Desarrollo

Hace falta Node 20+, pnpm, Python 3.11+ y [uv](https://docs.astral.sh/uv/). Ver
[CONTRIBUTING.md](CONTRIBUTING.md).

```bash
pnpm install && pnpm test          # TypeScript
cd python && uv run pytest         # Python
pnpm check:spec                    # los vectores coinciden con el generador
```

## Licencia

[MIT](LICENSE).
