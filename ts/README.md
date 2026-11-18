# campanita (TypeScript)

El núcleo de [campanita](https://github.com/PabloAlaniz/campanita) en TypeScript: decide por qué
canales sale un aviso y qué puede cambiar el usuario. Sin dependencias; corre en Node 20+,
Cloudflare Workers y navegadores.

> **Borrador.** Implementa la versión 0 del spec. Todavía no hay adaptadores de base ni de canal.

```ts
import { parsePolicy, resolveChannels, effectiveMatrix, validatePatch } from 'campanita';

const policy = parsePolicy({
  security: {
    inapp: { default: true, locked: true },
    email: { default: true, locked: true },
  },
  activity: {
    inapp: { default: true, locked: true },
    email: { default: false },
    push: { default: false },
  },
});

const disponibles = ['email'];                       // lo que esta instalación puede entregar
const overrides = { activity: { email: true } };     // lo que cambió la persona

resolveChannels(policy, 'activity', overrides, disponibles); // ['email']
resolveChannels(policy, 'security', { security: { email: false } }, disponibles);
// ['email']: seguridad está bloqueada, el override no la apaga

effectiveMatrix(policy, overrides, disponibles)['activity']?.['push'];
// { enabled: false, locked: true }: push todavía no está disponible

validatePatch(policy, { activity: { email: false } }, disponibles);
// [{ category: 'activity', channel: 'email', enabled: false }]
validatePatch(policy, { security: { email: false } }, disponibles);
// PatchRejectedError: cambio de preferencias rechazado en security.email: la celda está bloqueada
```

Los puertos (`NotificationStore`, `PreferenceStore`, `DeliveryChannel`) son un borrador: fijan la
forma que van a tener la base, los canales de entrega y las preferencias, pero el spec v0 todavía
no los cubre.

## Desarrollo

Desde la raíz del repo:

```bash
pnpm install
pnpm test
pnpm typecheck
pnpm build
```

Los tests corren los vectores de `../spec/vectors/`, los mismos que corre el paquete de Python.
