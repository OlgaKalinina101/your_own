import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

export default defineConfig({
  resolve: {
    alias: { "@": resolve(__dirname, ".") },
  },
  test: {
    // Only the pure modules. Component tests would need a DOM environment and
    // are not what this suite is for — see the review's "tests as insurance".
    include: ["lib/**/*.test.ts"],
  },
});
