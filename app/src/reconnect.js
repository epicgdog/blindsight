export function backoffMs(attempt, { base = 500, max = 8000, jitter = false, rand = Math.random } = {}) {
  const raw = Math.min(max, base * 2 ** Math.min(attempt, 30));
  return jitter ? Math.round(raw * (0.75 + rand() * 0.5)) : raw;
}
