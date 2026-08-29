import { describe, expect, it } from "vitest";

import { isSafeLink, safeUrlTransform } from "./links";

describe("isSafeLink", () => {
  it("opens the three schemes a conversation has reason to open", () => {
    expect(isSafeLink("https://example.com/x?y=1#z")).toBe(true);
    expect(isSafeLink("http://192.168.1.10:8000/a")).toBe(true);
    expect(isSafeLink("mailto:someone@example.com")).toBe(true);
  });

  it("ignores the case of the scheme", () => {
    expect(isSafeLink("HTTPS://example.com")).toBe(true);
    expect(isSafeLink("MailTo:a@b.c")).toBe(true);
  });

  it("refuses schemes that leave the app for somewhere else", () => {
    for (const href of [
      "javascript:alert(1)",
      "tel:+70000000000",
      "sms:+70000000000?body=hi",
      "intent://scan/#Intent;scheme=zxing;end",
      "file:///etc/passwd",
      "content://media/external/images/1",
      "data:text/html;base64,PHNjcmlwdD4=",
    ]) {
      expect(isSafeLink(href), href).toBe(false);
    }
  });

  it("is not fooled by whitespace around or inside the scheme", () => {
    // Leading space is how a scheme check on the raw string gets skipped;
    // a newline inside one is how it gets read as something else entirely.
    expect(isSafeLink("  javascript:alert(1)")).toBe(false);
    expect(isSafeLink("java\nscript:alert(1)")).toBe(false);
    expect(isSafeLink("java\tscript:alert(1)")).toBe(false);
  });

  it("refuses a link with no scheme at all", () => {
    // Relative, or protocol-relative and thus whatever the platform decides.
    // A reply has nothing to be relative to.
    expect(isSafeLink("/api/settings/raw")).toBe(false);
    expect(isSafeLink("//example.com")).toBe(false);
    expect(isSafeLink("example.com")).toBe(false);
  });

  it("refuses nothing at all", () => {
    expect(isSafeLink("")).toBe(false);
    expect(isSafeLink("   ")).toBe(false);
    expect(isSafeLink(null)).toBe(false);
    expect(isSafeLink(undefined)).toBe(false);
  });
});

describe("safeUrlTransform", () => {
  it("passes an allowed link through untouched", () => {
    expect(safeUrlTransform("https://example.com/x?y=1")).toBe("https://example.com/x?y=1");
  });

  it("empties a refused one, which is how react-markdown drops the href", () => {
    expect(safeUrlTransform("javascript:alert(1)")).toBe("");
  });
});
