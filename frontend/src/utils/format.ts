/**
 * Format a large integer as a compact string with K/M suffix.
 * Numbers below 1000 are returned as-is (locale-formatted).
 * Tooltip / precise contexts should still use toLocaleString().
 */
export function fmtN(n: number): string {
  if (n >= 1_000_000) {
    const v = n / 1_000_000;
    return (Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1)) + 'M';
  }
  if (n >= 1_000) {
    const v = n / 1_000;
    return (Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1)) + 'K';
  }
  return n.toLocaleString();
}
