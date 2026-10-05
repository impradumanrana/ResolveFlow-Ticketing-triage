import "server-only";

import { Pool, type PoolConfig } from "pg";

/**
 * The database pool.
 *
 * Server-only: `server-only` makes the build fail if this module is ever
 * imported into a client component, rather than shipping connection details to
 * a browser.
 *
 * Connection shape follows C02: Cloud Run reaches Cloud SQL over a unix socket
 * from the mounted `/cloudsql` volume, and authenticates as an IAM service
 * account, so there is no password in configuration. `DATABASE_URL` remains
 * available for local development against a plain PostgreSQL container.
 */

let pool: Pool | null = null;

function poolConfig(): PoolConfig {
  const databaseUrl = process.env.DATABASE_URL?.trim();
  if (databaseUrl) {
    return { connectionString: databaseUrl };
  }

  const instance = process.env.DB_INSTANCE_CONNECTION_NAME?.trim();
  const database = process.env.DB_NAME?.trim();
  const user = process.env.DB_IAM_USER?.trim();

  if (!instance || !database || !user) {
    throw new Error(
      "Database configuration is missing. Set DATABASE_URL for local development, " +
        "or DB_INSTANCE_CONNECTION_NAME, DB_NAME, and DB_IAM_USER on Cloud Run.",
    );
  }

  return {
    host: `/cloudsql/${instance}`,
    database,
    // Cloud SQL expects the IAM user without the .gserviceaccount.com suffix.
    user: user.replace(/\.gserviceaccount\.com$/, ""),
    max: Number(process.env.DB_POOL_MAX ?? "5"),
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 10_000,
  };
}

export function getPool(): Pool {
  if (!pool) {
    pool = new Pool(poolConfig());
  }
  return pool;
}

export async function query<T extends Record<string, unknown>>(
  text: string,
  values: readonly unknown[] = [],
): Promise<T[]> {
  const result = await getPool().query<T>(text, values as unknown[]);
  return result.rows;
}
