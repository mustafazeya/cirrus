'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDownIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline';

interface Option {
  value: string;
  label: string;
  subtitle?: string;
}

interface MultiSearchableSelectProps {
  options: Option[];
  selectedValues: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  showSelectAll?: boolean;
  maxSelections?: number;
}

export default function MultiSearchableSelect({
  options,
  selectedValues,
  onChange,
  placeholder = "Select...",
  disabled = false,
  className = "",
  showSelectAll = true,
  maxSelections
}: MultiSearchableSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Filter options based on search term
  const filteredOptions = options.filter(option =>
    option.label.toLowerCase().includes(searchTerm.toLowerCase()) ||
    option.value.toLowerCase().includes(searchTerm.toLowerCase()) ||
    (option.subtitle && option.subtitle.toLowerCase().includes(searchTerm.toLowerCase()))
  );

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setSearchTerm('');
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Focus search input when dropdown opens
  useEffect(() => {
    if (isOpen && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [isOpen]);

  const handleToggle = (optionValue: string) => {
    if (selectedValues.includes(optionValue)) {
      onChange(selectedValues.filter(v => v !== optionValue));
    } else {
      // Check max selections limit
      if (maxSelections && selectedValues.length >= maxSelections) {
        return; // Don't add more if limit reached
      }
      onChange([...selectedValues, optionValue]);
    }
  };

  const handleSelectAll = () => {
    if (selectedValues.length === options.length) {
      onChange([]);
    } else {
      // Respect max selections when selecting all
      const valuesToSelect = maxSelections 
        ? options.slice(0, maxSelections).map(opt => opt.value)
        : options.map(opt => opt.value);
      onChange(valuesToSelect);
    }
  };

  const allSelected = options.length > 0 && selectedValues.length === options.length;

  return (
    <div className={`relative ${className}`} ref={dropdownRef}>
      {/* Selected value display */}
      <button
        type="button"
        className={`
          w-full text-left flex items-center justify-between px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent text-base sm:text-sm
          ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:border-gray-400'}
          ${className?.includes('h-[') ? '' : 'h-auto'}
        `}
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
      >
        <span className={`text-base sm:text-sm ${selectedValues.length > 0 ? 'text-gray-900' : 'text-gray-500'}`}>
          {selectedValues.length > 0 
            ? `${selectedValues.length} selected` 
            : placeholder}
        </span>
        <ChevronDownIcon 
          className={`h-4 w-4 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} 
        />
      </button>

      {/* Dropdown */}
      {isOpen && !disabled && (
        <div className="absolute z-50 mt-1 w-full sm:w-full md:min-w-[320px] bg-white border border-gray-300 rounded-lg sm:rounded-md shadow-lg max-h-[60vh] sm:max-h-72 overflow-hidden">
          {/* Search input */}
          <div className="p-3 sm:p-2 border-b border-gray-200">
            <div className="relative">
              <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
              <input
                ref={searchInputRef}
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search..."
                className="w-full pl-10 pr-3 py-2.5 sm:py-2 border border-gray-300 rounded-md text-base sm:text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent touch-target-small"
              />
            </div>
          </div>

          {/* Select All */}
          {showSelectAll && options.length > 0 && (
            <div className="px-3 py-3 sm:py-2 border-b border-gray-200 bg-gray-50">
              <label className="flex items-center gap-3 sm:gap-2 cursor-pointer hover:bg-gray-100 active:bg-gray-200 rounded px-2 py-2 sm:py-1">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={handleSelectAll}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500 w-4 h-4 flex-shrink-0"
                />
                <span className="text-base sm:text-sm font-medium text-gray-700">
                  Select All ({maxSelections ? `max ${maxSelections}` : options.length})
                </span>
              </label>
              {maxSelections && selectedValues.length >= maxSelections && (
                <p className="text-xs text-amber-600 mt-1 px-2">
                  Maximum {maxSelections} selections reached
                </p>
              )}
            </div>
          )}

          {/* Options list */}
          <div className="max-h-[35vh] sm:max-h-48 overflow-y-auto">
            {filteredOptions.length === 0 ? (
              <div className="px-3 py-2 text-sm text-gray-500">
                No options found
              </div>
            ) : (
              filteredOptions.map((option) => {
                const isSelected = selectedValues.includes(option.value);
                const isDisabled = !isSelected && maxSelections && selectedValues.length >= maxSelections;
                
                return (
                  <label
                    key={option.value}
                    className={`flex items-start gap-3 sm:gap-2 px-3 py-3.5 sm:py-2 cursor-pointer ${
                      isDisabled ? 'opacity-50 cursor-not-allowed' : 'hover:bg-gray-100 active:bg-gray-200'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => handleToggle(option.value)}
                      disabled={isDisabled}
                      className="mt-0.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed w-4 h-4 flex-shrink-0"
                    />
                    <div className="flex flex-col flex-1 min-w-0">
                      <span className="text-base sm:text-sm font-medium text-gray-900 truncate">{option.label}</span>
                      {option.subtitle && (
                        <span className="text-sm sm:text-xs text-gray-500 truncate">
                          {option.subtitle}
                        </span>
                      )}
                    </div>
                  </label>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
