# Cómo contribuir

Gracias por tomarte el tiempo.

## Preparación

Hace falta Node 20+, pnpm, Python 3.11+ y [uv](https://docs.astral.sh/uv/).

```bash
pnpm install
pnpm test           # los vectores contra el paquete de TypeScript
pnpm typecheck
pnpm build
pnpm check:package  # publint y arethetypeswrong
pnpm check:spec     # los vectores del repo coinciden con el generador
```

```bash
cd python
uv sync
uv run pytest       # los mismos vectores contra el paquete de Python
uv run mypy
uv run ruff check . ../tools && uv run ruff format --check . ../tools
```

## Cambiar el comportamiento

El comportamiento vive en `spec/`, y los paquetes lo siguen. El orden es siempre el mismo:

1. Cambiá `spec/SPEC.md`.
2. Cambiá `tools/gen_vectors.py` y regenerá: `python3 tools/gen_vectors.py`. El generador es una
   implementación independiente; no importa los paquetes, y no debería empezar a hacerlo.
3. Hacé pasar los vectores en **los dos** paquetes. Un cambio que sólo pasa en uno no entra.

Los archivos de `spec/vectors/` no se editan a mano: `pnpm check:spec` falla si difieren de lo
que genera el script.

Si encontrás un comportamiento que el spec no define, eso es un vector que falta. Agregarlo es
una contribución tan válida como el código.

## Sumar un adaptador

Los puertos están en `python/src/campanita/ports.py` y `ts/src/ports.ts`, y todavía son un
borrador: ver [`docs/puertos.md`](docs/puertos.md) antes de escribir uno.

## Idioma

El proyecto se escribe en español: documentación, spec, comentarios y mensajes de error. Los
identificadores de código van en inglés, y los literales del contrato (`inapp`, `locked`,
`unknown_channel`) no se traducen.
