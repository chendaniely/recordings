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
  let pauses = 0;
  let cancelled = false;
  let done = false;
  const expire = () => {
    if (done || cancelled) return;
    done = true;
    handle = null;
    onExpire();
  };
  let handle: unknown = clock.set(expire, left);
  return {
    /** A fresh time, keeping the pause count: a merged toast whose text changed while hovered. */
    restart(newMs: number) {
      if (cancelled || done) return;
      if (handle !== null) clock.clear(handle);
      left = newMs;
      startedAt = clock.now();
      handle = pauses === 0 ? clock.set(expire, left) : null;
    },
    pause() {
      if (cancelled || done) return;
      pauses += 1;
      if (pauses === 1 && handle !== null) {
        clock.clear(handle);
        handle = null;
        left -= clock.now() - startedAt;
      }
    },
    resume() {
      if (cancelled || done || pauses === 0) return;
      pauses -= 1;
      if (pauses === 0) {
        startedAt = clock.now();
        handle = clock.set(expire, Math.max(0, left));
      }
    },
    cancel() {
      cancelled = true;
      if (handle !== null) clock.clear(handle);
      handle = null;
    },
  };
}
