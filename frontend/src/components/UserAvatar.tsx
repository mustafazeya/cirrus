'use client';

import React, { useState, useEffect, useRef } from 'react';
import { UserIcon } from '@heroicons/react/24/outline';

interface UserAvatarProps {
  photoUrl?: string | null;
  name?: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const sizeClasses = {
  sm: 'w-8 h-8 text-sm',
  md: 'w-10 h-10 text-base',
  lg: 'w-12 h-12 text-lg',
};

export default function UserAvatar({ photoUrl, name, size = 'md', className = '' }: UserAvatarProps) {
  const [imageError, setImageError] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [displayUrl, setDisplayUrl] = useState<string | null>(null);
  const prevPhotoUrlRef = useRef<string | null>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  
  // Handle cached null value
  const actualPhotoUrl = photoUrl === 'null' ? null : photoUrl;

  // Reset states when photoUrl changes, but keep displayUrl if new URL is null/undefined
  // This prevents the image from disappearing when photoUrl temporarily becomes null
  useEffect(() => {
    if (actualPhotoUrl && actualPhotoUrl !== prevPhotoUrlRef.current) {
      // New valid URL - update everything
      setImageError(false);
      setImageLoaded(false);
      setDisplayUrl(actualPhotoUrl);
      prevPhotoUrlRef.current = actualPhotoUrl;
    } else if (!actualPhotoUrl && prevPhotoUrlRef.current && !displayUrl) {
      // PhotoUrl became null but we have no display URL - clear everything
      setImageError(false);
      setImageLoaded(false);
      setDisplayUrl(null);
      prevPhotoUrlRef.current = null;
    }
    // If actualPhotoUrl is null but we have displayUrl, keep showing the cached image
  }, [actualPhotoUrl]);

  // If there's a photo URL and no error, show the image
  if (displayUrl && !imageError) {
    return (
      <div className={`${sizeClasses[size]} rounded-full overflow-hidden flex-shrink-0 relative ${className}`}>
        {/* Show icon as fallback while image is loading */}
        {!imageLoaded && (
          <div
            className="absolute inset-0 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-500"
          >
            <UserIcon className="w-3/5 h-3/5" />
          </div>
        )}
        <img
          src={displayUrl}
          alt={name || 'User'}
          className={`w-full h-full object-cover transition-opacity duration-300 ${
            imageLoaded ? 'opacity-100' : 'opacity-0'
          }`}
          onLoad={() => setImageLoaded(true)}
          onError={() => {
            setImageError(true);
            setImageLoaded(false);
          }}
        />
      </div>
    );
  }

  // Otherwise, show the default avatar icon with SAS branding
  return (
    <div
      className={`${sizeClasses[size]} rounded-full bg-indigo-100 flex items-center justify-center text-indigo-500 flex-shrink-0 ${className}`}
    >
      <UserIcon className="w-3/5 h-3/5" />
    </div>
  );
}
