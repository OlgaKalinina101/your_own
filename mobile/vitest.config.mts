import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

export default defineConfig({
  resolve: {
    alias: { "@": resolve(__dirname, ".") },
  },
  test: {
    // Pure modules only. Anything touching React Native needs a native runtime
    // and is not what this suite is for.
    include: ["lib/**/*.test.ts"],
  },
});
