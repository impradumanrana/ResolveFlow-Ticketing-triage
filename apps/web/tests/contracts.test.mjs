import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const contractUrl = new URL("../../../contracts/openapi/v1.json", import.meta.url);

test("web consumes the versioned safe triage contract", async () => {
  const contract = JSON.parse(await readFile(contractUrl, "utf8"));
  assert.equal(contract.info.version, "v1");
  assert.ok(contract.paths["/healthz"]);
  assert.ok(contract.paths["/v1/capabilities"]);
  assert.ok(contract.paths["/v1/triage"]);
  assert.equal(
    Object.keys(contract.paths).some((path) => path.toLowerCase().includes("send")),
    false,
  );
});
