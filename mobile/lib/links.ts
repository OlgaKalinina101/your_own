/**
 * Which links in a reply are allowed to leave the app.
 *
 * The text in a bubble is written by a model, which makes every `[…](…)` in it
 * untrusted input that a person is invited to tap. `react-native-markdown-display`
 * hands an unrecognised href straight to `Linking.openURL`, so `tel:`, `sms:`
 * and `intent://` opened silently — and on the desktop the only thing stopping
 * the same was react-markdown's `defaultUrlTransform`, someone else's default
 * rather than our decision.
 *
 * Three schemes are worth opening from a conversation. Everything else is a way
 * out of the app that nobody asked for.
 *
 * Kept identical to `frontend/lib/links.ts`, and pinned by the same tests.
 */

const ALLOWED_SCHEMES = new Set(["http", "https", "mailto"]);

/**
 * A scheme is what precedes the first `:` — and only if it is a scheme at all:
 * letters, digits and `+-.`, starting with a letter (RFC 3986). Deliberately
 * strict, because the interesting attempts are the ones that only look like a
 * scheme: a newline inside it, a leading space, a `//` with no scheme so the
 * platform supplies one.
 */
const SCHEME = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;

export function isSafeLink(href: string | null | undefined): boolean {
  if (!href) return false;
  const trimmed = href.trim();
  const match = SCHEME.exec(trimmed);
  // No scheme means a relative link, and a reply has nothing to be relative to.
  if (!match) return false;
  return ALLOWED_SCHEMES.has(match[1].toLowerCase());
}

/**
 * For `react-native-markdown-display`, whose `openUrl` opens the link only when
 * the handler returns exactly `true`.
 */
export function onMarkdownLinkPress(url: string): boolean {
  if (isSafeLink(url)) return true;
  console.warn("[links] refused to open:", url);
  return false;
}
