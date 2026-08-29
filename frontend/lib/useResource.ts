"use client";

/**
 * Loading something from the backend, with an error that is not silence.
 *
 * Four pages had the same shape and the same hole: `.catch(() => {})` and then
 * `setLoading(false)`. A 401 and an empty database rendered identically —
 * "no skills", "0 memories", an empty soul — so the one thing the person needed
 * to know was the one thing the screen could not say. For an app whose backend
 * is a process on the far end of a network, a failed request is not an edge
 * case; it is a normal Tuesday.
 *
 * The three states are deliberately mutually exclusive: there is no way to be
 * ready and errored at once, which is how "stale data plus a red banner" turns
 * into someone acting on numbers from ten minutes ago.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { describeApiError } from "@/lib/apiError";

export type Resource<T> =
  | { status: "loading" }
  | { status: "error"; message: string; error: unknown }
  | { status: "ready"; data: T };

export interface ResourceHandle<T> {
  resource: Resource<T>;
  /** Fetch again. Safe to call from an event handler. */
  reload: () => void;
  /** Replace the loaded value without a round trip, after a local edit. */
  update: (next: T | ((current: T) => T)) => void;
}

export function useResource<T>(
  load: () => Promise<T>,
  deps: readonly unknown[] = [],
): ResourceHandle<T> {
  const [resource, setResource] = useState<Resource<T>>({ status: "loading" });

  // The load function is almost always an inline closure, so it is a new
  // reference every render; depending on it directly would refetch forever.
  const loadRef = useRef(load);
  loadRef.current = load;

  // A reload that lands after the component is gone, or after a newer one
  // started, must not overwrite what is on screen.
  const runIdRef = useRef(0);
  const aliveRef = useRef(true);

  const run = useCallback(() => {
    const runId = ++runIdRef.current;
    setResource({ status: "loading" });
    loadRef.current().then(
      (data) => {
        if (aliveRef.current && runId === runIdRef.current) {
          setResource({ status: "ready", data });
        }
      },
      (error: unknown) => {
        if (aliveRef.current && runId === runIdRef.current) {
          setResource({ status: "error", message: describeApiError(error), error });
        }
      },
    );
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    run();
    return () => {
      aliveRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const update = useCallback((next: T | ((current: T) => T)) => {
    setResource((current) => {
      if (current.status !== "ready") return current;
      const data =
        typeof next === "function" ? (next as (c: T) => T)(current.data) : next;
      return { status: "ready", data };
    });
  }, []);

  return { resource, reload: run, update };
}
