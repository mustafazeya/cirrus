'use client';

import { useState, useMemo, useRef, useCallback, useEffect } from 'react';
import { ChevronDownIcon, ChevronUpIcon, MagnifyingGlassIcon, ArrowDownTrayIcon } from '@heroicons/react/24/outline';
import RowDetailModal from './RowDetailModal';
import toast from 'react-hot-toast';

interface QueryResultsTableProps {
  results: any[];
  maxHeight?: string;
  displayLimit?: number; // Limit for UI display (not for analysis)
}

interface ColumnWidth {
  [key: string]: number;
}

export default function QueryResultsTable({ results, maxHeight = '600px', displayLimit = 500 }: QueryResultsTableProps) {
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  const [expandedCell, setExpandedCell] = useState<{ row: number; col: string } | null>(null);
  const [columnWidths, setColumnWidths] = useState<ColumnWidth>({});
  const [showAllRows, setShowAllRows] = useState(false);
  const [columnFilters, setColumnFilters] = useState<Record<string, Set<string>>>({});
  const [activeFilterColumn, setActiveFilterColumn] = useState<string | null>(null);
  const [filterSearchTerms, setFilterSearchTerms] = useState<Record<string, string>>({});
  const [visibleColumns, setVisibleColumns] = useState<Set<string>>(new Set());
  const [showColumnSelector, setShowColumnSelector] = useState(false);
  
  const activeColumnRef = useRef<string | null>(null);
  const startXRef = useRef<number>(0);
  const startWidthRef = useRef<number>(0);
  const tableRef = useRef<HTMLTableElement>(null);

  if (!results || results.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        No results found
      </div>
    );
  }

  const columns = Object.keys(results[0]);
  
  // Smart column detection: find columns with actual data (Azure Portal style)
  const getColumnsWithData = useCallback(() => {
    const columnsWithData: string[] = [];
    const emptyColumns: string[] = [];
    
    columns.forEach(col => {
      let hasData = false;
      // Check first 100 rows for performance (enough to detect empty columns)
      const sampleSize = Math.min(100, results.length);
      for (let i = 0; i < sampleSize; i++) {
        const val = results[i][col];
        if (val !== null && val !== undefined && val !== '' && val !== 'null') {
          hasData = true;
          break;
        }
      }
      if (hasData) {
        columnsWithData.push(col);
      } else {
        emptyColumns.push(col);
      }
    });
    
    return { columnsWithData, emptyColumns };
  }, [columns, results]);
  
  // Initialize visible columns on first render - show only columns with data
  useEffect(() => {
    if (visibleColumns.size === 0) {
      if (columns.length > 50) {
        // For tables with many columns (like AzureDiagnostics), use smart detection
        const { columnsWithData } = getColumnsWithData();
        console.log(`Smart column detection: ${columnsWithData.length} columns with data out of ${columns.length} total`);
        setVisibleColumns(new Set(columnsWithData));
      } else {
        // For normal tables, show all columns
        setVisibleColumns(new Set(columns));
      }
    }
  }, [columns, visibleColumns.size, getColumnsWithData]);
  
  const totalRows = results.length;
  const hasMoreRows = totalRows > displayLimit;
  const displayedColumns = columns.filter(col => visibleColumns.has(col));

  // Get filtered dataset (excluding the column being filtered for cascading effect)
  const getFilteredDataExcludingColumn = useCallback((excludeColumn: string) => {
    let filtered = results;

    // Apply all column filters EXCEPT the one being filtered
    Object.entries(columnFilters).forEach(([column, selectedValues]) => {
      if (column !== excludeColumn && selectedValues.size > 0) {
        filtered = filtered.filter(row => 
          selectedValues.has(String(row[column] ?? ''))
        );
      }
    });

    // Apply search filter
    if (searchTerm) {
      filtered = filtered.filter(row => 
        Object.values(row).some(value => 
          String(value).toLowerCase().includes(searchTerm.toLowerCase())
        )
      );
    }

    return filtered;
  }, [results, columnFilters, searchTerm]);

  // Lazy calculation: compute unique values from filtered data (cascading filters)
  // Only shows values that exist in the current filtered dataset
  const getColumnUniqueValues = useCallback((column: string): string[] => {
    const values = new Set<string>();
    const filteredData = getFilteredDataExcludingColumn(column);
    
    filteredData.forEach(row => {
      const val = String(row[column] ?? '');
      if (val) values.add(val);
    });
    return Array.from(values).sort().slice(0, 1000);
  }, [getFilteredDataExcludingColumn]);

  // Filter and sort results
  const processedResults = useMemo(() => {
    let filtered = results;

    // Apply column filters
    Object.entries(columnFilters).forEach(([column, selectedValues]) => {
      if (selectedValues.size > 0) {
        filtered = filtered.filter(row => 
          selectedValues.has(String(row[column] ?? ''))
        );
      }
    });

    // Apply search filter
    if (searchTerm) {
      filtered = filtered.filter(row => 
        Object.values(row).some(value => 
          String(value).toLowerCase().includes(searchTerm.toLowerCase())
        )
      );
    }

    // Apply sorting
    if (sortColumn) {
      filtered = [...filtered].sort((a, b) => {
        const aVal = a[sortColumn];
        const bVal = b[sortColumn];
        
        if (aVal === null || aVal === undefined) return 1;
        if (bVal === null || bVal === undefined) return -1;
        
        const comparison = String(aVal).localeCompare(String(bVal), undefined, { numeric: true });
        return sortDirection === 'asc' ? comparison : -comparison;
      });
    }

    // Limit display rows for performance (not for analysis - all data already sent to AI)
    if (!showAllRows && filtered.length > displayLimit) {
      return filtered.slice(0, displayLimit);
    }

    return filtered;
  }, [results, searchTerm, sortColumn, sortDirection, showAllRows, displayLimit, columnFilters]);

  const handleSort = (column: string) => {
    if (sortColumn === column) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortColumn(column);
      setSortDirection('asc');
    }
  };

  const toggleColumnFilter = (column: string, value: string) => {
    setColumnFilters(prev => {
      const newFilters = { ...prev };
      if (!newFilters[column]) {
        newFilters[column] = new Set();
      }
      const filterSet = new Set(newFilters[column]);
      
      if (filterSet.has(value)) {
        filterSet.delete(value);
      } else {
        filterSet.add(value);
      }
      
      if (filterSet.size === 0) {
        delete newFilters[column];
      } else {
        newFilters[column] = filterSet;
      }
      
      return newFilters;
    });
  };

  const clearColumnFilter = (column: string) => {
    setColumnFilters(prev => {
      const newFilters = { ...prev };
      delete newFilters[column];
      return newFilters;
    });
    setActiveFilterColumn(null);
  };

  const selectAllInColumn = (column: string) => {
    const searchTerm = filterSearchTerms[column] || '';
    const allValues = getColumnUniqueValues(column);
    const valuesToSelect = searchTerm
      ? allValues.filter(val => 
          val.toLowerCase().includes(searchTerm.toLowerCase())
        )
      : allValues;
    
    setColumnFilters(prev => {
      const newFilters = { ...prev };
      newFilters[column] = new Set(valuesToSelect);
      return newFilters;
    });
  };

  const toggleColumnVisibility = (column: string) => {
    setVisibleColumns(prev => {
      const newSet = new Set(prev);
      if (newSet.has(column)) {
        newSet.delete(column);
      } else {
        newSet.add(column);
      }
      return newSet;
    });
  };

  const showAllColumns = () => {
    setVisibleColumns(new Set(columns));
  };

  const hideAllColumns = () => {
    setVisibleColumns(new Set());
  };

  const hasActiveFilters = Object.keys(columnFilters).length > 0;

  const onMouseDown = useCallback((column: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    activeColumnRef.current = column;
    startXRef.current = e.clientX;
    startWidthRef.current = columnWidths[column] || 150;
    
    // Add user-select-none to body to prevent text selection while dragging
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
  }, [columnWidths]);

  const onMouseMove = useCallback((e: MouseEvent) => {
    if (!activeColumnRef.current) return;
    
    const diff = e.clientX - startXRef.current;
    const newWidth = Math.max(50, startWidthRef.current + diff);
    
    // Direct DOM manipulation for smooth performance during drag
    const columnCells = document.querySelectorAll(`[data-column="${activeColumnRef.current}"]`);
    columnCells.forEach(cell => {
      (cell as HTMLElement).style.width = `${newWidth}px`;
      (cell as HTMLElement).style.maxWidth = `${newWidth}px`;
    });
  }, []);

  const onMouseUp = useCallback(() => {
    if (activeColumnRef.current) {
      // Update state only once at the end
      const columnCells = document.querySelectorAll(`[data-column="${activeColumnRef.current}"]`);
      if (columnCells.length > 0) {
        const finalWidth = parseInt((columnCells[0] as HTMLElement).style.width);
        setColumnWidths(prev => ({
          ...prev,
          [activeColumnRef.current!]: finalWidth
        }));
      }
    }
    
    activeColumnRef.current = null;
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  }, []);

  // Attach global mouse listeners
  useEffect(() => {
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    
    return () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
  }, [onMouseMove, onMouseUp]);

  const formatCellValue = (value: any): string => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    if (typeof value === 'boolean') return value ? '✓' : '✗';
    return String(value);
  };

  const handleCellClick = (rowIndex: number, colName: string) => {
    if (expandedCell?.row === rowIndex && expandedCell?.col === colName) {
      setExpandedCell(null);
    } else {
      setExpandedCell({ row: rowIndex, col: colName });
    }
  };

  // Export to CSV handler
  const handleExportCSV = () => {
    try {
      if (!processedResults || processedResults.length === 0) {
        toast.error('No data to export');
        return;
      }

      // Use only visible columns
      const headers = displayedColumns;
      const rows = processedResults.map(row => 
        headers.map(header => {
          const value = row[header];
          // Handle null/undefined
          if (value === null || value === undefined) return '';
          // Handle objects/arrays
          if (typeof value === 'object') return JSON.stringify(value);
          // Escape quotes and wrap in quotes if contains comma, quote, or newline
          const stringValue = String(value);
          if (stringValue.includes(',') || stringValue.includes('"') || stringValue.includes('\n')) {
            return `"${stringValue.replace(/"/g, '""')}"`;
          }
          return stringValue;
        })
      );

      const csvContent = [
        headers.join(','),
        ...rows.map(row => row.join(','))
      ].join('\n');

      // Create download
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const link = document.createElement('a');
      const url = URL.createObjectURL(blob);
      link.setAttribute('href', url);
      link.setAttribute('download', `cirrus-ai-results-${new Date().toISOString().split('T')[0]}.csv`);
      link.style.visibility = 'hidden';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      
      toast.success(`Exported ${processedResults.length} rows to CSV`);
    } catch (err) {
      console.error('CSV export error:', err);
      toast.error('Failed to export CSV');
    }
  };

  return (
    <div className="space-y-3">
      {/* Search and Filter Controls */}
      <div className="flex gap-3 items-center">
        {/* Search bar - Modern design */}
        <div className="relative flex-1">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <MagnifyingGlassIcon className="h-4 w-4 text-gray-400" />
          </div>
          <input
            type="text"
            placeholder="Search across all columns..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="block w-full pl-9 pr-3 py-2.5 border border-gray-300 rounded-lg text-sm bg-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
          />
          {searchTerm && (
            <button
              onClick={() => setSearchTerm('')}
              className="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        {/* Column Selector Button */}
        <button
          onClick={() => setShowColumnSelector(!showColumnSelector)}
          className={`px-4 py-2.5 text-sm font-medium border rounded-lg transition-colors flex items-center gap-2 ${
            columns.length > 50 && visibleColumns.size < 30
              ? 'text-orange-700 bg-orange-50 border-orange-300 hover:bg-orange-100'
              : 'text-gray-700 bg-white border-gray-300 hover:bg-gray-50'
          }`}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2" />
          </svg>
          Columns ({visibleColumns.size}/{columns.length})
          {columns.length > 50 && visibleColumns.size < 30 && (
            <span className="ml-1 px-1.5 py-0.5 text-xs font-semibold bg-orange-200 text-orange-800 rounded">
              Many hidden
            </span>
          )}
        </button>

        {/* Export CSV Button */}
        <button
          onClick={handleExportCSV}
          className="px-4 py-2.5 text-sm font-medium text-white bg-gradient-to-br from-indigo-500 to-indigo-700 hover:from-indigo-600 hover:to-indigo-800 rounded-lg transition-colors flex items-center gap-2 shadow-sm"
          title="Export visible data to CSV"
        >
          <ArrowDownTrayIcon className="w-4 h-4" />
          Export CSV
        </button>

        {/* Clear All Filters Button */}
        {hasActiveFilters && (
          <button
            onClick={() => setColumnFilters({})}
            className="px-4 py-2.5 text-sm font-medium text-red-600 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
            Clear Filters
          </button>
        )}
      </div>

      {/* Column Selector Dropdown */}
      {showColumnSelector && (
        <div className="p-4 bg-white border border-gray-300 rounded-lg shadow-lg">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-semibold text-gray-900">Select Columns to Display</h4>
            <div className="flex gap-2">
              <button
                onClick={showAllColumns}
                className="px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50 rounded"
              >
                Show All
              </button>
              <button
                onClick={hideAllColumns}
                className="px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 rounded"
              >
                Hide All
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2 max-h-60 overflow-y-auto">
            {columns.map((column) => (
              <label
                key={column}
                className="flex items-center gap-2 px-3 py-2 hover:bg-gray-50 rounded cursor-pointer text-sm"
              >
                <input
                  type="checkbox"
                  checked={visibleColumns.has(column)}
                  onChange={() => toggleColumnVisibility(column)}
                  className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                />
                <span className="truncate" title={column}>{column}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Active Filters Display */}
      {hasActiveFilters && (
        <div className="flex flex-wrap gap-2 p-3 bg-blue-50 border border-blue-200 rounded-lg">
          <span className="text-xs font-semibold text-blue-900">Active Filters:</span>
          {Object.entries(columnFilters).map(([column, values]) => (
            <div key={column} className="flex items-center gap-1 px-2 py-1 bg-white border border-blue-300 rounded-md text-xs">
              <span className="font-medium text-blue-900">{column}:</span>
              <span className="text-blue-700">{values.size} selected</span>
              <button
                onClick={() => clearColumnFilter(column)}
                className="ml-1 text-blue-600 hover:text-blue-800"
              >
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Results info - Refined */}
      <div className="flex items-center justify-between text-xs text-gray-500 px-1">
        <span className="font-medium">
          {processedResults.length === totalRows 
            ? `${totalRows.toLocaleString()} rows` 
            : `${processedResults.length.toLocaleString()} of ${totalRows.toLocaleString()} rows`}
          {!showAllRows && hasMoreRows && !searchTerm && (
            <span className="ml-2 text-blue-600">
              (displaying first {displayLimit.toLocaleString()})
            </span>
          )}
        </span>
        <div className="flex items-center gap-3">
          {searchTerm && (
            <span className="text-blue-600">
              Filtered by: "{searchTerm}"
            </span>
          )}
          {!showAllRows && hasMoreRows && !searchTerm && (
            <button
              onClick={() => setShowAllRows(true)}
              className="text-blue-600 hover:text-blue-800 font-medium underline"
            >
              Show all {totalRows.toLocaleString()} rows
            </button>
          )}
          {showAllRows && hasMoreRows && (
            <button
              onClick={() => setShowAllRows(false)}
              className="text-gray-600 hover:text-gray-800 font-medium underline"
            >
              Show less
            </button>
          )}
        </div>
      </div>

      {/* Table container - Premium styling */}
      <div 
        className="border border-gray-200 rounded-lg overflow-hidden shadow-sm bg-white"
        style={{ maxHeight }}
      >
        <div className="overflow-auto" style={{ maxHeight }}>
          <table className="w-full divide-y divide-gray-200" style={{ tableLayout: 'auto' }}>
            {/* Header - Modern Azure-style */}
            <thead className="bg-gradient-to-r from-gray-50 to-gray-100 sticky top-0 z-10 border-b border-gray-300">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase tracking-wide bg-gradient-to-r from-gray-50 to-gray-100" style={{ width: 60 }}>
                  <span className="text-gray-400">#</span>
                </th>
                {displayedColumns.map((column) => {
                  const hasFilter = columnFilters[column] && columnFilters[column].size > 0;
                  const filterSearch = filterSearchTerms[column] || '';
                  // Only compute unique values when filter dropdown is open
                  const allValues = activeFilterColumn === column ? getColumnUniqueValues(column) : [];
                  const filteredValues = filterSearch
                    ? allValues.filter(val => 
                        val.toLowerCase().includes(filterSearch.toLowerCase())
                      )
                    : allValues;
                  
                  return (
                  <th
                    key={column}
                    data-column={column}
                    className="relative px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wide bg-gradient-to-r from-gray-50 to-gray-100 group"
                    style={{ 
                      width: columnWidths[column] ? `${columnWidths[column]}px` : '200px',
                      maxWidth: columnWidths[column] ? `${columnWidths[column]}px` : '200px',
                      minWidth: '100px'
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div 
                        className="flex items-center space-x-1.5 cursor-pointer hover:bg-white/60 rounded-md px-2 py-1.5 -mx-2 -my-1.5 transition-colors flex-1"
                        onClick={() => handleSort(column)}
                      >
                        <span className="truncate font-medium" title={column}>
                          {column}
                        </span>
                        <div className="flex-shrink-0">
                          {sortColumn === column ? (
                            sortDirection === 'asc' 
                              ? <ChevronUpIcon className="h-4 w-4 text-blue-600" />
                              : <ChevronDownIcon className="h-4 w-4 text-blue-600" />
                          ) : (
                            <div className="h-4 w-4 opacity-0 group-hover:opacity-40">
                              <ChevronUpIcon className="h-4 w-4 text-gray-400" />
                            </div>
                          )}
                        </div>
                      </div>
                      
                      {/* Filter Button */}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setActiveFilterColumn(activeFilterColumn === column ? null : column);
                        }}
                        className={`flex-shrink-0 p-1 rounded hover:bg-white/80 transition-colors ${
                          hasFilter ? 'text-blue-600' : 'text-gray-400 hover:text-gray-600'
                        }`}
                        title="Filter column"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
                        </svg>
                      </button>
                    </div>

                    {/* Filter Dropdown */}
                    {activeFilterColumn === column && (
                      <div className="absolute top-full left-0 mt-1 w-72 bg-white border border-gray-300 rounded-lg shadow-lg z-50 max-h-96 overflow-hidden flex flex-col">
                        {/* Search within filter */}
                        <div className="p-2 border-b border-gray-200">
                          <div className="relative">
                            <input
                              type="text"
                              placeholder="Search values..."
                              value={filterSearch}
                              onChange={(e) => setFilterSearchTerms(prev => ({
                                ...prev,
                                [column]: e.target.value
                              }))}
                              className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                              onClick={(e) => e.stopPropagation()}
                            />
                            {filterSearch && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setFilterSearchTerms(prev => {
                                    const newTerms = { ...prev };
                                    delete newTerms[column];
                                    return newTerms;
                                  });
                                }}
                                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                              >
                                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                </svg>
                              </button>
                            )}
                          </div>
                        </div>
                        
                        {/* Action buttons */}
                        <div className="p-2 border-b border-gray-200 flex gap-2">
                          <button
                            onClick={() => selectAllInColumn(column)}
                            className="flex-1 px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50 rounded"
                          >
                            Select {filterSearch ? 'Filtered' : 'All'}
                          </button>
                          <button
                            onClick={() => clearColumnFilter(column)}
                            className="flex-1 px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 rounded"
                          >
                            Clear
                          </button>
                        </div>
                        
                        {/* Values list */}
                        <div className="overflow-y-auto max-h-64">
                          {filteredValues.length > 0 ? (
                            filteredValues.map((value) => {
                              const isSelected = columnFilters[column]?.has(value) || false;
                              return (
                                <label
                                  key={value}
                                  className="flex items-center gap-2 px-3 py-2 hover:bg-gray-50 cursor-pointer text-sm"
                                >
                                  <input
                                    type="checkbox"
                                    checked={isSelected}
                                    onChange={() => toggleColumnFilter(column, value)}
                                    className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                                  />
                                  <span className="truncate flex-1" title={value}>{value}</span>
                                </label>
                              );
                            })
                          ) : (
                            <div className="px-3 py-4 text-center text-sm text-gray-500">
                              No matching values
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                    
                    {/* Resize handle */}
                    <div
                      className="absolute right-0 top-0 bottom-0 w-4 cursor-col-resize hover:bg-blue-200/50 flex items-center justify-center transition-colors"
                      onMouseDown={(e) => onMouseDown(column, e)}
                      style={{ zIndex: 20 }}
                    >
                      <div className="w-0.5 h-4 bg-gray-300 group-hover:bg-blue-400" />
                    </div>
                  </th>
                  );
                })}
              </tr>
            </thead>
            
            {/* Body - Clean, readable rows */}
            <tbody className="bg-white divide-y divide-gray-100">
              {processedResults.map((row, rowIndex) => (
                <tr 
                  key={rowIndex}
                  className={`hover:bg-blue-50/50 transition-colors cursor-pointer ${
                    selectedRow === rowIndex ? 'bg-blue-100/60 ring-1 ring-inset ring-blue-300' : 'hover:shadow-sm'
                  }`}
                  onClick={() => setSelectedRow(selectedRow === rowIndex ? null : rowIndex)}
                >
                  <td className="px-4 py-3 text-xs font-medium text-gray-400 text-center border-r border-gray-100">
                    {rowIndex + 1}
                  </td>
                  {displayedColumns.map((column) => {
                    const value = row[column];
                    const formattedValue = formatCellValue(value);
                    const isExpanded = expandedCell?.row === rowIndex && expandedCell?.col === column;
                    const isTruncated = formattedValue.length > 50;

                    return (
                      <td
                        key={column}
                        data-column={column}
                        className="px-4 py-3 text-sm text-gray-800 cursor-pointer"
                        style={{ 
                          width: columnWidths[column] ? `${columnWidths[column]}px` : '200px',
                          maxWidth: columnWidths[column] ? `${columnWidths[column]}px` : '200px',
                          minWidth: '100px',
                          fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif'
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (isTruncated) handleCellClick(rowIndex, column);
                        }}
                        title={isTruncated ? formattedValue : undefined}
                      >
                        <div className={isExpanded ? '' : 'truncate'}>
                          {isExpanded ? (
                            <div className="whitespace-pre-wrap break-words p-3 bg-blue-50 rounded-md border border-blue-200 shadow-sm font-mono text-xs">
                              {formattedValue}
                            </div>
                          ) : (
                            <span className="leading-relaxed">{formattedValue}</span>
                          )}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Expanded row details - Modern card design */}
      {selectedRow !== null && processedResults[selectedRow] && (
        <div className="p-5 bg-gradient-to-br from-blue-50 to-indigo-50 border border-blue-200 rounded-lg shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
              <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Row {selectedRow + 1} Details
            </h4>
            <button
              onClick={() => setSelectedRow(null)}
              className="text-gray-400 hover:text-gray-600 transition-colors"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {columns.map((column) => {
              const value = processedResults[selectedRow][column];
              const formattedValue = formatCellValue(value);
              
              return (
                <div key={column} className="bg-white p-4 rounded-lg border border-gray-200 shadow-sm hover:shadow-md transition-shadow">
                  <div className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-2">
                    {column}
                  </div>
                  <div className="text-sm text-gray-900 break-words leading-relaxed" style={{ fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' }}>
                    {formattedValue || <span className="text-gray-400 italic text-xs">empty</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Row Detail Modal */}
      {selectedRow !== null && processedResults[selectedRow] && (
        <RowDetailModal 
          row={processedResults[selectedRow]} 
          visibleColumns={visibleColumns}
          onClose={() => setSelectedRow(null)} 
        />
      )}
    </div>
  );
}
