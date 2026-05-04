'use client';

interface CirrusAvatarProps {
  size?: 'sm' | 'md' | 'lg';
  /**
   * 'brand'  — rounded square, indigo gradient. Sidebar, login, panel header, hero.
   * 'circle' — full circle, teal→violet gradient. AI chat message rows.
   * 'flat'   — rounded square, flat indigo. Fallback/compact contexts.
   */
  variant?: 'brand' | 'circle' | 'flat';
  className?: string;
}

const SIZE_CLASS = { sm: 'w-8 h-8', md: 'w-10 h-10', lg: 'w-12 h-12' } as const;

export default function CirrusAvatar({ size = 'md', variant = 'brand', className = '' }: CirrusAvatarProps) {
  return (
    <svg
      width="100%"
      height="100%"
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      display="block"
      className={`${SIZE_CLASS[size]} flex-shrink-0 ${className}`}
      aria-label="CIRRUS"
      role="img"
    >
      <defs>
        {/* brand: indigo-500 → indigo-800 */}
        <linearGradient id="cirrus-bg" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0%"   stopColor="#6366f1" />
          <stop offset="100%" stopColor="#3730a3" />
        </linearGradient>
        <radialGradient id="cirrus-hl" cx="28%" cy="22%" r="58%">
          <stop offset="0%"   stopColor="white" stopOpacity="0.22" />
          <stop offset="100%" stopColor="white" stopOpacity="0"    />
        </radialGradient>
        <radialGradient id="cirrus-vg" cx="80%" cy="82%" r="52%">
          <stop offset="0%"   stopColor="#1e1b4b" stopOpacity="0.55" />
          <stop offset="100%" stopColor="#1e1b4b" stopOpacity="0"    />
        </radialGradient>

        {/* circle: indigo gradient for the C mark strokes */}
        <linearGradient id="cirrus-ai-stroke" x1="26" y1="7.8" x2="26" y2="32.2" gradientUnits="userSpaceOnUse">
          <stop offset="0%"   stopColor="#6366f1" />
          <stop offset="100%" stopColor="#4f46e5" />
        </linearGradient>
      </defs>

      {/* ── Background ── */}
      {variant === 'brand' && (
        <>
          <rect width="40" height="40" rx="9" fill="url(#cirrus-bg)" />
          <rect width="40" height="40" rx="9" fill="url(#cirrus-hl)" />
          <rect width="40" height="40" rx="9" fill="url(#cirrus-vg)" />
          <rect width="40" height="40" rx="9" fill="none" stroke="white" strokeWidth="0.75" strokeOpacity="0.18" />
        </>
      )}
      {variant === 'circle' && (
        /* White circle with a very subtle indigo ring — clean, light */
        <>
          <circle cx="20" cy="20" r="20" fill="white" />
          <circle cx="20" cy="20" r="20" fill="none" stroke="#e0e7ff" strokeWidth="1" />
        </>
      )}
      {variant === 'flat' && (
        <rect width="40" height="40" rx="9" fill="#4f46e5" />
      )}

      {/* ── C mark — arcs in indigo for circle variant, white for brand/flat ── */}
      <path d="M 26 7.8  A 12.2 12.2 0 0 0 26 32.2"
            stroke={variant === 'circle' ? '#6366f1' : 'white'}
            strokeWidth="2.2" strokeLinecap="round"
            strokeOpacity={variant === 'circle' ? 0.28 : 0.35} />
      <path d="M 26 11.6 A 8.4  8.4  0 0 0 26 28.4"
            stroke={variant === 'circle' ? '#6366f1' : 'white'}
            strokeWidth="2.2" strokeLinecap="round"
            strokeOpacity={variant === 'circle' ? 0.60 : 0.68} />
      <path d="M 26 15   A 5    5    0 0 0 26 25"
            stroke={variant === 'circle' ? '#4f46e5' : 'white'}
            strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="26" cy="20" r="2.5"
              fill={variant === 'circle' ? '#4f46e5' : 'white'} />
    </svg>
  );
}
