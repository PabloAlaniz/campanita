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
pnpm test:d1        # los almacenes de D1, contra el runtime local de Workers
```

```bash
cd python
uv sync
uv run pytest       # los mismos vectores contra el paquete de Python
uv run mypy
uv run ruff check . ../tools && uv run ruff format --check . ../tools
```

Los almacenes de Postgres se prueban contra una base de verdad, en TypeScript y en Python. Sin
`CAMPANITA_PG_URL`, esos tests se saltean:

```bash
docker run -d --name campanita-pg -e POSTGRES_PASSWORD=x -p 55432:5432 postgres:17
export CAMPANITA_PG_URL=postgres://postgres:x@localhost:55432/postgres
pnpm test:pg
(cd python && uv run pytest tests/test_postgres.py)
```

## Antes de proponer un cambio

Leé [`docs/principios.md`](docs/principios.md). El más importante: el núcleo no nombra productos,
proveedores ni bases, y lo que es particular de un caso entra como dato o como adaptador.

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

## Versiones

Los dos paquetes salen siempre con la misma versión, porque implementan el mismo spec. La maneja
[release-please](https://github.com/googleapis/release-please) a partir de los mensajes de commit
([Conventional Commits](https://www.conventionalcommits.org/)): `feat:` sube la versión menor,
`fix:` la de parche, y un cambio incompatible (`feat!:`) también la menor mientras estemos en 0.x.

En cada push a `main`, release-please actualiza un PR de versión con el CHANGELOG. Al mergearlo
se crea el tag `vX.Y.Z` y se publican `campanita` en npm y en PyPI desde
`.github/workflows/release.yml`, con trusted publishing: no hay tokens en el repo. Las versiones
de `ts/package.json`, `python/pyproject.toml` y `python/uv.lock` no se tocan a mano.
