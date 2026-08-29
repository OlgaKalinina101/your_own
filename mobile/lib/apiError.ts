/**
 * What went wrong, in words a person can act on.
 *
 * The backend already answers carefully — a database it cannot reach comes back
 * as 503 with `cause: "database_unavailable"` and a `hint` that says the backend
 * is up and Postgres is not (main.py `_unavailable`). The clients threw all of
 * that away: most calls never checked the status at all, and the ones that did
 * showed "is the backend running?" — the wrong question, since it was.
 *
 * Kept apart from `lib/api.ts` because deriving the sentence is the part worth
 * testing, and it should not need a fetch to do it.
 *
 * Identical to `frontend/lib/apiError.ts`. On a phone the case it exists for is
 * not an edge one at all: the backend is a laptop that sleeps, so "cannot reach
 * the server" is most of the day, and it must not read as "he said nothing".
 */

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: string;
  readonly hint?: string;
  readonly causeCode?: string;

  constructor(
    status: number,
    fields: { detail?: string; hint?: string; causeCode?: string } = {},
  ) {
    super(fields.detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = fields.detail;
    this.hint = fields.hint;
    this.causeCode = fields.causeCode;
  }
}

/** Build an ApiError from a failed response, reading the body when there is one. */
export async function apiErrorFrom(response: Response): Promise<ApiError> {
  let detail: string | undefined;
  let hint: string | undefined;
  let causeCode: string | undefined;
  try {
    const body = (await response.json()) as {
      detail?: unknown;
      hint?: unknown;
      cause?: unknown;
    };
    if (typeof body.detail === "string") detail = body.detail;
    if (typeof body.hint === "string") hint = body.hint;
    if (typeof body.cause === "string") causeCode = body.cause;
  } catch {
    // No body, or not JSON. The status alone still says something.
  }
  return new ApiError(response.status, { detail, hint, causeCode });
}

/**
 * One line to show the person looking at the screen.
 *
 * Order matters: the `hint` is written for a human and beats `detail`, which is
 * often an exception's text; both beat a bare status, which beats nothing.
 */
export function describeApiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "Not authorised — check the token in Settings";
    }
    if (error.hint) return error.hint;
    if (error.detail) return error.detail;
    return `Server error ${error.status}`;
  }
  // A fetch that never reached anyone: wrong address, or nothing listening.
  if (error instanceof TypeError) {
    return "Cannot reach the server — check the address in Settings";
  }
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong";
}

/** True when retrying later is likely to work by itself. */
export function isTransient(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 503 || error.status >= 500);
}
