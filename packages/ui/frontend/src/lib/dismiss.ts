/** A toast's own timer (tagging plan, decision 9): about 8 s, paused while the toast is hovered
 * or focused. Pauses nest, so hover and focus together need both to end. */

export interface Clock { set(fn: () => void, ms: number): unknown; clear(handle: unknown): void; now(): number }

export const TOAST_MS = 8000;

const realClock: Clock = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  now: () => Date.now(),
};

export function pausableTimer(ms: number, onExpire: () => void, clock: Clock = realClock) {
  let left = ms;
  let startedAt = clock.now();
  let handle: unknown = clock.set(onExpire, left);
  let pauses = 0;
  let cancelled = false;
  return {
    pause() {
      if (cancelled) return;
      pauses += 1;
      if (pauses === 1 && handle !== null) {
        clock.clear(handle);
        handle = null;
        left -= clock.now() - startedAt;
      }
    },
    resume() {
      if (cancelled || pauses === 0) return;
      pauses -= 1;
      if (pauses === 0) {
        startedAt = clock.now();
        handle = clock.set(onExpire, Math.max(0, left));
      }
    },
    cancel() {
      cancelled = true;
      if (handle !== null) clock.clear(handle);
      handle = null;
    },
  };
}
