/** Lo que comparten los canales para informar un resultado. */
import type { DeliveryResult } from '../ports.js';

/**
 * El nombre de un error, y nada más: el mensaje de un error de red o de un
 * proveedor puede repetir el destino o datos del pedido.
 */
export const errorName = (error: unknown): string => (error instanceof Error ? error.name : 'Error');

/** Un `failed` con el motivo y el nombre del error. */
export const failed = (reason: string, error: unknown): DeliveryResult => ({
  outcome: 'failed',
  error: `${reason}: ${errorName(error)}`,
});
