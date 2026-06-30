// Accent colors come back from the backend as `#rrggbb` hex strings. WebKitGTK
// renders plain hex (and 8-digit `#rrggbbaa` alpha) reliably, whereas color-mix()
// and oklch() are flaky there, so we blend in JS and keep everything as hex.

function parseHex(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Blend `fg` over `bg`, where `amount` is the weight of `fg` (0..1). Returns hex. */
export function blendHex(fg: string, bg: string, amount: number): string {
  const f = parseHex(fg);
  const b = parseHex(bg);
  if (!f || !b) return fg;
  const mix = (i: number) =>
    Math.round(f[i] * amount + b[i] * (1 - amount))
      .toString(16)
      .padStart(2, '0');
  return `#${mix(0)}${mix(1)}${mix(2)}`;
}
