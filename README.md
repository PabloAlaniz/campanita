# campanita

Notificaciones con las mismas reglas en cualquier stack.

[![CI](https://github.com/PabloAlaniz/campanita/actions/workflows/ci.yml/badge.svg)](https://github.com/PabloAlaniz/campanita/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/campanita)](https://www.npmjs.com/package/campanita)
[![PyPI](https://img.shields.io/pypi/v/campanita)](https://pypi.org/project/campanita/)
[![Licencia: MIT](https://img.shields.io/badge/licencia-MIT-yellow.svg)](LICENSE)

```bash
pip install campanita
npm install campanita
```

> **Versión temprana.** El núcleo decide por qué canales sale un aviso, qué puede cambiar la
> persona y cómo se arma un aviso, y guarda la bandeja y las preferencias sobre cualquier base.
> Los canales de entrega incluidos son email (SMTP, HTTP o Resend), Slack y push (a dispositivos
> de Apple y a navegadores); los demás los escribe quien integra. La bandeja se sirve por HTTP, con un cliente sin dependencias para la interfaz.
> Hasta la 1.0 la API puede cambiar entre versiones menores. La [hoja de ruta](docs/hoja-de-ruta.md) dice qué viene y el
> [CHANGELOG](CHANGELOG.md), qué cambió.

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

Dos datos describen una instalación. La **política** dice qué sale por dónde y qué se puede
apagar; el **catálogo**, qué tipos de aviso hay y cómo se redactan:

```python
from campanita import Notifier, parse_catalog, parse_policy
from campanita.stores import MemoryPreferenceStore, MemoryStore

policy = parse_policy({
    "security": {
        "inapp": {"default": True, "locked": True},
        "email": {"default": True, "locked": True},
    },
    "activity": {
        "inapp": {"default": True, "locked": True},
        "email": {"default": False},
    },
})
catalog = parse_catalog({
    "comments.new": {"category": "activity", "title": "{author} comentó en {document}"},
}, policy)

notifier = Notifier(
    policy=policy,
    catalog=catalog,
    store=MemoryStore(),                    # cualquier base
    preferences=MemoryPreferenceStore(),    # cualquier base
    channels=[email],                       # cualquier canal
)
```

Un canal incluido es email, con el transporte que elijas:

```python
from campanita.channels import EmailChannel, SmtpTransport

email = EmailChannel(
    transport=SmtpTransport("smtp.example.com", username="avisos", password="…"),
    sender="avisos@example.com",
    resolve_address=buscar_direccion,       # tu función: de un destinatario a su dirección
)
```

Otro es Slack: cada persona conecta un canal de su espacio con OAuth, sin ver ni pegar un token, y
los avisos le llegan ahí.

```python
from campanita.channels import SlackChannel

slack = SlackChannel(resolve_webhook=buscar_webhook)   # tu función: None si no conectó
```

Y otro es push: el aviso llega a los dispositivos de cada persona, cada uno por el servicio que
le corresponde. Qué dispositivos tiene una persona lo sabe tu aplicación; campanita no los guarda.

```ts
import { ApnsTransport, PushChannel, WebPushTransport } from 'campanita';

const push = new PushChannel({
  transports: {
    apns: new ApnsTransport({ key, keyId, teamId, topic: 'com.example.app' }),
    web: new WebPushTransport({ publicKey, privateKey, subject: 'mailto:avisos@example.com' }),
  },
  resolveDevices: buscarDispositivos,     // tu función: del aviso a sus dispositivos
  onResult: anotarResultado,              // tu función: con `expired`, olvidar el dispositivo
});
```

Un aviso puede hablarle a una persona o a un **grupo** (un equipo, un proyecto). Uno de grupo
sale una sola vez, al destino del grupo, y a cada miembro le queda una copia en la bandeja: nadie
recibe el mismo mensaje repetido en un canal compartido.

Avisar es mandar un tipo y sus datos. La categoría, el texto y los canales los decide campanita:

```python
result = await notifier.notify({
    "type": "comments.new",
    "recipient_id": "user-7",
    "payload": {"author": "Ana", "document": "Presupuesto"},
    "dedupe_key": "comment-381",            # repetir el evento no duplica el aviso
})
result.notification.title     # 'Ana comentó en Presupuesto'
result.deliveries             # qué pasó en cada canal
```

La bandeja se lee del almacén, y las preferencias se cambian pasando por la política:

```python
from campanita import FeedQuery, validate_patch

page = await store.list_feed(FeedQuery("user-7", limit=20))
await store.count_unread("user-7", cap=100)

changes = validate_patch(policy, {"security": {"email": False}}, available=["email"])
# PatchRejected: cambio de preferencias rechazado en security.email: la celda está bloqueada
```

Cada persona puede además definir un horario de **no molestar**: en ese rango los canales no la
interrumpen (salvo lo que la política marca como `urgent`) y el aviso la espera en la bandeja.

En TypeScript es lo mismo, con los nombres del lenguaje:

```ts
import { MemoryPreferenceStore, MemoryStore, Notifier, parseCatalog, parsePolicy } from 'campanita';

const notifier = new Notifier({
  policy,
  catalog,
  store: new MemoryStore(),
  preferences: new MemoryPreferenceStore(),
  channels: [],
});
const result = await notifier.notify({
  type: 'comments.new',
  recipient_id: 'user-7',
  payload: { author: 'Ana', document: 'Presupuesto' },
});
```

### La campanita en tu app

La bandeja también se sirve por HTTP, con un contrato que está en el spec: el mismo para cualquier
backend. Del lado del servidor hay un adaptador (`campanita/http` en TypeScript, `InboxApp` ASGI en
Python); del lado de la interfaz, un cliente con su store y dos hooks de React. La campana la
dibujás vos, con tu diseño:

```ts
// el servidor
const inbox = createInboxHandler({ store, basePath: '/api/avisos', resolveRecipient });
// la interfaz
const store = createInboxStore(createInboxClient({ baseUrl: '/api/avisos' }), { scope: 'acme' });
const { items, unread } = useInbox(store);   // unread.capped → "99+"
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
