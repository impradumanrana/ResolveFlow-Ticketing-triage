/**
 * Runtime repository selection.
 *
 * There is exactly one runtime implementation. The in-memory repository is a
 * test fixture and is deliberately unreachable from here: no environment
 * variable, header, or query parameter can select it.
 */

import { PostgresIdentityRepository } from "./postgres-repository";
import type { IdentityRepository } from "./repository";

let cached: IdentityRepository | null = null;

export function createIdentityRepository(): IdentityRepository {
  if (!cached) {
    cached = new PostgresIdentityRepository();
  }
  return cached;
}
