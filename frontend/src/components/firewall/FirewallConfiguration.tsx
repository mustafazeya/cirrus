'use client';

import { useState, useEffect } from 'react';
import {
  ShieldCheckIcon,
  GlobeAltIcon,
  ServerStackIcon,
  ExclamationTriangleIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  XCircleIcon,
  InformationCircleIcon,
} from '@heroicons/react/24/outline';
import { apiService } from '../../services/apiService';
import type { FirewallContext, FirewallConfiguration, FirewallDiagnosticInfo } from '../../types/firewall';

interface Props {
  ctx: FirewallContext;
}

// ── Small presentational helpers ─────────────────────────────────────────────

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-3 bg-gray-50 border-b border-gray-100">
        <span className="text-indigo-500">{icon}</span>
        <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
      </div>
      <div className="px-5 py-4">{children}</div>
    </div>
  );
}

function Row({ label, value, mono, badge, badgeColor }: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
  badge?: string;
  badgeColor?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 border-b border-gray-50 last:border-0">
      <span className="text-xs text-gray-500 shrink-0 w-48">{label}</span>
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        <span className={`text-xs text-gray-800 break-all ${mono ? 'font-mono' : ''}`}>{value}</span>
        {badge && (
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${badgeColor ?? 'bg-gray-100 text-gray-600'}`}>
            {badge}
          </span>
        )}
      </div>
    </div>
  );
}

function BoolRow({ label, value, trueLabel = 'Enabled', falseLabel = 'Disabled', warn }: {
  label: string;
  value: boolean;
  trueLabel?: string;
  falseLabel?: string;
  /** When true, "enabled" is the undesirable state — shown in amber instead of green */
  warn?: boolean;
}) {
  const colorClass = value
    ? (warn ? 'text-amber-600' : 'text-emerald-600')
    : 'text-gray-400';
  return (
    <div className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0">
      <span className="text-xs text-gray-500">{label}</span>
      <span className={`flex items-center gap-1 text-xs font-medium ${colorClass}`}>
        {value
          ? <CheckCircleIcon className="w-3.5 h-3.5" />
          : <XCircleIcon className="w-3.5 h-3.5" />}
        {value ? trueLabel : falseLabel}
      </span>
    </div>
  );
}

function SkuBadge({ tier }: { tier: string }) {
  const colors: Record<string, string> = {
    Basic:    'bg-gray-100 text-gray-700',
    Standard: 'bg-blue-100 text-blue-700',
    Premium:  'bg-purple-100 text-purple-700',
  };
  return (
    <span className={`text-xs px-2.5 py-0.5 rounded-full font-semibold ${colors[tier] ?? 'bg-gray-100 text-gray-600'}`}>
      {tier}
    </span>
  );
}

function ThreatBadge({ mode }: { mode: string }) {
  if (mode === 'Deny')  return <span className="text-xs px-2.5 py-0.5 rounded-full font-medium bg-red-100 text-red-700">Deny (blocking)</span>;
  if (mode === 'Alert') return <span className="text-xs px-2.5 py-0.5 rounded-full font-medium bg-amber-100 text-amber-700">Alert only</span>;
  return <span className="text-xs px-2.5 py-0.5 rounded-full font-medium bg-gray-100 text-gray-500">Off</span>;
}

function ZoneBadges({ zones }: { zones: string[] }) {
  if (!zones.length)
    return <span className="text-xs text-amber-600 flex items-center gap-1"><ExclamationTriangleIcon className="w-3.5 h-3.5" />No zone redundancy — single datacenter risk</span>;
  return (
    <span className="flex gap-1">
      {['1', '2', '3'].map((z) => (
        <span key={z} className={`text-xs w-6 h-6 flex items-center justify-center rounded-full font-semibold ${zones.includes(z) ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-300'}`}>{z}</span>
      ))}
    </span>
  );
}

function ProvisioningBadge({ state }: { state: string }) {
  if (state === 'Succeeded') return <span className="text-xs px-2.5 py-0.5 rounded-full bg-emerald-100 text-emerald-700 font-medium">{state}</span>;
  if (state === 'Failed')    return <span className="text-xs px-2.5 py-0.5 rounded-full bg-red-100 text-red-700 font-medium">{state}</span>;
  return <span className="text-xs px-2.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">{state}</span>;
}

// ── Main component ────────────────────────────────────────────────────────────

export default function FirewallConfiguration({ ctx }: Props) {
  const [config, setConfig] = useState<FirewallConfiguration | null>(null);
  const [diagInfo, setDiagInfo] = useState<FirewallDiagnosticInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    apiService.firewallGetDiagnosticInfo(ctx.subscription_id, ctx.resource_group, ctx.policy_name)
      .then(async (res) => {
        const info = res;
        setDiagInfo(info);
        if (!info.firewall_found || !info.firewall_name) return;
        const fwRg = info.firewall_resource_group ?? ctx.resource_group;
        const cfg = await apiService.firewallGetConfiguration(ctx.subscription_id, fwRg, info.firewall_name);
        setConfig(cfg as FirewallConfiguration);
      })
      .catch(() => setError('Failed to load firewall configuration.'))
      .finally(() => setLoading(false));
  }, [ctx.subscription_id, ctx.resource_group, ctx.policy_name]);

  const fwName = diagInfo?.firewall_name;

  if (!fwName) {
    return (
      <div className="flex items-center gap-2 py-12 text-gray-400 text-sm justify-center">
        <InformationCircleIcon className="w-5 h-5" />
        No Azure Firewall instance was found linked to this policy.
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-12 text-gray-400 text-sm justify-center">
        <ArrowPathIcon className="w-4 h-4 animate-spin" /> Loading configuration…
      </div>
    );
  }

  if (error || !config) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-5 text-sm text-red-700">
        {error ?? 'No configuration data returned.'}
      </div>
    );
  }

  const snatIPs = config.ip_configurations.map((ip) => ip.public_ip).filter(Boolean);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-5">

      {/* ── Identity & SKU ── */}
      <Section title="Identity & SKU" icon={<ShieldCheckIcon className="w-4 h-4" />}>
        <Row label="Firewall name"       value={config.name} />
        <Row label="SKU"                 value={<SkuBadge tier={config.sku_tier} />} />
        <Row label="Location"            value={config.location} />
        <Row label="Resource group"      value={config.resource_group} />
        <Row label="Provisioning state"  value={<ProvisioningBadge state={config.provisioning_state} />} />
        <Row label="Linked policy"       value={config.policy_name || ctx.policy_name} />
        {config.virtual_hub && <Row label="Secured vHub" value={config.virtual_hub} />}
      </Section>

      {/* ── High Availability ── */}
      <Section title="High Availability" icon={<ServerStackIcon className="w-4 h-4" />}>
        <Row label="Availability zones"  value={<ZoneBadges zones={config.availability_zones} />} />
        <Row label="HA mode"             value={config.ha_mode} />
      </Section>

      {/* ── SNAT / IP Configuration ── */}
      <Section title="SNAT & Public IP Configuration" icon={<GlobeAltIcon className="w-4 h-4" />}>
        {config.ip_configurations.length === 0 ? (
          <p className="text-xs text-gray-400 italic">No IP configurations found.</p>
        ) : config.ip_configurations.map((ip, i) => (
          <div key={i} className="mb-3 last:mb-0 p-3 bg-gray-50 rounded-lg space-y-1">
            <p className="text-xs font-semibold text-gray-600 mb-1">{ip.name}</p>
            {ip.public_ip  && <Row label="SNAT Public IP"  value={ip.public_ip}  mono />}
            {ip.private_ip && <Row label="Private IP"      value={ip.private_ip} mono />}
            {ip.vnet       && <Row label="VNet"            value={ip.vnet} />}
            {ip.subnet     && <Row label="Subnet"          value={ip.subnet} />}
          </div>
        ))}
        {config.snat_private_ranges.length > 0 && (
          <div className="mt-2">
            <p className="text-xs font-medium text-gray-500 mb-1">SNAT private ranges (no-SNAT)</p>
            <div className="flex flex-wrap gap-1">
              {config.snat_private_ranges.map((r) => (
                <span key={r} className="font-mono text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded">{r}</span>
              ))}
            </div>
          </div>
        )}
        <div className="mt-3 p-3 bg-blue-50 border border-blue-100 rounded-lg text-xs text-blue-700">
          <strong>SNAT ports:</strong> Each public IP provides ~64K SNAT ports. Current public IPs: <strong>{snatIPs.length}</strong>. Add IPs if SNAT port utilisation exceeds 95% (firewall health degrades). Max 250K parallel connections per firewall instance.
        </div>
      </Section>

      {/* ── Logging ── */}
      <Section title="Logging" icon={<InformationCircleIcon className="w-4 h-4" />}>
        {(() => {
          const active = (diagInfo?.all_settings ?? []).filter(s => s.log_categories.length > 0);
          const isEnabled = active.length > 0;
          return (
            <>
              <BoolRow label="Logging" value={isEnabled} trueLabel="Enabled" falseLabel="Disabled" />
              {isEnabled && (
                <div className="mt-2 space-y-2">
                  {active.map((s, i) => (
                    <div key={i} className="p-3 bg-gray-50 rounded-lg border border-gray-100">
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-xs font-semibold text-gray-700 truncate">{s.workspace_name}</span>
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium shrink-0 ml-2 ${
                          s.mode === 'Dedicated' ? 'bg-blue-100 text-blue-700' : 'bg-orange-100 text-orange-700'
                        }`}>{s.mode}</span>
                      </div>
                      <p className="text-xs text-gray-400 font-mono truncate mb-1.5">{s.diagnostic_setting_name}</p>
                      <div className="flex flex-wrap gap-1">
                        {s.log_categories.map((cat) => (
                          <span key={cat} className="text-xs bg-white border border-gray-200 text-gray-600 px-1.5 py-0.5 rounded">{cat}</span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          );
        })()}
      </Section>

      {/* ── DNS ── */}
      <Section title="DNS Proxy (from Firewall Policy)" icon={<GlobeAltIcon className="w-4 h-4" />}>
        <BoolRow label="DNS proxy" value={config.dns_proxy_enabled} />
        {config.dns_servers.length > 0 ? (
          <div className="mt-2">
            <p className="text-xs font-medium text-gray-500 mb-1">Custom DNS servers</p>
            <div className="flex flex-wrap gap-1">
              {config.dns_servers.map((s) => (
                <span key={s} className="font-mono text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded">{s}</span>
              ))}
            </div>
          </div>
        ) : (
          <Row label="DNS servers" value="Azure default (168.63.129.16)" mono />
        )}
        {!config.dns_proxy_enabled && (
          <div className="mt-3 p-2 bg-amber-50 border border-amber-100 rounded text-xs text-amber-700">
            DNS proxy is disabled. Without DNS proxy, FQDN-based application rules may not filter correctly if clients use different DNS servers.
          </div>
        )}
      </Section>

      {/* ── Scaling & Prescaling ── */}
      <Section title="Scaling & Prescaling" icon={<ServerStackIcon className="w-4 h-4" />}>
        <Row label="Max throughput (SKU ceiling)" value={config.max_throughput} />
        {config.sku_tier === 'Basic' ? (
          <div className="mt-2 p-2 bg-amber-50 border border-amber-100 rounded text-xs text-amber-700">
            Basic SKU uses a fixed 2-instance backend — no auto-scale. Max 250 Mbps.
          </div>
        ) : (
          <>
            <Row
              label="Prescaled min capacity"
              value={config.autoscale_min_capacity != null
                ? `${config.autoscale_min_capacity} scale units`
                : 'Not configured (service default)'}
            />
            <Row
              label="Prescaled max capacity"
              value={config.autoscale_max_capacity != null
                ? `${config.autoscale_max_capacity} scale units`
                : 'Not configured (service default)'}
            />
            {config.autoscale_min_capacity == null && (
              <div className="mt-2 p-2 bg-amber-50 border border-amber-100 rounded text-xs text-amber-700">
                No capacity reservation configured. The firewall starts at minimum scale and auto-scales on demand (takes 5–7 min). Configure <strong>minCapacity</strong> to pre-warm instances for predictable traffic spikes.
              </div>
            )}
          </>
        )}
      </Section>

      {/* ── Maintenance Configuration ── */}
      <Section title="Customer-Controlled Maintenance" icon={<ServerStackIcon className="w-4 h-4" />}>
        {config.maintenance_config ? (
          <>
            <Row label="Config name"    value={config.maintenance_config.name} />
            <Row label="Schedule"       value={config.maintenance_config.recur_every ?? '—'} />
            <Row label="Window duration" value={config.maintenance_config.duration ?? '—'} />
            <Row label="Time zone"      value={config.maintenance_config.time_zone ?? '—'} />
            <Row label="Start date"     value={config.maintenance_config.start_date_time ?? '—'} />
            {config.maintenance_config.expiration_date_time && config.maintenance_config.expiration_date_time !== '9999-12-31 00:00' && (
              <Row label="Expires" value={config.maintenance_config.expiration_date_time} />
            )}
            <div className="mt-2 p-2 bg-emerald-50 border border-emerald-100 rounded text-xs text-emerald-700">
              Maintenance window is configured. Azure Firewall upgrades (OS and service) will be scheduled within this window. Minimum 5-hour daily window required.
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-gray-400 italic py-1">No customer-controlled maintenance window configured.</p>
            <div className="mt-2 p-2 bg-amber-50 border border-amber-100 rounded text-xs text-amber-700">
              Without a maintenance window, Azure schedules upgrades automatically (typically off-hours). Configure a maintenance window if your workloads require persistent connections (e.g. SAP, Azure Virtual Desktop).
            </div>
          </>
        )}
      </Section>
      {/* ── Forced Tunnelling ── */}
      <Section title="Forced Tunnelling & Management NIC" icon={<ServerStackIcon className="w-4 h-4" />}>
        <BoolRow label="Forced tunnelling" value={config.forced_tunneling} trueLabel="Enabled" falseLabel="Disabled" />
        {config.management_ip ? (
          <div className="mt-2 p-3 bg-gray-50 rounded-lg space-y-1">
            <p className="text-xs font-semibold text-gray-600 mb-1">Management NIC</p>
            <Row label="Public IP (mgmt)" value={config.management_ip.public_ip} mono />
            {config.management_ip.private_ip && <Row label="Private IP (mgmt)" value={config.management_ip.private_ip} mono />}
          </div>
        ) : (
          <p className="text-xs text-gray-400 mt-2 italic">No management NIC — firewall has direct internet connectivity.</p>
        )}
        <div className="mt-3 p-3 bg-gray-50 border border-gray-100 rounded-lg text-xs text-gray-500">
          Forced tunnelling routes all traffic through an NVA or on-premises. The management NIC ensures Azure Firewall operational traffic always has internet access.
        </div>
      </Section>

      {/* ── Threat Intelligence ── */}
      <Section title="Threat Intelligence" icon={<ExclamationTriangleIcon className="w-4 h-4" />}>
        <Row label="Mode" value={<ThreatBadge mode={config.threat_intel_mode} />} />
        {config.threat_intel_mode === 'Alert' && (
          <div className="mt-2 p-2 bg-amber-50 border border-amber-100 rounded text-xs text-amber-700">
            Consider switching to <strong>Deny</strong> mode to actively block known malicious traffic (updated in real time from Microsoft Cyber Security).
          </div>
        )}
        {config.threat_intel_whitelist_ips.length > 0 && (
          <div className="mt-2">
            <p className="text-xs font-medium text-gray-500 mb-1">Whitelisted IPs</p>
            <div className="flex flex-wrap gap-1">
              {config.threat_intel_whitelist_ips.map((ip) => (
                <span key={ip} className="font-mono text-xs bg-red-50 text-red-700 px-2 py-0.5 rounded">{ip}</span>
              ))}
            </div>
          </div>
        )}
        {config.threat_intel_whitelist_fqdns.length > 0 && (
          <div className="mt-2">
            <p className="text-xs font-medium text-gray-500 mb-1">Whitelisted FQDNs</p>
            <div className="flex flex-wrap gap-1">
              {config.threat_intel_whitelist_fqdns.map((fqdn) => (
                <span key={fqdn} className="font-mono text-xs bg-red-50 text-red-700 px-2 py-0.5 rounded">{fqdn}</span>
              ))}
            </div>
          </div>
        )}
        {config.idps_mode && (
          <Row
            label="IDPS mode (Premium)"
            value={config.idps_mode}
            badge="Premium"
            badgeColor="bg-purple-100 text-purple-700"
          />
        )}
      </Section>

      {/* ── TLS Inspection & IDPS (from policy) ── */}
      <Section title="TLS Inspection & IDPS" icon={<ShieldCheckIcon className="w-4 h-4" />}>
        {(() => {
          const idps = config.idps_config as { mode?: string | null };
          const tls  = config.tls_inspection as { enabled?: boolean };
          const idpsMode = idps?.mode ?? 'Off';
          const idpsColor = idpsMode === 'Deny' ? 'text-emerald-600' : idpsMode === 'Alert' ? 'text-amber-600' : 'text-gray-400';
          return (
            <>
              <BoolRow label="TLS inspection" value={!!tls?.enabled} trueLabel="Enabled" falseLabel="Disabled" />
              <div className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0">
                <span className="text-xs text-gray-500">IDPS</span>
                <span className={`text-xs font-medium ${idpsColor}`}>{idpsMode}</span>
              </div>
            </>
          );
        })()}
      </Section>
    </div>
  );
}
