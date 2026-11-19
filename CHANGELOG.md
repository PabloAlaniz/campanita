# Changelog

## [0.4.0](https://github.com/PabloAlaniz/campanita/compare/v0.3.0...v0.4.0) (2026-11-19)


### Features

* el transporte HTTP lee el resultado del cuerpo de la respuesta ([#4](https://github.com/PabloAlaniz/campanita/issues/4)) ([c423876](https://github.com/PabloAlaniz/campanita/commit/c42387656a71b0d73db497134329c2f4af944bb0))

## [0.3.0](https://github.com/PabloAlaniz/campanita/compare/v0.2.0...v0.3.0) (2026-11-18)


### Features

* canal de Slack, que cada persona conecta con OAuth ([4438347](https://github.com/PabloAlaniz/campanita/commit/44383470d0d66a326837643120014c0d289fea1d))

## [0.2.0](https://github.com/PabloAlaniz/campanita/compare/v0.1.0...v0.2.0) (2026-11-18)


### ⚠ BREAKING CHANGES

* en el puerto PreferenceStore, que estaba marcado como borrador, el ámbito pasa a ser un argumento con nombre: `scope=` en Python y `{ scope }` en TypeScript.

### Features

* canal de email con transporte intercambiable ([04d3a6b](https://github.com/PabloAlaniz/campanita/commit/04d3a6bd3f39708fc2ab2175fc84ca9a9eac5910))
* el recorrido completo, de un evento a un aviso entregado ([ffeb4d2](https://github.com/PabloAlaniz/campanita/commit/ffeb4d2c81227e5a894e591e6fe4b7815d221cbd))
* las preferencias guardadas, sobre cualquier base ([dfb4430](https://github.com/PabloAlaniz/campanita/commit/dfb443077dceea4c152a06b825213e116e2b5176))
* no molestar, un horario por persona en el que los canales no interrumpen ([fdd9571](https://github.com/PabloAlaniz/campanita/commit/fdd95716cb683d2bad9c194c0cf3e97b045c448e))
* **ts:** almacenes para Cloudflare D1 ([760d485](https://github.com/PabloAlaniz/campanita/commit/760d48589fce8845503eee8a3c18e9f14e15716d))

## [0.1.0](https://github.com/PabloAlaniz/campanita/compare/v0.0.1...v0.1.0) (2026-11-18)


### Features

* la bandeja, con almacenes en memoria y SQLite ([690d0f4](https://github.com/PabloAlaniz/campanita/commit/690d0f4483782b51de394b35b23271fbb1179bd4))
* plantillas, catálogo de tipos y armado del aviso ([d27c5a9](https://github.com/PabloAlaniz/campanita/commit/d27c5a992cf1c7141d9e0960d9df7386e1ff1f7a))
* spec v0 y núcleo en Python y TypeScript ([d4b9301](https://github.com/PabloAlaniz/campanita/commit/d4b9301b3ebfeadeb78a83aacfdc217571b0e417))
