'use client';

/**
 * KqlEditor — Monaco editor with KQL syntax highlighting + autocomplete.
 *
 * Registers a custom 'kql' language directly against the CDN Monaco instance
 * via beforeMount — no npm/CDN mismatch, no web worker needed.
 *
 * Provides:
 *  - KQL syntax highlighting (keywords, operators, functions, strings, comments)
 *  - Autocomplete: AZFW table names, column names per table, KQL keywords + functions
 *  - Ctrl+Enter / Cmd+Enter to run (same as Azure Portal)
 */

import { useCallback, useRef } from 'react';
import Editor from '@monaco-editor/react';
import type * as MonacoTypes from 'monaco-editor';

// ── Schema ────────────────────────────────────────────────────────────────────

const TABLES: Record<string, { doc: string; columns: { name: string; type: string }[] }> = {
  AZFWNetworkRule: {
    doc: 'Azure Firewall Network Rule logs',
    columns: [
      { name: 'TimeGenerated',       type: 'datetime' },
      { name: 'Action',              type: 'string'   },
      { name: 'SourceIp',            type: 'string'   },
      { name: 'SourcePort',          type: 'int'      },
      { name: 'DestinationIp',       type: 'string'   },
      { name: 'DestinationPort',     type: 'int'      },
      { name: 'Protocol',            type: 'string'   },
      { name: 'Rule',                type: 'string'   },
      { name: 'RuleCollection',      type: 'string'   },
      { name: 'RuleCollectionGroup', type: 'string'   },
      { name: 'Policy',              type: 'string'   },
      { name: 'ResourceId',          type: 'string'   },
      { name: 'SubscriptionId',      type: 'string'   },
    ],
  },
  AZFWApplicationRule: {
    doc: 'Azure Firewall Application Rule logs',
    columns: [
      { name: 'TimeGenerated',       type: 'datetime' },
      { name: 'Action',              type: 'string'   },
      { name: 'SourceIp',            type: 'string'   },
      { name: 'DestinationPort',     type: 'int'      },
      { name: 'Protocol',            type: 'string'   },
      { name: 'Rule',                type: 'string'   },
      { name: 'RuleCollection',      type: 'string'   },
      { name: 'RuleCollectionGroup', type: 'string'   },
      { name: 'Fqdn',                type: 'string'   },
      { name: 'TargetUrl',           type: 'string'   },
      { name: 'Policy',              type: 'string'   },
      { name: 'ResourceId',          type: 'string'   },
      { name: 'SubscriptionId',      type: 'string'   },
    ],
  },
  AZFWNatRule: {
    doc: 'Azure Firewall NAT Rule logs',
    columns: [
      { name: 'TimeGenerated',       type: 'datetime' },
      { name: 'Action',              type: 'string'   },
      { name: 'SourceIp',            type: 'string'   },
      { name: 'DestinationIp',       type: 'string'   },
      { name: 'DestinationPort',     type: 'int'      },
      { name: 'Protocol',            type: 'string'   },
      { name: 'Rule',                type: 'string'   },
      { name: 'RuleCollection',      type: 'string'   },
      { name: 'RuleCollectionGroup', type: 'string'   },
      { name: 'Policy',              type: 'string'   },
      { name: 'TranslatedIp',        type: 'string'   },
      { name: 'TranslatedPort',      type: 'int'      },
      { name: 'ResourceId',          type: 'string'   },
      { name: 'SubscriptionId',      type: 'string'   },
    ],
  },
  AZFWThreatIntel: {
    doc: 'Azure Firewall Threat Intelligence hits',
    columns: [
      { name: 'TimeGenerated',     type: 'datetime' },
      { name: 'Action',            type: 'string'   },
      { name: 'SourceIp',          type: 'string'   },
      { name: 'DestinationIp',     type: 'string'   },
      { name: 'DestinationPort',   type: 'int'      },
      { name: 'Protocol',          type: 'string'   },
      { name: 'Fqdn',              type: 'string'   },
      { name: 'ThreatDescription', type: 'string'   },
      { name: 'Policy',            type: 'string'   },
      { name: 'ResourceId',        type: 'string'   },
      { name: 'SubscriptionId',    type: 'string'   },
    ],
  },
  AZFWIdpsSignature: {
    doc: 'Azure Firewall IDPS signature hits',
    columns: [
      { name: 'TimeGenerated',   type: 'datetime' },
      { name: 'SignatureId',     type: 'string'   },
      { name: 'Description',     type: 'string'   },
      { name: 'Severity',        type: 'string'   },
      { name: 'Action',          type: 'string'   },
      { name: 'Protocol',        type: 'string'   },
      { name: 'SourceIp',        type: 'string'   },
      { name: 'DestinationIp',   type: 'string'   },
      { name: 'DestinationPort', type: 'int'      },
      { name: 'ResourceId',      type: 'string'   },
    ],
  },
  AZFWDnsQuery: {
    doc: 'Azure Firewall DNS proxy query logs',
    columns: [
      { name: 'TimeGenerated', type: 'datetime' },
      { name: 'SourceIp',      type: 'string'   },
      { name: 'QueryName',     type: 'string'   },
      { name: 'QueryType',     type: 'string'   },
      { name: 'QueryClass',    type: 'string'   },
      { name: 'ResponseCode',  type: 'string'   },
      { name: 'ResourceId',    type: 'string'   },
    ],
  },
  AzureDiagnostics: {
    doc: 'Legacy Azure Diagnostics (pre-structured logs)',
    columns: [
      { name: 'TimeGenerated', type: 'datetime' },
      { name: 'Category',      type: 'string'   },
      { name: 'msg_s',         type: 'string'   },
      { name: 'ResourceId',    type: 'string'   },
      { name: 'OperationName', type: 'string'   },
    ],
  },
};

const KQL_KEYWORDS = [
  'where','summarize','extend','project','join','union','let','order','by',
  'asc','desc','take','limit','top','count','distinct','evaluate','parse',
  'render','mv-expand','print','as','on','kind','inner','outer','left',
  'right','fullouter','isfuzzy','true','false','in','has','contains',
  'startswith','endswith','matches','regex','not','and','or','between',
];

const KQL_FUNCTIONS = [
  'ago','bin','now','datetime','timespan','format_datetime','startofday',
  'startofweek','startofmonth','endofday','endofweek','endofmonth',
  'dcount','dcountif','count','countif','sum','sumif','avg','avgif',
  'min','max','stdev','variance','percentile','any','anyif',
  'arg_min','arg_max','make_set','make_list','make_bag',
  'tostring','toint','tolong','todouble','tobool','todatetime','totimespan',
  'iif','iff','coalesce','isnotempty','isnotnull','isnull','isempty',
  'trim','extract','replace','split','strcat','strlen','toupper','tolower',
  'substring','indexof','parse_json','todynamic','bag_keys','bag_values',
  'ipv4_is_private','ipv4_is_in_range','ipv4_compare',
];

// ── One-time registration flag ────────────────────────────────────────────────
let registered = false;

function registerKqlLanguage(monaco: typeof MonacoTypes) {
  if (registered) return;
  registered = true;

  // Register language
  monaco.languages.register({ id: 'kql', extensions: ['.kql'], aliases: ['KQL', 'kql'] });

  // Monarch tokenizer — syntax highlighting
  monaco.languages.setMonarchTokensProvider('kql', {
    defaultToken: '',
    tokenPostfix: '.kql',
    keywords: KQL_KEYWORDS,
    functions: KQL_FUNCTIONS,
    tables: Object.keys(TABLES),
    operators: ['=', '!=', '<', '<=', '>', '>=', '=~', '!~', 'in~', '!in', '!in~', '|', ',', '.'],
    tokenizer: {
      root: [
        // Line comments
        [/\/\/.*$/, 'comment'],
        // Strings
        [/"([^"\\]|\\.)*$/, 'string.invalid'],
        [/"/, 'string', '@string_double'],
        [/'([^'\\]|\\.)*$/, 'string.invalid'],
        [/'/, 'string', '@string_single'],
        // Numbers
        [/\b\d+(\.\d+)?([eE][+-]?\d+)?\b/, 'number'],
        [/\b(0x[0-9a-fA-F]+)\b/, 'number.hex'],
        // Pipe operator
        [/\|/, 'delimiter.pipe'],
        // Table names (must come before identifier rule)
        [/\bAZFW\w+\b/, 'type.identifier'],
        [/\bAzureDiagnostics\b/, 'type.identifier'],
        // Keywords and functions
        [/[a-zA-Z_][\w-]*/, {
          cases: {
            '@keywords':  'keyword',
            '@functions': 'support.function',
            '@default':   'identifier',
          },
        }],
        // Operators
        [/[=!<>]=?|[|,.]/, 'operator'],
        // Brackets
        [/[()[\]{}]/, '@brackets'],
        // Whitespace
        [/\s+/, 'white'],
      ],
      string_double: [
        [/[^\\"]+/, 'string'],
        [/\\./, 'string.escape'],
        [/"/, 'string', '@pop'],
      ],
      string_single: [
        [/[^\\']+/, 'string'],
        [/\\./, 'string.escape'],
        [/'/, 'string', '@pop'],
      ],
    },
  } as MonacoTypes.languages.IMonarchLanguage);

  // Completion provider
  monaco.languages.registerCompletionItemProvider('kql', {
    triggerCharacters: [' ', '|', '.', '\n'],
    provideCompletionItems(model, position) {
      const textUntil = model.getValueInRange({
        startLineNumber: 1, startColumn: 1,
        endLineNumber: position.lineNumber, endColumn: position.column,
      });
      const wordInfo = model.getWordUntilPosition(position);
      const range: MonacoTypes.IRange = {
        startLineNumber: position.lineNumber,
        endLineNumber:   position.lineNumber,
        startColumn:     wordInfo.startColumn,
        endColumn:       wordInfo.endColumn,
      };

      const suggestions: MonacoTypes.languages.CompletionItem[] = [];
      const CK = monaco.languages.CompletionItemKind;

      // Determine the "active table" — look for the last table name before cursor
      let activeTable: string | null = null;
      for (const tbl of Object.keys(TABLES)) {
        if (textUntil.includes(tbl)) activeTable = tbl;
      }

      // After a pipe — suggest operators / aggregates
      const afterPipe = /\|\s*\w*$/.test(textUntil);
      const pipeKeywords = [
        'where','summarize','extend','project','join','union','order by',
        'take','limit','top','distinct','evaluate','parse','render',
        'mv-expand','count','project-away','project-rename',
      ];

      if (afterPipe) {
        for (const kw of pipeKeywords) {
          suggestions.push({
            label: kw, kind: CK.Keyword,
            insertText: kw + ' ', range,
            detail: 'KQL operator',
          });
        }
      }

      // After "where ", "extend ", "summarize " etc — suggest columns of active table
      const afterFieldOp = /\b(where|extend|summarize|project|order by|by|on)\s+\w*$/.test(textUntil);
      if (afterFieldOp && activeTable) {
        for (const col of TABLES[activeTable].columns) {
          suggestions.push({
            label: col.name, kind: CK.Field,
            insertText: col.name, range,
            detail: `${activeTable} · ${col.type}`,
          });
        }
      }

      // Table names — always suggest when typing at start of line or after union/join
      const atTablePos = /^[\w]*$/.test(wordInfo.word) && (
        /^[\s]*$/.test(textUntil.split('\n').slice(-1)[0].replace(wordInfo.word, '')) ||
        /\b(union|join|from)\s+[\w]*$/.test(textUntil)
      );
      if (atTablePos || wordInfo.word.length > 0) {
        for (const [tbl, meta] of Object.entries(TABLES)) {
          suggestions.push({
            label: tbl, kind: CK.Class,
            insertText: tbl, range,
            detail: meta.doc,
            documentation: meta.doc,
          });
        }
      }

      // Functions — always suggest
      for (const fn of KQL_FUNCTIONS) {
        suggestions.push({
          label: fn, kind: CK.Function,
          insertText: fn.endsWith(')') ? fn : fn + '()',
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          range,
          detail: 'KQL function',
        });
      }

      // Keywords — always suggest
      for (const kw of KQL_KEYWORDS) {
        suggestions.push({
          label: kw, kind: CK.Keyword,
          insertText: kw, range,
          detail: 'KQL keyword',
        });
      }

      return { suggestions, incomplete: false };
    },
  });

  // Basic language config (bracket matching, auto-closing, comments)
  monaco.languages.setLanguageConfiguration('kql', {
    brackets: [['(', ')'], ['[', ']'], ['{', '}']],
    autoClosingPairs: [
      { open: '(', close: ')' },
      { open: '[', close: ']' },
      { open: '{', close: '}' },
      { open: '"', close: '"' },
      { open: "'", close: "'" },
    ],
    comments: { lineComment: '//' },
    wordPattern: /[a-zA-Z_][\w-]*/,
  });
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  value: string;
  onChange: (v: string) => void;
  onRun?: () => void;
  height?: string;
  readOnly?: boolean;
}

export default function KqlEditor({ value, onChange, onRun, height = '180px', readOnly = false }: Props) {
  const onRunRef = useRef(onRun);
  onRunRef.current = onRun;

  const beforeMount = useCallback((monaco: typeof MonacoTypes) => {
    registerKqlLanguage(monaco);
  }, []);

  const handleMount = useCallback((editor: MonacoTypes.editor.IStandaloneCodeEditor, monaco: typeof MonacoTypes) => {
    editor.addCommand(
      monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter,
      () => onRunRef.current?.(),
    );
  }, []);

  return (
    <div className="rounded-lg border border-gray-200 bg-white" style={{ overflow: 'clip' }}>
      <Editor
        height={height}
        language="kql"
        value={value}
        onChange={(v) => onChange(v ?? '')}
        beforeMount={beforeMount}
        onMount={handleMount}
        options={{
          minimap:                    { enabled: false },
          scrollBeyondLastLine:       false,
          fontSize:                   13,
          fontFamily:                 "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, monospace",
          lineNumbers:                'on',
          wordWrap:                   'on',
          automaticLayout:            true,
          suggestOnTriggerCharacters: true,
          quickSuggestions:           { other: true, comments: false, strings: false },
          tabCompletion:              'on',
          readOnly,
          renderLineHighlight:        'line',
          scrollbar:                  { vertical: 'auto', horizontal: 'auto' },
          padding:                    { top: 10, bottom: 10 },          fixedOverflowWidgets:        true,        }}
      />
    </div>
  );
}
