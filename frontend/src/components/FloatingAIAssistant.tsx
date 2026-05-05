'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import CirrusAvatar from './CirrusAvatar';

interface FloatingAIAssistantProps {
  onOpenChat?: () => void;
}

export default function FloatingAIAssistant({ onOpenChat }: FloatingAIAssistantProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isVisible, setIsVisible] = useState(true);
  const [showTooltip, setShowTooltip] = useState(false);
  const dragRef = useRef<HTMLDivElement>(null);
  const dragStartPos = useRef({ x: 0, y: 0 });
  const hasDragged = useRef(false);
  const router = useRouter();

  // Initialize position (bottom right)
  useEffect(() => {
    const updatePosition = () => {
      setPosition({
        x: window.innerWidth - 100,
        y: window.innerHeight - 100
      });
    };
    
    updatePosition();
    window.addEventListener('resize', updatePosition);
    return () => window.removeEventListener('resize', updatePosition);
  }, []);

  const handleMouseDown = (e: React.MouseEvent) => {
    setIsDragging(true);
    hasDragged.current = false;
    dragStartPos.current = {
      x: e.clientX - position.x,
      y: e.clientY - position.y
    };
  };

  const handleTouchStart = (e: React.TouchEvent) => {
    const touch = e.touches[0];
    setIsDragging(true);
    hasDragged.current = false;
    dragStartPos.current = {
      x: touch.clientX - position.x,
      y: touch.clientY - position.y
    };
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (isDragging) {
      hasDragged.current = true;
      const newX = e.clientX - dragStartPos.current.x;
      const newY = e.clientY - dragStartPos.current.y;
      
      // Constrain to window bounds
      const maxX = window.innerWidth - 80;
      const maxY = window.innerHeight - 80;
      
      setPosition({
        x: Math.max(20, Math.min(newX, maxX)),
        y: Math.max(20, Math.min(newY, maxY))
      });
    }
  };

  const handleTouchMove = (e: TouchEvent) => {
    if (isDragging && e.touches.length > 0) {
      hasDragged.current = true;
      const touch = e.touches[0];
      const newX = touch.clientX - dragStartPos.current.x;
      const newY = touch.clientY - dragStartPos.current.y;
      
      // Constrain to window bounds
      const maxX = window.innerWidth - 80;
      const maxY = window.innerHeight - 80;
      
      setPosition({
        x: Math.max(20, Math.min(newX, maxX)),
        y: Math.max(20, Math.min(newY, maxY))
      });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleTouchEnd = () => {
    setIsDragging(false);
  };

  useEffect(() => {
    if (isDragging) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
      window.addEventListener('touchmove', handleTouchMove, { passive: false });
      window.addEventListener('touchend', handleTouchEnd);
      
      return () => {
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', handleMouseUp);
        window.removeEventListener('touchmove', handleTouchMove);
        window.removeEventListener('touchend', handleTouchEnd);
      };
    }
  }, [isDragging]);

  const handleClick = () => {
    if (!hasDragged.current) {
      router.push('/ai');
      if (onOpenChat) {
        onOpenChat();
      }
    }
    hasDragged.current = false;
  };

  if (!isVisible) return null;

  return (
    <div
      ref={dragRef}
      onMouseDown={handleMouseDown}
      onTouchStart={handleTouchStart}
      onClick={handleClick}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
      style={{
        position: 'fixed',
        left: `${position.x}px`,
        top: `${position.y}px`,
        zIndex: 9999,
        cursor: isDragging ? 'grabbing' : 'grab',
        transition: isDragging ? 'none' : 'transform 0.2s ease',
        touchAction: 'none', // Prevent default touch actions
      }}
      className="group"
    >
      {/* Main Button */}
      <div className="relative">
        {/* Button */}
        <div className="relative w-14 h-14 bg-indigo-600 rounded-2xl shadow-lg flex items-center justify-center transform group-hover:scale-105 group-hover:shadow-xl transition-all duration-200 border border-indigo-500">
          <CirrusAvatar size="sm" />
          
          {/* Online dot */}
          <div className="absolute -top-1 -right-1 w-3 h-3 bg-emerald-500 rounded-full border-2 border-white"></div>
        </div>
        
        {/* Tooltip */}
        {showTooltip && !isDragging && (
          <div className="absolute bottom-full right-0 mb-2 px-3 py-2 bg-slate-900 text-white text-sm rounded-xl shadow-lg whitespace-nowrap animate-fade-in">
            <div className="font-semibold text-xs">Ask AI</div>
            <div className="text-[10px] text-slate-400 mt-0.5">Click to open chat</div>
            {/* Arrow */}
            <div className="absolute top-full right-4 w-0 h-0 border-l-4 border-r-4 border-t-4 border-transparent border-t-slate-900"></div>
          </div>
        )}
      </div>

      {/* Drag handle indicator */}
      {isDragging && (
        <div className="absolute -bottom-8 left-1/2 transform -translate-x-1/2 px-2 py-1 bg-gray-800 text-white text-xs rounded shadow-lg whitespace-nowrap">
          Release to place
        </div>
      )}
    </div>
  );
}
