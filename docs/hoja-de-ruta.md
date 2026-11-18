# Hoja de ruta

## Hecho

- Política, resolución de canales, matriz efectiva y cambio de preferencias.
- Plantillas, catálogo de tipos y armado del aviso a partir de un evento.
- Vectores de conformidad y su generador independiente.
- Núcleo en Python y en TypeScript, sin dependencias, pasando los mismos vectores.
- Puertos declarados como borrador.

## Lo que sigue

1. **Publicar una primera versión** en npm y PyPI.
2. **La bandeja.** Guardar un aviso, no duplicarlo cuando se repite la clave, paginar por cursor,
   contar los no leídos y marcarlos como leídos. Se especifica como secuencias de operaciones
   que cualquier base tiene que reproducir.
3. **Adaptadores de base.** Uno en memoria y uno sobre SQL, con la misma batería de conformidad.
   Son los dos primeros, para cumplir la regla de los dos adaptadores.
4. **Las entregas.** Estados de una entrega, reintentos y qué hacer con un destino vencido.
5. **Adaptadores de canal.** Email y web push; después, push nativo.
6. **La campanita.** Componentes para mostrar la bandeja y la pantalla de preferencias.
7. **Un servicio listo para desplegar.** Una API HTTP armada con el núcleo y los adaptadores,
   para quien no quiera embeber la librería.
8. **Avisos en varios idiomas.** El payload ya se guarda entero para poder redactar de nuevo.

## Fuera de alcance

- Flujos con pasos y esperas, y un editor visual de plantillas.
- Un catálogo propio de proveedores: cada canal es un adaptador que escribe quien lo necesita.
- Autenticación y autorización: el núcleo recibe datos ya autorizados.
