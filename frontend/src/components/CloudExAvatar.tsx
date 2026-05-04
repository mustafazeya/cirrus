'use client';

import { useId } from 'react';

interface CirrusAvatarProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const SIZE_PX = { sm: 32, md: 40, lg: 48 } as const;

/**
 * CIRRUS brand avatar.
 * Mark: geometric C arc (center 21,20 · r=9 · ±55° opening) + precision dot at
 * upper terminal. Self-contained — includes its own indigo-600 rounded background.
 * Works identically at 16 px (favicon) → 48 px (empty-state hero).
 */
export default function CirrusAvatar({ size = 'md', className = '' }: CirrusAvatarProps) {
  // useId gives a page-unique prefix so multiple instances don't share gradient IDs.
  const uid = useId().replace(/:/g, '');
  const px = SIZE_PX[size];

  // C arc: center (21, 20), radius 9, opening ±55°
  // cos 55° ≈ 0.5736  sin 55° ≈ 0.8192
  // Upper terminal: (21 + 9·cos(-55°), 20 + 9·sin(-55°)) = (26.16, 12.63)
  // Lower terminal: (26.16, 27.37)
  // SVG arc: M upper A 9 9 0 1 0 lower  →  large-arc CCW = C shape opening right

  return (
    <svg
      width={px}
      height={px}
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`flex-shrink-0 ${className}`}
      aria-label="CIRRUS"
      role="img"
    >
      <defs>
        {/* Subtle top-left light source — gives the background depth without looking gradientey */}
        <radialGradient id={`${uid}g`} cx="32%" cy="22%" r="68%">
          <stop offset="0%" stopColor="white" stopOpacity="0.18" />
          <stop offset="100%" stopColor="white" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* ── Background ─────────────────────────────────────────────────── */}
      <rect width="40" height="40" rx="9" fill="#4f46e5" />
      <rect width="40" height="40" rx="9" fill={`url(#${uid}g)`} />

      {/* ── C arc ──────────────────────────────────────────────────────── */}
      <path
        d="M 26.16 12.63 A 9 9 0 1 0 26.16 27.37"
        stroke="white"
        strokeWidth="3"
        strokeLinecap="round"
        fill="none"
      />

      {/* ── Precision dot at upper terminal ───────────────────────────── */}
      {/* Elevates the mark from a generic C to a distinctive brand glyph. */}
      <circle cx="26.16" cy="12.63" r="1.95" fill="white" />
    </svg>
  );
}
