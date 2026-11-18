# campanita

Notificaciones con las mismas reglas en cualquier stack.

> **Borrador.** Hoy existe el spec v0 y el núcleo en Python y TypeScript. Todavía no hay
> adaptadores de base ni de canal, y nada está publicado en npm ni en PyPI. La
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

Más detalle en [`python/README.md`](python/README.md) y [`ts/README.md`](ts/README.md).

## Qué hay en el repo

| Carpeta | Qué es |
|---|---|
| [`spec/`](spec/SPEC.md) | La especificación y los vectores de conformidad |
| [`python/`](python/README.md) | El núcleo en Python, sin dependencias |
| [`ts/`](ts/README.md) | El núcleo en TypeScript, sin dependencias |
| [`tools/`](tools/gen_vectors.py) | El generador de vectores, una implementación independiente |
| [`docs/`](docs/puertos.md) | Los puertos y la hoja de ruta |

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
