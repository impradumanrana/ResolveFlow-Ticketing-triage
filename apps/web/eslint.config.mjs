import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTypescript,
  // `.test-build` is tsc output for the offline test suite, not source.
  globalIgnores([".next/**", "out/**", "build/**", ".test-build/**", "next-env.d.ts"]),
]);
