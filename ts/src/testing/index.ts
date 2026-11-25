/**
 * `campanita/testing`: lo que necesita quien escribe un adaptador propio.
 *
 * Las secuencias de conformidad del spec, para correrlas contra un almacén o un
 * adaptador HTTP, y las reglas que todo almacén comparte (límites, tope del
 * contador, milisegundos y paginado), para no copiarlas.
 */
export {
  notificationToWire,
  quietHoursToWire,
  recipientFromVectorRequest,
  runHttpVector,
  runPreferenceVector,
  runQuietHoursVector,
  runStoreVector,
} from '../conformance.js';
export type { HttpVectorSetup } from '../conformance.js';
export { checkCap, checkLimit, paginate, StoreError, toMs } from '../core/inbox.js';
