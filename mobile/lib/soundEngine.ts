/**
 * Sound Engine — keyboard typing sounds during AI stream.
 *
 * Mirrors the Python prototype in tests/sound_engine_test.py:
 *   - Text chunks feed into a queue
 *   - Scheduler processes queue word-by-word with jitter
 *   - Words are classified by length → different pitch sounds
 *   - Punctuation adds pauses
 *   - End-of-message plays a distinct sound
 *   - Minimum interval between sound starts prevents "triple-hit" effect
 */

import { Audio } from "expo-av";
import AsyncStorage from "@react-native-async-storage/async-storage";

// ─── AsyncStorage key ─────────────────────────────────────────────────────────
const KEY_SOUND_VOLUME = "keyboard_sound_volume";
export const DEFAULT_SOUND_VOLUME = 0.7;

export async function loadSoundVolume(): Promise<number> {
  const stored = await AsyncStorage.getItem(KEY_SOUND_VOLUME);
  if (stored === null) return DEFAULT_SOUND_VOLUME;
  const n = parseFloat(stored);
  return isNaN(n) ? DEFAULT_SOUND_VOLUME : Math.max(0, Math.min(1, n));
}

export async function saveSoundVolume(volume: number): Promise<void> {
  await AsyncStorage.setItem(KEY_SOUND_VOLUME, String(volume));
}

// ─── Sound asset map ──────────────────────────────────────────────────────────
const SOUND_ASSETS = {
  s0:    require("../assets/sounds/keyboard/min_sound.mp3"),
  s0_5:  require("../assets/sounds/keyboard/min+0_5.mp3"),
  s1:    require("../assets/sounds/keyboard/min_sound_+1.mp3"),
  s1_5:  require("../assets/sounds/keyboard/min+1_5.mp3"),
  s2:    require("../assets/sounds/keyboard/min_sound+2.mp3"),
  s3:    require("../assets/sounds/keyboard/min_sound+3.mp3"),
  space: require("../assets/sounds/keyboard/two_min_sound.mp3"),
  end:   require("../assets/sounds/keyboard/end_sound.mp3"),
} as const;

type SoundKey = keyof typeof SOUND_ASSETS;

// ─── Timing constants (ms) ────────────────────────────────────────────────────
const BASE_WORD_MS            = 230;
const JITTER_MS               = 45;
const SPACE_DELAY_MS          = 65;
const COMMA_DELAY_MS          = 360;
const PERIOD_DELAY_MS         = 650;
const DASH_DELAY_MS           = 450;
const NEWLINE_DELAY_MS        = 780;
const LONG_WORD_EXTRA_MS      = 65;   // per 3 extra chars beyond 5
const MIN_INTERVAL_MS         = 200;  // minimum time between sound starts

// ─── Word classification table (length → weighted sound choices) ──────────────
const WORD_SOUND_TABLE: Array<[number, Array<[SoundKey, number]>]> = [
  [2,  [["s0",   0.8], ["s0_5", 0.2]]],
  [3,  [["s0",   0.4], ["s0_5", 0.6]]],
  [4,  [["s0_5", 0.5], ["s1",   0.5]]],
  [5,  [["s0_5", 0.2], ["s1",   0.8]]],
  [6,  [["s1",   0.5], ["s1_5", 0.5]]],
  [7,  [["s1_5", 0.6], ["s2",   0.4]]],
  [8,  [["s1_5", 0.2], ["s2",   0.8]]],
  [999,[["s2",   0.3], ["s3",   0.7]]],
];

function classifyWord(word: string): SoundKey {
  const n = word.length;
  for (const [maxLen, choices] of WORD_SOUND_TABLE) {
    if (n <= maxLen) {
      const total = choices.reduce((sum, [, w]) => sum + w, 0);
      let r = Math.random() * total;
      for (const [key, w] of choices) {
        r -= w;
        if (r <= 0) return key;
      }
      return choices[choices.length - 1][0];
    }
  }
  return "s3";
}

function wordExtraDelayMs(word: string): number {
  const extra = Math.max(0, word.length - 5);
  return Math.floor(extra / 3) * LONG_WORD_EXTRA_MS;
}

function jitter(): number {
  return (Math.random() * 2 - 1) * JITTER_MS;
}

// ─── Event types ──────────────────────────────────────────────────────────────
type SoundEvent =
  | { kind: "word";    word: string; delayMs: number }
  | { kind: "space";   delayMs: number }
  | { kind: "punct";   delayMs: number }
  | { kind: "newline"; delayMs: number }
  | { kind: "end";     delayMs: number };

function tokenize(text: string): SoundEvent[] {
  const events: SoundEvent[] = [];
  // Split on word boundaries, punctuation, and whitespace runs
  const tokens = text.match(/\w+|[^\w\s]|\s+/g) ?? [];

  for (const token of tokens) {
    const stripped = token.trim();

    if (!stripped) {
      if (token.includes("\n")) {
        events.push({ kind: "newline", delayMs: NEWLINE_DELAY_MS });
      } else {
        events.push({ kind: "space", delayMs: SPACE_DELAY_MS });
      }
    } else if ([".", "!", "?"].includes(stripped)) {
      events.push({ kind: "punct", delayMs: PERIOD_DELAY_MS });
    } else if (stripped === ",") {
      events.push({ kind: "punct", delayMs: COMMA_DELAY_MS });
    } else if (["—", "-", "–"].includes(stripped)) {
      events.push({ kind: "punct", delayMs: DASH_DELAY_MS });
    } else if ([":", ";"].includes(stripped)) {
      events.push({ kind: "punct", delayMs: COMMA_DELAY_MS });
    } else if (/^\w+$/.test(stripped)) {
      const delay = Math.max(
        MIN_INTERVAL_MS,
        BASE_WORD_MS + wordExtraDelayMs(stripped) + jitter(),
      );
      events.push({ kind: "word", word: stripped, delayMs: delay });
    } else {
      events.push({ kind: "space", delayMs: SPACE_DELAY_MS });
    }
  }

  return events;
}

// ─── Sound Engine class ───────────────────────────────────────────────────────

/**
 * Owns its own lifetime, which is the whole change here.
 *
 * The chat screen used to call `load()` on mount and `unload()` on unmount, and
 * the two raced: `unload()` set `loaded = false` only after several awaits, so a
 * quick leave-and-return let `load()` see `loaded === true` and return
 * immediately — and then the tail of the old `unload()` wiped the sound objects
 * it had just decided not to recreate. The keyboard went silent until the app
 * was restarted, with nothing logged.
 *
 * Eight short mp3s do not need a lifecycle. The engine loads once, on first use,
 * and stays loaded; a screen that is leaving calls `stop()`, which only empties
 * the queue. There is no state left for two callers to disagree about.
 */
export class SoundEngine {
  private soundObjects: Partial<Record<SoundKey, Audio.Sound>> = {};
  private volume: number = DEFAULT_SOUND_VOLUME;
  private loaded = false;
  /** Shared by every concurrent caller, so the assets are decoded once. */
  private loading: Promise<void> | null = null;
  /** Someone moved the slider before the assets finished decoding. */
  private volumeChosen = false;
  private pumping = false;

  private queue: SoundEvent[] = [];
  private nextPlayAt = 0;  // Date.now() ms — earliest time next sound can start

  /**
   * Start decoding, so the first keystroke of a reply is not silent.
   *
   * Optional in the sense that `feed()` would do it anyway; worth calling when
   * a screen opens, because decoding takes longer than the gap between the
   * first two chunks of a stream.
   */
  async prime(): Promise<void> {
    if (this.loaded) return;
    if (!this.loading) this.loading = this._load();
    return this.loading;
  }

  private async _load(): Promise<void> {
    try {
      await Audio.setAudioModeAsync({
        playsInSilentModeIOS: true,
        staysActiveInBackground: false,
      });

      // Only if nobody has said otherwise meanwhile: decoding takes long
      // enough for the settings slider to be dragged during it, and the stored
      // value is by then the older opinion.
      if (!this.volumeChosen) this.volume = await loadSoundVolume();

      await Promise.all(
        (Object.entries(SOUND_ASSETS) as Array<[SoundKey, unknown]>).map(async ([key, asset]) => {
          try {
            const { sound } = await Audio.Sound.createAsync(asset as number, {
              volume: this.volume,
              shouldPlay: false,
            });
            this.soundObjects[key] = sound;
          } catch (e) {
            console.warn(`[SoundEngine] failed to load ${key}:`, e);
          }
        }),
      );

      this.loaded = true;
    } catch (e) {
      // Leave `loading` null so the next feed() can try again rather than
      // silently never sounding for the life of the process.
      console.warn("[SoundEngine] load failed:", e);
      this.loading = null;
    }
  }

  setVolume(v: number): void {
    this.volumeChosen = true;
    this.volume = Math.max(0, Math.min(1, v));
    for (const snd of Object.values(this.soundObjects)) {
      snd?.setVolumeAsync(this.volume).catch(() => {});
    }
  }

  feed(text: string): void {
    if (this.volume === 0) return;
    void this.prime();
    this.queue.push(...tokenize(text));
    void this._pump();
  }

  endMessage(): void {
    if (this.volume === 0) return;
    // Drop everything pending: the stream is over, and a queue that keeps
    // clicking after the reply has landed is just noise arriving late.
    this.queue = [{ kind: "end", delayMs: NEWLINE_DELAY_MS }];
    void this._pump();
  }

  /** Go quiet. Leaves the assets decoded — see the note on the class. */
  stop(): void {
    this.queue = [];
  }

  /**
   * Drain the queue, then exit.
   *
   * The loop this replaces polled an empty queue every 20 ms for the entire life
   * of the process, background included.
   */
  private async _pump(): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.queue.length > 0) {
        const event = this.queue.shift()!;

        // Enforce minimum interval between sound starts
        const wait = this.nextPlayAt - Date.now();
        if (wait > 0) await sleep(wait);

        let soundKey: SoundKey | null = null;
        if (event.kind === "word") {
          soundKey = classifyWord(event.word);
        } else if (event.kind === "space" || event.kind === "punct") {
          soundKey = "space";
        } else if (event.kind === "newline" || event.kind === "end") {
          soundKey = "end";
        }

        if (soundKey) {
          const snd = this.soundObjects[soundKey];
          if (snd) {
            try {
              await snd.setPositionAsync(0);
              await snd.playAsync();
            } catch {
              // Still decoding, or the player is gone. Skip this one.
            }
          }
          this.nextPlayAt = Date.now() + MIN_INTERVAL_MS;
        }

        await sleep(event.delayMs);
      }
    } finally {
      this.pumping = false;
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ─── Singleton ────────────────────────────────────────────────────────────────
export const soundEngine = new SoundEngine();
