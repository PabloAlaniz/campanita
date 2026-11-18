# Especificación de campanita, versión 0 (borrador)

Todo port de campanita tiene que comportarse como se describe acá. Los archivos JSON de
`vectors/` son la prueba normativa: un port cumple cuando pasa todos los vectores. Los genera
`tools/gen_vectors.py`, una implementación independiente, para que ningún port se valide a sí
mismo.

La versión 0 es un borrador y puede cambiar sin aviso. Cubre dos cosas:

- **Por qué canales sale un aviso y qué puede cambiar el usuario**: la política, la resolución de
  canales, la matriz efectiva y el cambio de preferencias.
- **Cómo se arma un aviso**: las plantillas, el catálogo de tipos y el paso de un evento a un
  aviso listo para guardar.

Guardar el aviso, la bandeja y las entregas todavía no están especificados (ver
`docs/hoja-de-ruta.md`).

El spec no nombra categorías, canales ni tipos propios. Los que aparecen en los ejemplos y en los
vectores son datos de prueba.

## Nombres

El spec nombra las operaciones en `snake_case`. Cada port usa la convención de su lenguaje
(`resolve_channels` en Python, `resolveChannels` en TypeScript). Lo que no cambia entre ports son
los literales que viajan: `inapp`, las claves `default`, `locked` y `enabled`, y los motivos de
rechazo.

## Vocabulario

- Un **canal** es un medio por el que un aviso llega a una persona: `email`, `push`, `sms`.
- Una **categoría** agrupa avisos que comparten reglas: `security`, `activity`.
- Los dos son **identificadores**: texto que cumple `^[a-z][a-z0-9_-]{0,63}$`.
- **`inapp` está reservado.** Es la bandeja, no un canal de entrega. La notificación guardada
  *es* la entrega in-app; por eso `inapp` aparece en la política y en la matriz de preferencias,
  pero nunca en el resultado de la resolución de canales.

## Política

La política dice, para cada categoría y cada canal, qué pasa por defecto y si el usuario lo puede
cambiar. Es un dato de entrada: campanita no trae categorías ni canales propios.

```json
{
  "security": {
    "inapp": { "default": true, "locked": true },
    "email": { "default": true, "locked": true }
  },
  "activity": {
    "inapp": { "default": true, "locked": true },
    "email": { "default": false }
  }
}
```

Cada **celda** tiene:

- `default` (booleano, obligatorio): el valor cuando el usuario nunca tocó la celda.
- `locked` (booleano, opcional, por omisión `false`): el usuario no la puede cambiar.

Una política es **válida** cuando se cumple todo esto:

1. Es un objeto con al menos una categoría.
2. Cada clave de categoría y de canal es un identificador.
3. Cada categoría es un objeto de celdas, y cada celda es un objeto que sólo tiene `default` y,
   opcionalmente, `locked`, los dos booleanos.
4. Cada categoría incluye `inapp` con `default: true` y `locked: true`.

La regla 4 es la primera invariante del dominio: **la bandeja siempre recibe**. No es un canal
opcional, es donde la notificación vive.

La segunda invariante es una decisión de quien escribe la política y no del spec: marcar como
`locked` los canales de las categorías que no se pueden silenciar (típicamente seguridad). El
spec garantiza que una celda `locked` se respeta en todos lados.

Un port rechaza una política inválida al cargarla, antes de resolver nada.

## Overrides y canales disponibles

- Los **overrides** son las celdas que el usuario cambió, con la misma forma que la política pero
  con un booleano por celda: `{ "activity": { "email": true } }`. Se guardan sólo los cambios: un
  usuario que nunca tocó nada no tiene overrides y recibe los valores por defecto. Agregar una
  categoría o un canal a la política no requiere migrar datos.
- Un override sobre una categoría o un canal que la política no tiene se **ignora**. Es lo que
  queda cuando se saca algo de la política, y no es un error.
- Los **canales disponibles** son los que la instalación tiene configurados para entregar. Un
  canal puede estar en la política y no estar disponible todavía.

## Resolución de canales

`resolve_channels(política, categoría, overrides, disponibles)` devuelve el conjunto de canales
de entrega por los que sale un aviso de esa categoría.

```text
si la categoría no está en la política: devolver vacío

para cada (canal, celda) de la categoría:
    si canal == "inapp":            saltar
    si canal no está en disponibles: saltar
    habilitado = celda.default            si celda.locked
                 override, o celda.default si no hay override
    si habilitado: agregar canal

devolver el conjunto
```

- **Nunca devuelve `inapp`.**
- **Una celda `locked` ignora el override.** Un override viejo en contra no apaga un canal
  bloqueado.
- **Un canal no disponible se excluye**, aunque esté bloqueado en `true`. Encender un canal es
  configurar la instalación, no tocar esta lógica.
- El resultado es un conjunto. Cuando un port lo expone como secuencia, la ordena
  alfabéticamente. Los vectores lo guardan así.

## Matriz efectiva

`effective_matrix(política, overrides, disponibles)` devuelve lo que muestra una pantalla de
preferencias: para cada celda de la política, `enabled` y `locked`.

```text
enabled = celda.default             si celda.locked
          override, o celda.default si no

locked  = celda.locked
          o (canal != "inapp" y canal no está en disponibles)
```

- La interfaz no recalcula la política: la muestra. Por eso `locked` viaja en la respuesta.
- Un canal no disponible se informa como `locked` y conserva su `enabled`. Ofrecer un control que
  no hace nada es peor que decir que todavía no está.
- `inapp` no depende de los canales disponibles.

## Cambio de preferencias

`validate_patch(política, patch, disponibles)` recibe las celdas que el usuario quiere cambiar,
con la forma de los overrides, y devuelve la lista de cambios a guardar o rechaza el pedido.

```text
para cada categoría del pedido, en orden:
    si no está en la política: rechazar (unknown_category, sin canal)
    para cada (canal, valor) de esa categoría, en orden:
        si el canal no está en la categoría: rechazar (unknown_channel)
        si el valor no es un booleano:       rechazar (invalid_value)
        si la celda está bloqueada:          rechazar (locked)
        si el canal no está disponible:      rechazar (unavailable)
        anotar el cambio

devolver los cambios
```

| Motivo | Cuándo |
|---|---|
| `unknown_category` | la categoría no está en la política |
| `unknown_channel` | el canal no está en esa categoría |
| `invalid_value` | el valor no es un booleano |
| `locked` | la celda está bloqueada |
| `unavailable` | el canal no está disponible |

- **El orden es por punto de código Unicode**, primero de categoría y después de canal. Fija qué
  error se informa cuando hay más de uno. Ojo en los lenguajes que ordenan texto por unidades
  UTF-16: dan otro resultado fuera del plano básico.
- Una categoría desconocida se rechaza aunque no traiga celdas, y el error no lleva canal.
- `inapp` está siempre bloqueado, así que pedir cambiarlo da `locked`.
- La forma del pedido (un objeto de objetos) la valida quien lo recibe; el spec empieza cuando ya
  tiene esa forma.

- **Un pedido inválido se rechaza entero y no se guarda nada.** Una celda bloqueada no se ignora
  en silencio: si una interfaz la mandó, tiene un error y conviene que se entere.
- Un pedido vacío es válido y no produce cambios.
- Los cambios se devuelven en el mismo orden en que se revisan.
- Un cambio que deja la celda en su valor por defecto es válido y se guarda igual: el override
  explícito sobrevive a un cambio posterior del valor por defecto.

## Plantillas

Una plantilla es texto con variables. Es a propósito lo mínimo, para que lo que una plantilla
puede hacer sea igual en todos los lenguajes.

- `{nombre}` se reemplaza por el valor de `nombre` en el payload. El nombre cumple
  `^[a-z][a-z0-9_]{0,63}$`.
- `{{` y `}}` son una llave literal.
- Cualquier otra `{` o `}` hace inválida la plantilla. No hay formatos, expresiones ni acceso a
  propiedades.

`render_template(plantilla, payload)`:

1. **Revisa la plantilla entera antes de mirar el payload.** Una plantilla inválida falla con
   `invalid_template` aunque antes le falte una variable.
2. Reemplaza las variables en orden de aparición. La primera que no se puede resolver corta:

| Motivo | Cuándo |
|---|---|
| `missing_variable` | el payload no tiene esa clave como propia |
| `invalid_variable` | el valor no es texto ni un entero mostrable |

- **Texto** se inserta tal cual. No se vuelve a interpretar: un valor `{b}` queda `{b}`.
- **Un entero** entre `-(2^53 - 1)` y `2^53 - 1` se escribe en base 10. Un número sin parte
  fraccionaria cuenta como entero (`3.0` es `3`, `-0.0` es `0`), porque JSON no distingue.
- Todo lo demás es `invalid_variable`: decimales, enteros fuera de ese rango, booleanos, nulos,
  listas y objetos.
- Las claves del payload que la plantilla no usa se ignoran.
- "Como propia" importa en los lenguajes donde los objetos heredan propiedades: `{constructor}`
  falta en un payload vacío.

## Catálogo

El catálogo dice qué tipos de aviso existen. Quien emite manda un tipo y un payload, y no sabe de
categorías ni de textos: eso se decide en un solo lugar. Como la política, es un dato de entrada.

```json
{
  "invitations.received": {
    "category": "invitations",
    "title": "Te invitaron a {project}",
    "body": "{inviter} te invitó al proyecto {project}."
  }
}
```

Un catálogo es **válido** contra una política cuando:

1. Es un objeto. Puede estar vacío.
2. Cada clave es un **identificador de tipo**: segmentos separados por puntos, cada uno con la
   forma `[a-z][a-z0-9_-]*`, y hasta 128 caracteres en total.
3. Cada entrada es un objeto que sólo tiene `category`, `title` y, opcionalmente, `body`.
4. `category` es una categoría de la política.
5. `title` es una plantilla válida y no vacía. `body`, si está, es una plantilla válida.

Un port rechaza un catálogo inválido al cargarlo, antes de armar ningún aviso.

## Armado del aviso

`build_notification(catálogo, evento)` convierte un evento en un aviso listo para guardar, o lo
rechaza.

El **evento** es lo que manda quien emite:

| Campo | Forma |
|---|---|
| `type` | un tipo del catálogo |
| `recipient_id` | texto no vacío. Es opaco: campanita no lo interpreta |
| `payload` | un objeto; si falta, vale `{}` |
| `action_url` | texto no vacío, nulo o ausente |
| `scope` | texto no vacío, nulo o ausente |
| `dedupe_key` | texto no vacío, nulo o ausente |

Las revisiones van en este orden, y la primera que falla corta:

| Paso | Motivo | `detail` |
|---|---|---|
| 1. El tipo es texto y está en el catálogo | `unknown_type` | el tipo, o nulo si no es texto |
| 2. `recipient_id`, `payload`, `action_url`, `scope`, `dedupe_key`, en ese orden | `invalid_field` | el campo |
| 3. No hay campos desconocidos | `invalid_field` | el primero por punto de código |
| 4. Se arma el título | `missing_variable`, `invalid_variable` | la variable |
| 5. El título no queda vacío | `empty_title` | nulo |
| 6. Se arma el cuerpo, si el tipo lo tiene | `missing_variable`, `invalid_variable` | la variable |

- **Un campo desconocido se rechaza.** Un `dedup_key` mal escrito no puede perderse en silencio y
  dejar pasar duplicados.
- **Vacío** quiere decir sin ningún carácter que no sea espacio (U+0020), tabulación, salto de
  línea o retorno de carro. La lista es fija porque "espacio" no significa lo mismo en cada
  lenguaje.
- Un texto vacío en `action_url`, `scope` o `dedupe_key` se rechaza: "no hay" se dice con nulo o
  dejando el campo afuera, de una sola forma.

El **aviso armado** tiene `recipient_id`, `type`, `category` (la del catálogo), `title`, `body`
(nulo si el tipo no tiene cuerpo), `action_url`, `payload`, `scope` y `dedupe_key`, con los
opcionales ausentes en nulo.

- **No tiene identificador ni fecha.** Los pone quien guarda.
- **El payload se guarda entero**, también lo que las plantillas no usaron: permite volver a
  redactar el aviso más adelante sin pedirle el evento a quien lo emitió.
- **`action_url` no se interpreta.** Quien la muestre como enlace tiene que revisar su esquema:
  el spec no impide un `javascript:`.

## Vectores

Cada archivo de `vectors/` cumple `schemas/vector.schema.json` y tiene `spec`, `id`, `kind` y
`cases`.

| `kind` | Qué prueba | Cada caso |
|---|---|---|
| `politica` | validación de la política | `policy`, `valid` |
| `canales` | resolución de canales | `category`, `overrides`, `available`, `expect` |
| `matriz` | matriz efectiva | `overrides`, `available`, `expect` |
| `preferencias` | cambio de preferencias | `patch`, `available`, `expect` |
| `plantilla` | plantillas | `template`, `payload`, `expect` |
| `catalogo` | validación del catálogo | `catalog`, `valid` |
| `aviso` | armado del aviso | `event`, `expect` |

Los vectores de `canales`, `matriz`, `preferencias` y `catalogo` llevan la `policy` una vez por
archivo; los de `aviso` llevan además el `catalog`.

`python3 tools/gen_vectors.py` los regenera; con `--check` falla si los archivos del repo no
coinciden con lo generado.
