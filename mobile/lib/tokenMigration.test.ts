import { describe, expect, it } from "vitest";

import { decideTokenMigration } from "./tokenMigration";

describe("decideTokenMigration", () => {
  it("moves a token that is still in plain text", () => {
    expect(decideTokenMigration({ secure: null, legacy: "tok" })).toEqual({
      token: "tok",
      writeSecure: true,
      clearLegacy: true,
    });
  });

  it("clears the plaintext copy even when the keychain already has one", () => {
    // The case the whole file exists for. An early return on "found it in the
    // secure store" leaves the readable copy sitting there forever, and the
    // migration then achieves precisely nothing while looking done.
    expect(decideTokenMigration({ secure: "tok", legacy: "tok" })).toEqual({
      token: "tok",
      writeSecure: false,
      clearLegacy: true,
    });
  });

  it("prefers the secure copy when the two disagree", () => {
    // A stale plaintext leftover must never win over the one that was written
    // deliberately.
    expect(decideTokenMigration({ secure: "new", legacy: "old" }).token).toBe("new");
  });

  it("does nothing when the token is already only in the keychain", () => {
    expect(decideTokenMigration({ secure: "tok", legacy: null })).toEqual({
      token: "tok",
      writeSecure: false,
      clearLegacy: false,
    });
  });

  it("reports no token rather than an empty one", () => {
    expect(decideTokenMigration({ secure: null, legacy: null })).toEqual({
      token: null,
      writeSecure: false,
      clearLegacy: false,
    });
  });

  it("treats an empty string as no token", () => {
    // AsyncStorage returns "" for a key someone wrote an empty value into, and
    // `Bearer ` with nothing after it is not an improvement on no header.
    expect(decideTokenMigration({ secure: "", legacy: "" })).toEqual({
      token: null,
      writeSecure: false,
      clearLegacy: false,
    });
  });
});
