/**
 * Límites entre capas de campanita. Ver CONTRIBUTING.md.
 *
 * - core/ es el núcleo: sólo conoce core/ y los puertos.
 * - channels/ y stores/ son adaptadores: dependen del núcleo, nunca entre sí ni del HTTP.
 * - client/ y react/ corren en el navegador: del lado del servidor sólo ven tipos.
 */
/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: 'nucleo-aislado',
      comment: 'core/ sólo importa core/ y ports.ts.',
      severity: 'error',
      from: { path: '^src/core/' },
      to: { path: '^src/', pathNot: ['^src/core/', '^src/ports\\.ts$'] },
    },
    {
      name: 'puertos-sin-adaptadores',
      comment: 'ports.ts describe contratos: sólo usa tipos del núcleo.',
      severity: 'error',
      from: { path: '^src/ports\\.ts$' },
      to: { path: '^src/', pathNot: ['^src/core/'] },
    },
    {
      name: 'adaptadores-independientes',
      comment: 'channels/ y stores/ dependen del núcleo, no del resto.',
      severity: 'error',
      from: { path: '^src/(channels|stores)/' },
      to: { path: '^src/(http|client|react)/' },
    },
    {
      name: 'canales-sin-almacenes',
      severity: 'error',
      from: { path: '^src/channels/' },
      to: { path: '^src/stores/' },
    },
    {
      name: 'almacenes-sin-canales',
      severity: 'error',
      from: { path: '^src/stores/' },
      to: { path: '^src/channels/' },
    },
    {
      name: 'http-sin-adaptadores',
      comment: 'El handler recibe los almacenes por parámetro; no conoce implementaciones.',
      severity: 'error',
      from: { path: '^src/http/' },
      to: { path: '^src/(channels|stores|client|react)/' },
    },
    {
      name: 'navegador-solo-tipos-del-servidor',
      comment: 'client/ sólo trae tipos de http/ y core/: nada que corra en el servidor.',
      severity: 'error',
      from: { path: '^src/client/' },
      to: { path: '^src/(core|http)/', dependencyTypesNot: ['type-only'] },
    },
    {
      name: 'navegador-sin-adaptadores',
      severity: 'error',
      from: { path: '^src/(client|react)/' },
      to: { path: '^src/(channels|stores)/' },
    },
    {
      name: 'react-solo-cliente',
      comment: 'react/ se apoya en client/; del resto sólo tipos.',
      severity: 'error',
      from: { path: '^src/react/' },
      to: { path: '^src/', pathNot: ['^src/react/', '^src/client/'], dependencyTypesNot: ['type-only'] },
    },
    {
      name: 'sin-ciclos',
      severity: 'error',
      from: {},
      to: { circular: true, viaOnly: { dependencyTypesNot: ['type-only'] } },
    },
    {
      name: 'sin-dependencias-de-desarrollo',
      comment: 'El código publicado no puede importar devDependencies (salvo peers como react).',
      severity: 'error',
      from: { path: '^src/' },
      to: { dependencyTypes: ['npm-dev'], dependencyTypesNot: ['type-only', 'npm-peer'] },
    },
    {
      name: 'sin-imports-rotos',
      severity: 'error',
      from: {},
      to: { couldNotResolve: true },
    },
  ],
  options: {
    doNotFollow: { path: 'node_modules' },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: 'tsconfig.json' },
    enhancedResolveOptions: { extensions: ['.ts', '.js'] },
  },
};
