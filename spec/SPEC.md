# Especificación de campanita, versión 0 (borrador)

Todo port de campanita tiene que comportarse como se describe acá. Los archivos JSON de
`vectors/` son la prueba normativa: un port cumple cuando pasa todos los vectores. Los genera
`tools/gen_vectors.py`, una implementación independiente, para que ningún port se valide a sí
mismo.

La versión 0 es un borrador y puede cambiar sin aviso. Cubre una sola cosa: **por qué canales
sale un aviso y qué puede cambiar el usuario**. La ingesta, la bandeja y las entregas llegan en
la versión 1 (ver `docs/hoja-de-ruta.md`).

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

## Vectores

Cada archivo de `vectors/` cumple `schemas/vector.schema.json` y tiene `spec`, `id`, `kind` y
`cases`.

| `kind` | Qué prueba | Cada caso |
|---|---|---|
| `politica` | validación de la política | `policy`, `valid` |
| `canales` | resolución de canales | `category`, `overrides`, `available`, `expect` |
| `matriz` | matriz efectiva | `overrides`, `available`, `expect` |
| `preferencias` | cambio de preferencias | `patch`, `available`, `expect` |

Los vectores de `canales`, `matriz` y `preferencias` llevan la `policy` una vez por archivo.

`python3 tools/gen_vectors.py` los regenera; con `--check` falla si los archivos del repo no
coinciden con lo generado.
