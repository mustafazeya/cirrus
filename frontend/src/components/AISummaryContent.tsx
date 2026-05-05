'use client';

import React from 'react';

interface AISummaryContentProps {
  content: string;
}

export default function AISummaryContent({ content }: AISummaryContentProps) {
  // Parse the markdown-style content into structured sections with nested lists and tables
  const parseContent = (text: string) => {
    const lines = text.split('\n');
    const sections: Array<{ 
      type: string; 
      content: string; 
      items?: Array<{ text: string; indent: number }>;
      tableData?: { headers: string[]; rows: string[][] };
    }> = [];
    let currentSection: { 
      type: string; 
      content: string; 
      items?: Array<{ text: string; indent: number }>;
      tableData?: { headers: string[]; rows: string[][] };
    } | null = null;

    lines.forEach((line) => {
      const trimmed = line.trim();
      const leadingSpaces = line.length - line.trimStart().length;
      
      // Table row detection (starts with |)
      if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
        const cells = trimmed.split('|').map(c => c.trim()).filter(c => c.length > 0);
        
        // Check if this is a separator row (|---|---|)
        const isSeparator = cells.every(cell => /^[-:]+$/.test(cell));
        
        if (isSeparator) {
          // Skip separator rows
          return;
        }
        
        if (currentSection && currentSection.type === 'table') {
          // Add row to existing table
          if (!currentSection.tableData) {
            currentSection.tableData = { headers: cells, rows: [] };
          } else {
            currentSection.tableData.rows.push(cells);
          }
        } else {
          // Start new table
          if (currentSection) sections.push(currentSection);
          currentSection = { 
            type: 'table', 
            content: '', 
            tableData: { headers: cells, rows: [] }
          };
        }
        return;
      }
      
      // If we were building a table and hit non-table content, close the table
      if (currentSection && currentSection.type === 'table' && !trimmed.startsWith('|')) {
        sections.push(currentSection);
        currentSection = null;
      }
      
      // Heading (## or **heading**)
      if (trimmed.startsWith('##')) {
        if (currentSection) sections.push(currentSection);
        currentSection = { type: 'heading', content: trimmed.replace(/^##\s*/, '') };
      }
      // Bold standalone line (acts as subheading)
      else if (trimmed.match(/^\*\*[^*]+\*\*:?\s*$/)) {
        if (currentSection) sections.push(currentSection);
        currentSection = { type: 'subheading', content: trimmed.replace(/\*\*/g, '').replace(/:$/, '') };
      }
      // Bullet point (check for indentation)
      else if (trimmed.startsWith('•') || trimmed.startsWith('-') || trimmed.startsWith('*')) {
        const item = trimmed.replace(/^[•\-*]\s*/, '');
        // Calculate indent level: 0, 2, 4, 6 spaces = levels 0, 1, 2, 3
        const indent = Math.floor(leadingSpaces / 2);
        
        if (currentSection && currentSection.type === 'list') {
          currentSection.items = currentSection.items || [];
          currentSection.items.push({ text: item, indent });
        } else {
          if (currentSection) sections.push(currentSection);
          currentSection = { type: 'list', content: '', items: [{ text: item, indent }] };
        }
      }
      // Numbered list
      else if (trimmed.match(/^\d+\./)) {
        const item = trimmed.replace(/^\d+\.\s*/, '');
        const indent = Math.floor(leadingSpaces / 2);
        
        if (currentSection && currentSection.type === 'numbered') {
          currentSection.items = currentSection.items || [];
          currentSection.items.push({ text: item, indent });
        } else {
          if (currentSection) sections.push(currentSection);
          currentSection = { type: 'numbered', content: '', items: [{ text: item, indent }] };
        }
      }
      // Regular paragraph
      else if (trimmed.length > 0) {
        if (currentSection && currentSection.type === 'paragraph') {
          currentSection.content += ' ' + trimmed;
        } else {
          if (currentSection) sections.push(currentSection);
          currentSection = { type: 'paragraph', content: trimmed };
        }
      }
    });

    if (currentSection) sections.push(currentSection);
    return sections;
  };

  // Format inline markdown (bold, code)
  const formatInline = (text: string) => {
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    
    // Match **bold**, `code`, and regular text
    const regex = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let match;

    while ((match = regex.exec(text)) !== null) {
      // Add text before match
      if (match.index > lastIndex) {
        parts.push(text.substring(lastIndex, match.index));
      }

      const matched = match[0];
      if (matched.startsWith('**')) {
        // Bold text
        parts.push(
          <span key={match.index} className="font-semibold text-gray-900">
            {matched.slice(2, -2)}
          </span>
        );
      } else if (matched.startsWith('`')) {
        // Code text
        parts.push(
          <code key={match.index} className="font-mono text-xs bg-slate-100 text-slate-800 px-1.5 py-0.5 rounded">
            {matched.slice(1, -1)}
          </code>
        );
      }

      lastIndex = match.index + matched.length;
    }

    // Add remaining text
    if (lastIndex < text.length) {
      parts.push(text.substring(lastIndex));
    }

    return parts.length > 0 ? parts : text;
  };

  // Get icon for section based on heading content
  const getSectionIcon = (heading: string) => {
    const lower = heading.toLowerCase();
    
    if (lower.includes('summary') || lower.includes('overview')) {
      return (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
        </svg>
      );
    } else if (lower.includes('key') || lower.includes('finding') || lower.includes('insight')) {
      return (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
        </svg>
      );
    } else if (lower.includes('recommendation') || lower.includes('action') || lower.includes('next step')) {
      return (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      );
    } else if (lower.includes('detail') || lower.includes('breakdown') || lower.includes('analysis')) {
      return (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
      );
    }
    
    return (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    );
  };

  const sections = parseContent(content);

  return (
    <div className="space-y-6" style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif' }}>
      {sections.map((section, index) => {
        switch (section.type) {
          case 'heading':
            return (
              <div key={index} className="group mt-8 first:mt-0">
                <div className="flex items-center gap-3 mb-4">
                  <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white shadow-sm">
                    {getSectionIcon(section.content)}
                  </div>
                  <h3 className="text-xl font-semibold text-gray-900">
                    {formatInline(section.content)}
                  </h3>
                </div>
              </div>
            );

          case 'subheading':
            return (
              <div key={index} className="mt-6 mb-3">
                <h4 className="text-lg font-semibold text-gray-800">
                  {formatInline(section.content)}
                </h4>
              </div>
            );

          case 'paragraph':
            return (
              <p key={index} className="text-base text-gray-700 leading-relaxed mb-4">
                {formatInline(section.content)}
              </p>
            );

          case 'list':
            return (
              <ul key={index} className="space-y-3 mb-5">
                {section.items?.map((item, i) => {
                  const isNested = item.indent > 0;
                  
                  return (
                    <li 
                      key={i} 
                      className={`flex items-start gap-4 text-base leading-relaxed ${
                        isNested ? 'text-gray-600' : 'text-gray-700'
                      }`}
                      style={{ marginLeft: item.indent * 28 + 'px' }}
                    >
                      <span className={`flex-shrink-0 mt-2 ${
                        item.indent === 0 ? 'w-2 h-2 rounded-full bg-blue-500' :
                        item.indent === 1 ? 'w-1.5 h-1.5 rounded-full bg-gray-400' :
                        'w-1 h-1 rounded-full bg-gray-300'
                      }`}></span>
                      <span className="flex-1">{formatInline(item.text)}</span>
                    </li>
                  );
                })}
              </ul>
            );

          case 'numbered':
            return (
              <ol key={index} className="space-y-3 mb-5">
                {section.items?.map((item, i) => {
                  const isNested = item.indent > 0;
                  
                  return (
                    <li 
                      key={i} 
                      className={`flex items-start gap-4 text-base leading-relaxed ${
                        isNested ? 'text-gray-600' : 'text-gray-700'
                      }`}
                      style={{ marginLeft: item.indent * 28 + 'px' }}
                    >
                      <span className={`flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center text-sm font-semibold ${
                        item.indent === 0 ? 'bg-blue-100 text-blue-700' :
                        item.indent === 1 ? 'bg-gray-100 text-gray-600' :
                        'bg-gray-50 text-gray-500'
                      }`}>
                        {i + 1}
                      </span>
                      <span className="flex-1 mt-0.5">{formatInline(item.text)}</span>
                    </li>
                  );
                })}
              </ol>
            );

          case 'table':
            return (
              <div key={index} className="overflow-hidden rounded-lg border border-gray-200 shadow-sm my-4">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gradient-to-r from-gray-50 to-gray-100">
                    <tr>
                      {section.tableData?.headers.map((header, i) => (
                        <th
                          key={i}
                          className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider"
                        >
                          {formatInline(header)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-100">
                    {section.tableData?.rows.map((row, rowIndex) => (
                      <tr key={rowIndex} className="hover:bg-blue-50/40 transition-colors">
                        {row.map((cell, cellIndex) => (
                          <td
                            key={cellIndex}
                            className="px-4 py-3 text-sm text-gray-700"
                          >
                            {formatInline(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );

          default:
            return null;
        }
      })}
    </div>
  );
}
