# Hoja de ruta

## Hecho

- Spec v0: política, resolución de canales, matriz efectiva y cambio de preferencias.
- Vectores de conformidad y su generador independiente.
- Núcleo en Python y en TypeScript, sin dependencias, pasando los mismos vectores.
- Puertos declarados como borrador.

## Lo que sigue

1. **Reservar el nombre** en npm y PyPI con una primera versión.
2. **Spec v1: el aviso.** Ingesta, deduplicación por clave, armado del título desde el catálogo
   de tipos, y la bandeja: paginado por cursor, contador de no leídos con tope, marcar leído.
3. **Adaptadores de base.** Postgres y SQLite/D1, con su batería de conformidad. Son los dos
   primeros, para cumplir la regla de los dos adaptadores.
4. **Spec v1: las entregas.** Estados de una entrega, reintentos y qué hacer con un destino
   vencido.
5. **Adaptadores de canal.** Email y web push; después APNs.
6. **La campanita.** Componentes para mostrar la bandeja y la pantalla de preferencias.
7. **Un servicio de referencia.** Una API HTTP lista para desplegar, armada con el núcleo y los
   adaptadores, para quien no quiera embeber la librería.

## Antes de abrir el repo

- Activar el CI en cada push y pull request (hoy corre sólo a mano, ver
  `.github/workflows/ci.yml`).
- Configurar las publicaciones a npm y PyPI.

## Fuera de alcance

- Flujos con pasos y esperas, y un editor visual de plantillas.
- Un catálogo propio de proveedores: cada canal es un adaptador que escribe quien lo necesita.
