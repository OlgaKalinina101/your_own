import { describe, expect, it } from "vitest";

import { ApiError, apiErrorFrom, describeApiError, isTransient } from "./apiError";

function response(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiErrorFrom", () => {
  it("reads the backend's 503 shape", async () => {
    // main.py `_unavailable` — the one error the backend explains properly.
    const error = await apiErrorFrom(
      response(503, {
        detail: "connection refused",
        cause: "database_unavailable",
        hint: "The backend is running; it cannot reach PostgreSQL.",
      }),
    );
    expect(error.status).toBe(503);
    expect(error.causeCode).toBe("database_unavailable");
    expect(error.hint).toContain("cannot reach PostgreSQL");
  });

  it("survives an error with no body at all", async () => {
    const error = await apiErrorFrom(response(500));
    expect(error.status).toBe(500);
    expect(error.detail).toBeUndefined();
  });

  it("survives a body that is not JSON", async () => {
    const error = await apiErrorFrom(
      new Response("Internal Server Error", { status: 500 }),
    );
    expect(error.status).toBe(500);
    expect(error.message).toBe("HTTP 500");
  });

  it("ignores fields of the wrong type instead of showing them", async () => {
    const error = await apiErrorFrom(response(400, { detail: { nested: true } }));
    expect(error.detail).toBeUndefined();
  });
});

describe("describeApiError", () => {
  it("names the one thing the user can actually fix on a 401", async () => {
    const error = await apiErrorFrom(
      response(401, { detail: "Invalid or missing auth token" }),
    );
    expect(describeApiError(error)).toContain("Settings");
  });

  it("prefers the hint, which is written for a human", () => {
    const error = new ApiError(503, {
      detail: "ConnectionRefusedError(61)",
      hint: "The backend is running; it cannot reach PostgreSQL.",
    });
    expect(describeApiError(error)).toBe(
      "The backend is running; it cannot reach PostgreSQL.",
    );
  });

  it("falls back to detail when there is no hint", () => {
    expect(describeApiError(new ApiError(400, { detail: "bad model id" }))).toBe(
      "bad model id",
    );
  });

  it("falls back to the status when the body said nothing", () => {
    expect(describeApiError(new ApiError(500))).toBe("Server error 500");
  });

  it("distinguishes 'nothing listening' from 'server said no'", () => {
    // A fetch that never got a response rejects with TypeError. Saying
    // "server error" there sends someone hunting the wrong problem.
    expect(describeApiError(new TypeError("Failed to fetch"))).toContain(
      "Cannot reach the server",
    );
  });

  it("never returns an empty string", () => {
    for (const value of [undefined, null, "", 0, {}, new Error("")]) {
      expect(describeApiError(value).length).toBeGreaterThan(0);
    }
  });
});

describe("isTransient", () => {
  it("is true for the failures that end by themselves", () => {
    expect(isTransient(new ApiError(503))).toBe(true);
    expect(isTransient(new ApiError(500))).toBe(true);
  });

  it("is false for the ones that need someone to act", () => {
    expect(isTransient(new ApiError(401))).toBe(false);
    expect(isTransient(new ApiError(404))).toBe(false);
    expect(isTransient(new TypeError("Failed to fetch"))).toBe(false);
  });
});
