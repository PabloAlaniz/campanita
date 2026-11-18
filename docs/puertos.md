# Puertos

El núcleo de campanita decide; no guarda, no envía y no dibuja. Todo eso entra por tres puertos.
Están declarados en [`python/src/campanita/ports.py`](../python/src/campanita/ports.py) y en
[`ts/src/ports.ts`](../ts/src/ports.ts).

| Puerto | Qué resuelve | Estado |
|---|---|---|
| `NotificationStore` | Guardar avisos y leer la bandeja | Especificado, con secuencias de conformidad |
| `PreferenceStore` | Guardar lo que cambió cada persona | Borrador |
| `DeliveryChannel` | Entregar un aviso fuera de la bandeja | Borrador |

La **bandeja** no es un puerto: es lo que se lee del `NotificationStore`. Cualquier superficie
que la muestre (un panel web, una app, un bot) es un cliente de esa lectura.

## El almacén de avisos

Su comportamiento está en la sección "La bandeja" del [spec](../spec/SPEC.md) y en las
secuencias de [`spec/vectors/bandeja/`](../spec/vectors/bandeja). Un adaptador es correcto
cuando las reproduce todas.

Adaptadores incluidos:

| Adaptador | Python | TypeScript |
|---|---|---|
| En memoria | `campanita.stores.MemoryStore` | `MemoryStore` |
| SQLite | `campanita.stores.SqliteStore` | todavía no |

### Escribir un adaptador

Cada paquete trae el corredor de secuencias que usa para sus propios adaptadores:

```python
import json
from pathlib import Path
from campanita.conformance import run_store_vector

for path in sorted(Path("spec/vectors/bandeja").glob("*.json")):
    await run_store_vector(json.loads(path.read_text()), lambda: MiAlmacen(...))
```

```ts
import { readdirSync, readFileSync } from 'node:fs';
import { runStoreVector } from 'campanita';

for (const file of readdirSync('spec/vectors/bandeja')) {
  const vector = JSON.parse(readFileSync(`spec/vectors/bandeja/${file}`, 'utf8'));
  await runStoreVector(vector, () => new MiAlmacen(/* … */));
}
```

Cada secuencia necesita un almacén vacío. Si un paso no coincide, el corredor dice cuál, qué
esperaba y qué obtuvo.

Lo que las secuencias **no** pueden probar, y cada adaptador tiene que garantizar por su cuenta:

- **Que revisar la clave y guardar sea atómico.** Dos pedidos simultáneos con la misma
  `dedupe_key` dejan un solo aviso. En SQL, eso es un índice único sobre `(recipient_id,
  dedupe_key)`.
- **Que el texto se compare byte a byte.** El desempate por `id` y la comparación de
  `recipient_id` no pueden depender de mayúsculas, acentos ni de la configuración regional de la
  base.
- **Que las fechas no pierdan los milisegundos.**

## Decisiones que valen para los tres puertos

- **Toda lectura pide el destinatario.** Es el único alcance de seguridad, y por eso es un
  parámetro obligatorio y no algo que quien llama se tiene que acordar de filtrar.
- **La fecha de creación la pone quien guarda.** Si la pusiera quien emite, un reloj corrido
  desordenaría la bandeja.
- **Las preferencias guardan sólo los cambios.** Sin filas, valen los valores de la política.
  Agregar una categoría o un canal no obliga a migrar datos.
- **Un canal de entrega nunca lanza.** Devuelve `sent`, `expired` o `failed`. Un destino roto no
  puede impedir avisarle al resto, y `expired` le dice al adaptador que olvide ese destino.
- **Cada canal resuelve sus destinos.** El núcleo le pasa el aviso; a qué dirección o a qué
  dispositivos va es asunto del adaptador.

## La regla de los dos adaptadores

Un puerto no se da por estable hasta tener **dos adaptadores reales**. Con uno solo, el puerto
termina teniendo la forma de ese adaptador y el segundo no entra.

El almacén de avisos la cumple en Python (en memoria y SQLite). Los otros dos puertos siguen en
borrador hasta tener los suyos.
