'use client';

import { PlusIcon, TrashIcon, MagnifyingGlassIcon, XMarkIcon } from '@heroicons/react/24/outline';

export interface SearchCondition {
  id: string;
  field: string;
  operator: string;
  value: string;
}

export interface SearchConditionGroup {
  id: string;
  conditions: SearchCondition[];
  intraConnector: 'AND' | 'OR';
  interConnector: 'AND' | 'OR';
}

export interface SearchQuery {
  groups: SearchConditionGroup[];
}

interface Props {
  onSearch: (query: SearchQuery) => void;
  onClear: () => void;
  loading?: boolean;
  groups: SearchConditionGroup[];
  onGroupsChange: (groups: SearchConditionGroup[]) => void;
}

const FIELDS = [
  { value: 'source',      label: 'Source' },
  { value: 'destination', label: 'Destination IP' },
  { value: 'fqdn',        label: 'FQDN / URL' },
  { value: 'port',        label: 'Port' },
  { value: 'protocol',    label: 'Protocol' },
  { value: 'action',      label: 'Action' },
  { value: 'rule_type',   label: 'Rule Type' },
  { value: 'name',        label: 'Rule Name' },
  { value: 'collection',  label: 'Rule Collection' },
  { value: 'group',       label: 'Rule Group' },
];

const OPERATORS = [
  { value: 'contains',     label: 'contains' },
  { value: 'not_contains', label: 'does not contain' },
  { value: 'equals',       label: 'equals' },
  { value: 'not_equals',   label: 'not equals' },
  { value: 'starts_with',  label: 'starts with' },
  { value: 'ends_with',    label: 'ends with' },
];

const IP_FIELDS = new Set(['source', 'destination']);
const DROPDOWN_FIELDS = new Set(['protocol', 'action', 'rule_type']);
const IP_OPERATORS       = OPERATORS.filter(o => ['equals','not_equals','starts_with','ends_with'].includes(o.value));
const DROPDOWN_OPERATORS = OPERATORS.filter(o => ['equals','not_equals'].includes(o.value));

function operatorsFor(field: string) {
  if (DROPDOWN_FIELDS.has(field)) return DROPDOWN_OPERATORS;
  if (IP_FIELDS.has(field))       return IP_OPERATORS;
  return OPERATORS;
}

const VALUE_HINTS: Record<string, string[]> = {
  protocol:  ['TCP', 'UDP', 'ICMP', 'HTTPS', 'HTTP', 'Any'],
  action:    ['Allow', 'Deny'],
  rule_type: ['NetworkRule', 'ApplicationRule', 'NatRule'],
};

function newCond(): SearchCondition {
  return { id: crypto.randomUUID(), field: 'source', operator: 'contains', value: '' };
}
export function newGroup(): SearchConditionGroup {
  return { id: crypto.randomUUID(), conditions: [newCond()], intraConnector: 'AND', interConnector: 'AND' };
}

export default function RuleSearchBuilder({ onSearch, onClear, loading, groups, onGroupsChange }: Props) {
  const addGroup = () => onGroupsChange([...groups, newGroup()]);
  const removeGroup = (gid: string) => onGroupsChange(groups.filter(x => x.id !== gid));
  const setIntra = (gid: string, v: 'AND' | 'OR') =>
    onGroupsChange(groups.map(x => x.id === gid ? { ...x, intraConnector: v } : x));
  const setInter = (gid: string, v: 'AND' | 'OR') =>
    onGroupsChange(groups.map(x => x.id === gid ? { ...x, interConnector: v } : x));

  const addCond = (gid: string) =>
    onGroupsChange(groups.map(x => x.id === gid ? { ...x, conditions: [...x.conditions, newCond()] } : x));
  const removeCond = (gid: string, cid: string) =>
    onGroupsChange(groups.map(x => x.id === gid ? { ...x, conditions: x.conditions.filter(c => c.id !== cid) } : x));
  const updateCond = (gid: string, cid: string, patch: Partial<SearchCondition>) =>
    onGroupsChange(groups.map(x => x.id === gid
      ? { ...x, conditions: x.conditions.map(c => c.id === cid ? { ...c, ...patch } : c) }
      : x));

  const clear = () => { onGroupsChange([newGroup()]); onClear(); };

  const canSearch = groups.some(g => g.conditions.some(c => c.value.trim()));

  const submit = () => {
    if (!canSearch) return;
    onSearch({ groups });
  };

  const buildPreview = (): string => {
    const activeGroups = groups
      .map(g => ({ ...g, conditions: g.conditions.filter(c => c.value.trim()) }))
      .filter(g => g.conditions.length > 0);
    if (activeGroups.length < 2 && (activeGroups[0]?.conditions.length ?? 0) < 2) return '';
    const fl = (f: string) => FIELDS.find(x => x.value === f)?.label ?? f;
    const ol = (o: string) => OPERATORS.find(x => x.value === o)?.label ?? o;
    const groupExprs = activeGroups.map(g => {
      const parts = g.conditions.map(c => `${fl(c.field)} ${ol(c.operator)} "${c.value}"`);
      return parts.length === 1 ? parts[0] : `(${parts.join(` ${g.intraConnector} `)})`;
    });
    if (groupExprs.length === 1) return groupExprs[0];
    let r = groupExprs[0];
    for (let i = 1; i < groupExprs.length; i++) {
      r = `${r} ${activeGroups[i - 1].interConnector} ${groupExprs[i]}`;
    }
    return r;
  };
  const preview = buildPreview();

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <MagnifyingGlassIcon className="w-4 h-4 text-gray-500" />
        <span className="text-sm font-semibold text-gray-800">Firewall Rule Search</span>
      </div>

      {/* Group-based condition builder */}
      <div className="space-y-2">
        {groups.map((group, gIdx) => (
          <div key={group.id}>
            <div className="border border-gray-200 rounded-xl p-3 space-y-2 bg-gray-50/40">
              {/* Group header */}
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-400 font-medium">Group {gIdx + 1}</span>
                <div className="flex items-center gap-0.5 bg-gray-100 rounded-lg p-0.5">
                  {(['AND', 'OR'] as const).map(l => (
                    <button key={l} onClick={() => setIntra(group.id, l)}
                      className={`px-2 py-0.5 text-xs font-semibold rounded-md transition-colors ${
                        group.intraConnector === l
                          ? l === 'AND' ? 'bg-indigo-500 text-white' : 'bg-orange-500 text-white'
                          : 'text-gray-500 hover:text-gray-700'
                      }`}>{l}</button>
                  ))}
                </div>
                <span className="text-xs text-gray-400">{group.intraConnector === 'AND' ? 'all must match' : 'any must match'}</span>
                {groups.length > 1 && (
                  <button onClick={() => removeGroup(group.id)}
                    className="ml-auto p-1 text-gray-400 hover:text-red-500 rounded hover:bg-red-50 transition-colors">
                    <TrashIcon className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>

              {/* Conditions */}
              {group.conditions.map((cond, cIdx) => (
                <div key={cond.id} className="flex items-center gap-2 flex-wrap">
                  {cIdx === 0
                    ? <span className="text-xs font-medium text-gray-400 w-10 text-right shrink-0">If</span>
                    : <span className={`text-xs font-semibold w-10 text-right shrink-0 ${group.intraConnector === 'AND' ? 'text-indigo-600' : 'text-orange-500'}`}>
                        {group.intraConnector}
                      </span>
                  }
                  <select value={cond.field} onChange={e => {
                      const f = e.target.value;
                      const op = !operatorsFor(f).find(o => o.value === cond.operator) ? 'equals' : cond.operator;
                      updateCond(group.id, cond.id, { field: f, value: '', operator: op });
                    }}
                    className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                    {FIELDS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                  </select>
                  <select value={cond.operator} onChange={e => updateCond(group.id, cond.id, { operator: e.target.value })}
                    className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                    {operatorsFor(cond.field).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  {VALUE_HINTS[cond.field] ? (
                    <select value={cond.value} onChange={e => updateCond(group.id, cond.id, { value: e.target.value })}
                      className="flex-1 min-w-[140px] text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                      <option value="">— select —</option>
                      {VALUE_HINTS[cond.field].map(v => <option key={v} value={v}>{v}</option>)}
                    </select>
                  ) : (
                    <input type="text" placeholder={fieldPlaceholder(cond.field)} value={cond.value}
                      onChange={e => updateCond(group.id, cond.id, { value: e.target.value })}
                      onKeyDown={e => e.key === 'Enter' && canSearch && submit()}
                      className="flex-1 min-w-[160px] text-sm border border-gray-300 rounded-lg px-2 py-1.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                  )}
                  {group.conditions.length > 1 && (
                    <button onClick={() => removeCond(group.id, cond.id)}
                      className="p-1.5 text-gray-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors">
                      <TrashIcon className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              ))}
              <button onClick={() => addCond(group.id)}
                className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 font-medium">
                <PlusIcon className="w-3.5 h-3.5" /> Add condition
              </button>
            </div>

            {/* Inter-group connector */}
            {gIdx < groups.length - 1 && (
              <div className="flex justify-center my-1">
                <div className="flex items-center gap-0.5 bg-white rounded-lg p-0.5 border border-gray-200 shadow-sm">
                  {(['AND', 'OR'] as const).map(l => (
                    <button key={l} onClick={() => setInter(group.id, l)}
                      className={`px-2.5 py-0.5 text-xs font-semibold rounded-md transition-colors ${
                        group.interConnector === l
                          ? l === 'AND' ? 'bg-indigo-500 text-white' : 'bg-orange-500 text-white'
                          : 'text-gray-500 hover:text-gray-700'
                      }`}>{l}</button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1 flex-wrap">
        <button onClick={addGroup}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 font-medium border border-dashed border-gray-300 rounded-lg px-2 py-1 hover:border-gray-400 transition-colors">
          <PlusIcon className="w-3.5 h-3.5" /> Add group
        </button>
        {preview && (
          <div className="w-full mt-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg font-mono text-xs text-gray-500 break-all">
            <span className="text-gray-400 mr-1.5 select-none">where</span>{preview}
          </div>
        )}
        <div className="flex-1" />
        <button onClick={clear}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
          <XMarkIcon className="w-3.5 h-3.5" /> Clear
        </button>
        <button onClick={submit} disabled={!canSearch || loading}
          className="flex items-center gap-1.5 px-4 py-1.5 text-sm bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-medium">
          <MagnifyingGlassIcon className="w-3.5 h-3.5" />{loading ? 'Searching…' : 'Search'}
        </button>
      </div>
    </div>
  );
}

function fieldPlaceholder(field: string): string {
  const map: Record<string, string> = {
    name:        'e.g. prod-allow',
    source:      'e.g. 10.33.0.0/16',
    destination: 'e.g. 10.44.0.5',
    fqdn:        'e.g. *.microsoft.com',
    port:        'e.g. 443',
    collection:  'e.g. from-chkp',
    group:       'e.g. checkpoint-rcg',
  };
  return map[field] ?? '';
}
