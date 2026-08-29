/**
 * Moving the backend token out of plain text, exactly once.
 *
 * It used to live in AsyncStorage, which on Android is a SQLite file in the
 * app's sandbox and on iOS a plist — neither of which is a place for a secret.
 * And this is not a session token: it opens the whole backend, including
 * `/api/settings/raw`, which hands back the OpenRouter key in full. The cost of
 * losing it is not "log in again", it is "rotate everything".
 *
 * The decision is a function, and separate from `lib/api.ts`, for the usual
 * reason: `api.ts` imports AsyncStorage and so cannot be reached by a test at
 * all. The case worth pinning is the third one below — the copy already in the
 * keychain does *not* excuse leaving the plaintext copy behind, and an early
 * return on "found it in the secure store" is precisely how the old value
 * survives forever and the migration achieves nothing.
 *
 * Deletable once no installed phone can still be carrying the legacy key.
 */

export interface TokenMigration {
  /** The token to use, from whichever place had one. */
  token: string | null;
  /** Write `token` into the secure store. */
  writeSecure: boolean;
  /** Delete the plaintext copy, whether or not it was the one we used. */
  clearLegacy: boolean;
}

export function decideTokenMigration(input: {
  secure: string | null;
  legacy: string | null;
}): TokenMigration {
  const { secure, legacy } = input;

  if (secure) {
    // Already migrated. The plaintext copy is still a plaintext copy.
    return { token: secure, writeSecure: false, clearLegacy: Boolean(legacy) };
  }
  if (legacy) {
    return { token: legacy, writeSecure: true, clearLegacy: true };
  }
  return { token: null, writeSecure: false, clearLegacy: false };
}
