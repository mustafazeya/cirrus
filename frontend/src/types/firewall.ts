// ============================================================
// Azure Firewall Analysis – shared TypeScript types
// ============================================================

export interface FirewallSubscription {
  subscription_id: string;
  display_name: string;
  state: string;
}

export interface FirewallIpConfig {
  name: string;
  public_ip: string;
  private_ip: string | null;
}

export interface AzureFirewall {
  id: string;
  name: string;
  location: string;
  resource_group: string;
  sku_tier: string;
  provisioning_state: string;
  policy_id: string;
  threat_intel_mode: string | null;
  ip_configurations: FirewallIpConfig[];
}

export interface FirewallPolicy {
  id: string;
  name: string;
  resource_group: string;
  location: string;
  sku_tier: string;
  threat_intel_mode: string | null;
  dns_settings: Record<string, unknown> | null;
  intrusion_detection: string | null;
  child_policies: string[];
  base_policy_id: string | null;
}

// A normalised flat rule as returned by the backend
export interface FirewallRule {
  rule_group: string;
  rule_group_priority: number;
  rule_collection: string;
  rule_collection_type: string;
  rule_collection_action: string;
  rule_collection_priority: number;
  name: string;
  rule_type: string; // NetworkRule | ApplicationRule | NatRule
  source_addresses: string[];
  source_ip_groups: string[];
  destination_addresses: string[];
  destination_ip_groups: string[];
  destination_fqdns: string[];
  destination_urls: string[];
  destination_fqdn_tags: string[];
  destination_ports: string[];
  source_ports: string[];
  protocols: string[];
  action: string;
  translated_address: string | null;
  translated_port: string | null;
  translated_fqdn: string | null;
  enabled: boolean;
  policy_name: string;
  resource_group: string;
  subscription_id: string;
  // search result only
  _match_reasons?: string[];
  // orphan result only
  log_hits?: number;
  days_observed?: number;
}

export interface RulesResult {
  total: number;
  network_rules_count: number;
  application_rules_count: number;
  nat_rules_count: number;
  rules: FirewallRule[];
}

export interface SearchQuery {
  source?: string;
  destination?: string;
  port?: string;
  protocol?: string;
  rule_types?: string[];
  action?: string;
  name_filter?: string;
}

export interface SearchResult {
  query: SearchQuery;
  total_scanned: number;
  matches_found: number;
  rules: FirewallRule[];
}

export interface OverlapPair {
  rule1: FirewallRule;
  rule2: FirewallRule;
  rule_category: string;
  severity: 'exact_duplicate' | 'same_action' | 'conflict';
  action_match: boolean;
  source_overlap: string[];
  destination_ip_overlap: string[];
  destination_fqdn_overlap: string[];
  port_overlap: string[];
  recommendation: string;
}

export interface OverlapResult {
  total_rules_scanned: number;
  overlaps_found: number;
  summary: {
    exact_duplicates: number;
    same_action_overlaps: number;
    conflicting_rules: number;
  };
  overlaps: OverlapPair[];
  /** "full" | "exact_duplicates_only" | "partial (CIDR overlap skipped for: …)" */
  analysis_mode?: string;
}

export interface OrphanResult {
  days_observed: number;
  total_rules: number;
  orphan_count: number;
  active_count: number;
  workspace_id: string;
  orphan_rules: FirewallRule[];
  active_rules: FirewallRule[];
  top_rules: FirewallRule[];
  log_available: boolean;
}

export interface RuleLogEntry {
  TimeGenerated: string;
  RuleName: string;
  SourceIP: string;
  DestIP: string;
  DestinationPort: string;
  Protocol: string;
  Action: string;
  [key: string]: string;
}

export interface RuleLogDetail {
  rule_name: string;
  days_observed: number;
  total_hits: number;
  entries: RuleLogEntry[];
}

export interface PolicySummary {
  total_rules: number;
  allow_rules: number;
  deny_rules: number;
  network_rules: number;
  application_rules: number;
  nat_rules: number;
  rule_collection_groups: number;
}

// The user's "analysis context" – what policy they are examining + workspace for log correlation
export interface FirewallContext {
  subscription_id: string;
  subscription_name: string;
  resource_group: string;
  policy_name: string;
  workspace_id?: string;
}

// ── Azure Monitor Metrics ─────────────────────────────────────────────────────

export interface FwMetricDataPoint { t: string; v: number }
export interface FwMetricSeries    { name: string; data: FwMetricDataPoint[] }
export interface FwMetricResult {
  metric:        string;
  aggregation:   string;
  unit:          string;
  interval:      string;
  timespan_start: string;
  timespan_end:   string;
  split_by:      string;
  series:        FwMetricSeries[];
}

/** Static catalogue of every metric exposed by Microsoft.Network/azureFirewalls */
export interface FwMetricDef {
  name:         string;          // Azure metric name
  label:        string;          // human-readable
  description:  string;
  unit:         string;          // Bytes | Percent | Count | Milliseconds | BitsPerSecond | Units
  aggregations: string[];        // supported: Average | Minimum | Maximum | Total
  defaultAgg:   string;
  dimensions:   string[];        // supported dimension names for splitting; [] = none
}

export const FW_METRIC_CATALOGUE: FwMetricDef[] = [
  {
    name: 'NetworkRuleHit',
    label: 'Network Rules Hit',
    description: 'Number of times network rules were matched',
    unit: 'Count',
    aggregations: ['Total'],
    defaultAgg: 'Total',
    dimensions: ['Status', 'Reason'],
  },
  {
    name: 'ApplicationRuleHit',
    label: 'Application Rules Hit',
    description: 'Number of times application rules were matched',
    unit: 'Count',
    aggregations: ['Total'],
    defaultAgg: 'Total',
    dimensions: ['Status', 'Reason', 'Protocol'],
  },
  {
    name: 'DataProcessed',
    label: 'Data Processed',
    description: 'Total data processed by the firewall',
    unit: 'Bytes',
    aggregations: ['Total'],
    defaultAgg: 'Total',
    dimensions: [],
  },
  {
    name: 'FirewallHealth',
    label: 'Firewall Health',
    description: 'Overall health of the firewall (100 = healthy, 50 = degraded, 0 = unhealthy)',
    unit: 'Percent',
    aggregations: ['Average'],
    defaultAgg: 'Average',
    dimensions: ['Status', 'Reason'],
  },
  {
    name: 'FirewallLatencyPng',
    label: 'Firewall Latency (Probe)',
    description: 'Estimated average latency measured by latency probe',
    unit: 'Milliseconds',
    aggregations: ['Average'],
    defaultAgg: 'Average',
    dimensions: [],
  },
  {
    name: 'ObservedCapacity',
    label: 'Observed Capacity Units',
    description: 'Reported capacity units — use to track auto-scaling behaviour',
    unit: 'Units',
    aggregations: ['Average', 'Minimum', 'Maximum'],
    defaultAgg: 'Average',
    dimensions: [],
  },
  {
    name: 'SNATPortUtilization',
    label: 'SNAT Port Utilisation',
    description: 'Percentage of outbound SNAT ports in use (>95% = exhausted)',
    unit: 'Percent',
    aggregations: ['Average', 'Maximum'],
    defaultAgg: 'Average',
    dimensions: ['Protocol'],
  },
  {
    name: 'Throughput',
    label: 'Throughput',
    description: 'Throughput processed by the firewall',
    unit: 'BitsPerSecond',
    aggregations: ['Average'],
    defaultAgg: 'Average',
    dimensions: [],
  },
];

export type AnalysisTab = 'rules' | 'search' | 'overlaps' | 'orphans' | 'traffic' | 'logsearch' | 'metrics' | 'configuration' | 'packetwalk';

export interface PacketWalkStageSource {
  type: string;
  detail: string;
  subnet_cidr?: string;
  vnet_id?: string;
  nsg_id?: string;
  gateway_name?: string;
  gateway_type?: string;
  circuit_name?: string;
}

export interface PacketWalkMatchingRoute {
  prefix: string;
  source: string;
  nextHopType: string;
  nextHopIp: string;
  winning: boolean;
}

export interface PacketWalkStageRoute {
  reaches_firewall: boolean;
  verdict: string;
  winning_prefix?: string;
  route_source?: string;
  next_hop_type?: string;
  next_hop_ip?: string;
  bgp_peer?: string;
  security_flag?: string | null;
  detail: string;
  all_matching_routes?: PacketWalkMatchingRoute[];
}

export interface PacketWalkNearMiss {
  rule?: string;
  collection?: string;
  action?: string;
  reason: string;
}

export interface PacketWalkStageNsg {
  has_nsg: boolean;
  verdict: string;
  matching_rule?: string;
  priority?: number;
  detail: string;
  near_misses?: string[];
}

export interface PacketWalkStageFw {
  verdict: string;
  matching_rule?: string | null;
  rule_collection?: string;
  rule_group?: string;
  rule_type?: string;
  group_priority?: number;
  collection_priority?: number;
  action?: string;
  detail: string;
  partial_source_match?: boolean;
  covered_ranges?: string[];
  near_misses?: PacketWalkNearMiss[];
  partial_coverage_rules?: PacketWalkStageFw[];
}

export interface PacketWalkResult {
  query: { source: string; destination: string; port: string; protocol: string };
  destination_ips: string[];
  firewall_private_ip?: string | null;
  overall_verdict: string;
  overall_detail: string;
  stages: {
    source_resolution: PacketWalkStageSource;
    route_evaluation: PacketWalkStageRoute | null;
    nsg_evaluation: PacketWalkStageNsg | null;
    firewall_evaluation: PacketWalkStageFw | null;
  };
  security_flags: string[];
  ai_analysis: string;
}

export interface FirewallDiagnosticInfo {
  firewall_found: boolean;
  firewall_name: string | null;
  firewall_id: string | null;
  firewall_resource_group: string | null;
  has_diagnostics: boolean;
  primary_workspace: string | null;
  primary_workspace_name: string | null;
  log_mode: 'Dedicated' | 'AzureDiagnostics' | null;
  fallback_workspace: string | null;
  fallback_workspace_name: string | null;
  all_settings: Array<{
    diagnostic_setting_name: string;
    workspace_id: string;
    workspace_name: string;
    mode: string;
    log_categories: string[];
    metric_categories: string[];
  }>;
  message: string;
}

// ── Firewall Configuration ────────────────────────────────────────────────────
export interface FwIpConfig {
  name: string;
  public_ip: string;
  private_ip: string | null;
  subnet: string;
  vnet: string;
}

export interface FwManagementIp {
  name: string;
  public_ip: string;
  private_ip: string | null;
}

export interface FirewallConfiguration {
  // Identity
  name: string;
  location: string;
  resource_group: string;
  subscription_id: string;
  resource_id: string;
  // SKU & capacity
  sku_name: string;
  sku_tier: 'Basic' | 'Standard' | 'Premium';
  max_throughput: string;
  deployment_type: 'VNet' | 'Secured vHub';
  // HA
  availability_zones: string[];
  ha_mode: string;
  // Network
  ip_configurations: FwIpConfig[];
  management_ip: FwManagementIp | null;
  forced_tunneling: boolean;
  virtual_hub: string | null;
  // Policy
  policy_name: string;
  // Threat Intelligence
  threat_intel_mode: 'Alert' | 'Deny' | 'Off';
  threat_intel_whitelist_ips: string[];
  threat_intel_whitelist_fqdns: string[];
  // DNS
  dns_proxy_enabled: boolean;
  dns_servers: string[];
  // Premium features
  idps_mode: string | null;
  fat_flow_logging: boolean;
  flow_trace_logging: boolean;
  // SNAT
  snat_private_ranges: string[];
  // State
  provisioning_state: string;
  // Autoscale / prescaling (null = service default, not configured)
  autoscale_min_capacity: number | null;
  autoscale_max_capacity: number | null;
  // Maintenance configuration (null = not configured)
  maintenance_config: {
    name: string;
    start_date_time: string | null;
    expiration_date_time: string | null;
    duration: string | null;
    time_zone: string | null;
    recur_every: string | null;
  } | null;
  // Resource tags
  tags: Record<string, string>;
  // IDPS (from policy; empty object if not Premium / not configured)
  idps_config: {
    mode: string | null;
    profile: string | null;
    signature_overrides_count: number;
    bypass_rules_count: number;
  } | Record<string, never>;
  // TLS inspection (from policy; empty object if not configured)
  tls_inspection: {
    enabled: boolean;
    ca_name: string | null;
    kv_secret: string | null;
  } | Record<string, never>;
}
export interface TrafficIntelligenceResult {
  days: number;
  workspace_id: string;
  denied_sources:      { Source: string; Count: number }[];
  denied_destinations: { Dest: string; Count: number }[];
  denied_ports:        { Port: string; Protocol: string; Count: number }[];
  deny_trend:          { T: string; Count: number }[];
  top_rules:           { Rule: string; RuleCollection: string; RuleType: string; Count: number }[];
  top_talkers:         { Source: string; Count: number }[];
  east_west:           { internal: number; external: number; total: number };
  threat_intel_hits:   { Fqdn: string; ThreatDescription: string; Action: string; Count: number }[];
  idps_signatures:     { SignatureId: string; Description: string; Severity: string; Action: string; Count: number }[];
  protocol_anomalies:  { Protocol: string; Port: string; Count: number; IsAnomaly: boolean }[];
  denied_combinations: { SourceIp: string; Destination: string; Port: string; Protocol: string; Rule: string; RuleCollection: string; RuleType: string; Count: number }[];
  nat_activity:        { ExternalPort: string; Protocol: string; TranslatedIp: string; TranslatedPort: string; RuleName: string; Collection: string; Count: number; UniqueSources: number; UniqueExternalIPs: number }[];
  nat_top_sources:     { Source: string; Count: number; UniqueDestinations: number }[];
  dns_top_queries:     { QueryName: string; Count: number; UniqueClients: number }[];
  // Real totals — no take cap
  total_deny_events:   number;
  unique_deny_sources: number;
  unique_rules_hit:    number;
  unique_talkers:      number;
}

// ── Log Search ────────────────────────────────────────────────────────────────
export interface LogEntry {
  TimeGenerated: string;
  RuleType: string;
  Action: string;
  SourceIp: string;
  DestinationIp: string;
  DestinationPort: string;
  Protocol: string;
  Rule: string;
  RuleCollection: string;
  RuleCollectionGroup: string;
  TargetUrl: string;
  ThreatDescription: string;
}

export interface LogSearchResult {
  total: number;
  matched: number;
  entries: LogEntry[];
  start_time: string;
  end_time: string;
}

export interface SavedKqlTemplate {
  id: string;
  user_name: string;
  name: string;
  description: string;
  kql: string;
  created_at: string;
}

export interface KqlResult {
  columns: string[];
  rows: unknown[][];
  total: number;
  error: string | null;
  kql_executed: string;
}

export interface KqlJobStatus {
  status: string;
  result?: KqlResult;
}
