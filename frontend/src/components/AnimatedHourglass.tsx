'use client';

import React from 'react';

interface AnimatedHourglassProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const sizeClasses = {
  sm: 'w-6 h-6',
  md: 'w-8 h-8',
  lg: 'w-10 h-10',
};

/**
 * Animated Hourglass - Continuously rotates to show loading state
 */
export default function AnimatedHourglass({ size = 'md', className = '' }: AnimatedHourglassProps) {
  return (
    <div className={`${sizeClasses[size]} flex-shrink-0 ${className}`}>
      <svg
        viewBox="0 0 100 100"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className="w-full h-full animate-spin"
        style={{ animationDuration: '2s' }}
      >
        {/* Gradient definitions */}
        <defs>
          <linearGradient id="hourglassGradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" style={{ stopColor: '#6366f1', stopOpacity: 1 }} />
            <stop offset="50%" style={{ stopColor: '#4f46e5', stopOpacity: 1 }} />
            <stop offset="100%" style={{ stopColor: '#4338ca', stopOpacity: 1 }} />
          </linearGradient>
          
          <linearGradient id="sandGradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" style={{ stopColor: '#e0e7ff', stopOpacity: 1 }} />
            <stop offset="100%" style={{ stopColor: '#818cf8', stopOpacity: 1 }} />
          </linearGradient>
        </defs>

        {/* Hourglass frame */}
        <g fill="url(#hourglassGradient)">
          {/* Top frame */}
          <path d="M 30 15 L 70 15 L 70 20 L 65 20 L 50 35 L 35 20 L 30 20 Z" />
          
          {/* Bottom frame */}
          <path d="M 30 85 L 70 85 L 70 80 L 65 80 L 50 65 L 35 80 L 30 80 Z" />
          
          {/* Middle connector */}
          <rect x="48" y="35" width="4" height="30" />
        </g>

        {/* Top sand (draining) */}
        <g fill="url(#sandGradient)" className="animate-pulse" style={{ animationDuration: '1.5s' }}>
          <path d="M 35 20 L 50 35 L 65 20 L 60 20 L 50 30 L 40 20 Z" opacity="0.8" />
        </g>

        {/* Bottom sand (filling) */}
        <g fill="url(#sandGradient)">
          <path d="M 35 80 L 50 65 L 65 80 L 60 80 L 50 70 L 40 80 Z" />
          <ellipse cx="50" cy="80" rx="12" ry="3" />
        </g>

        {/* Falling sand particles (animated) */}
        <g fill="#3385ff" className="animate-pulse" style={{ animationDuration: '1s' }}>
          <circle cx="50" cy="45" r="1" opacity="0.6" />
          <circle cx="50" cy="52" r="0.8" opacity="0.4" />
          <circle cx="50" cy="58" r="1" opacity="0.7" />
        </g>
      </svg>
    </div>
  );
}
