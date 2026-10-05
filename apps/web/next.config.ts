import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";

const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  outputFileTracingRoot: repositoryRoot,
  // Next writes AGENTS.md and CLAUDE.md into the project on dev start.
  // This is a client's repository; the framework does not get to add
  // files to it as a side effect of running the server.
  agentRules: false,
};

export default nextConfig;
