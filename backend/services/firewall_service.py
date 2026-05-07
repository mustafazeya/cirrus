"""
Azure Firewall Analysis Service
Provides comprehensive firewall rule inspection, overlap detection, orphan detection
and log correlation using the service principal credential.

Capabilities:
  - Fetch all firewall policies and rule collection groups across subscriptions
  - Search rules by source/destination IP or CIDR (contained-in matching)
  - Detect overlapping / duplicate rules (network, application, NAT)
  - Detect orphan rules with zero log hits for a configurable window
  - Resolve IP groups to individual CIDRs
"""

import asyncio
import ipaddress
import logging
import re
import time
from config import settings
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from azure.core.exceptions import HttpResponseError

logger = logging.getLogger(__name__)

_MANAGEMENT_API = "https://management.azure.com"
_NETWORK_API_VERSION = "2024-05-01"
_MONITOR_API_VERSION = "2021-06-01"

# ---------------------------------------------------------------------------
# Module-level flatten cache — shared across all requests, TTL 10 min.
# Keyed by "subscription_id:resource_group:policy_name".
# ---------------------------------------------------------------------------
_FLATTEN_CACHE: Dict[str, Tuple[List[Dict], float]] = {}
_FLATTEN_CACHE_TTL = settings.FIREWALL_FLATTEN_CACHE_TTL  # sourced from config.py


def clear_flatten_cache() -> None:
    """Evict the entire flatten cache. Call after any data-affecting bug fix or policy change."""
    _FLATTEN_CACHE.clear()
    logger.info("flatten_cache cleared")

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ip_network_safe(cidr: str):
    """Return ip_network or None for invalid CIDRs / wildcards."""
    if not cidr or cidr == "*":
        return None
    try:
        return ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return None


def _cidr_overlaps(a: str, b: str) -> bool:
    """True if the two CIDRs overlap at all (either direction)."""
    na, nb = _ip_network_safe(a), _ip_network_safe(b)
    if na is None or nb is None:
        return a == b  # wildcard match
    return na.overlaps(nb)


def _cidr_contained_in(candidate: str, container: str) -> bool:
    """True if *candidate* is fully contained inside *container*."""
    n_cand = _ip_network_safe(candidate)
    n_cont = _ip_network_safe(container)
    if n_cand is None or n_cont is None:
        return candidate == container or container == "*"
    try:
        return n_cand.subnet_of(n_cont) or n_cand == n_cont
    except TypeError:  # different IP versions
        return False


def _port_overlaps(ports_a: List[str], ports_b: List[str]) -> bool:
    """True if the two port lists share at least one common port/range."""
    if not ports_a or not ports_b:
        return False
    if "*" in ports_a or "*" in ports_b:
        return True
    set_a = _expand_ports(ports_a)
    set_b = _expand_ports(ports_b)
    return bool(set_a & set_b)


def _expand_ports(ports: List[str]) -> set:
    """Expand port ranges like '80-90' into a flat set of ints."""
    result: set = set()
    for p in ports:
        p = str(p).strip()
        if p == "*":
            return {-1}  # sentinel for wildcard
        if "-" in p:
            try:
                lo, hi = p.split("-", 1)
                result.update(range(int(lo), int(hi) + 1))
            except ValueError:
                pass
        else:
            try:
                result.add(int(p))
            except ValueError:
                pass
    return result


def _fqdn_overlaps(fqdns_a: List[str], fqdns_b: List[str]) -> bool:
    if not fqdns_a or not fqdns_b:
        return False
    for a in fqdns_a:
        for b in fqdns_b:
            if a.lstrip("*.") == b.lstrip("*.") or a == b or a == "*" or b == "*":
                return True
    return False


def _protocol_overlaps(protos_a: List[str], protos_b: List[str]) -> bool:
    if not protos_a or not protos_b:
        return False
    norm = lambda lst: {p.upper() for p in lst}
    a_set, b_set = norm(protos_a), norm(protos_b)
    return bool(a_set & b_set) or "ANY" in a_set or "ANY" in b_set


# ---------------------------------------------------------------------------

def _parse_addr_intervals(
    addresses: List[str],
) -> Tuple[bool, List[Tuple[int, int]], frozenset]:
    """
    Parse a list of address strings into integer intervals for fast CIDR overlap.
    Returns: (is_wildcard, [(start_int, end_int), ...], frozenset_of_service_tags)
    is_wildcard=True means the set matches any address (* / Any / 0.0.0.0/0).
    Calling this ONCE per rule moves all ipaddress parsing out of the hot loop.
    """
    if not addresses:
        # Empty list means no addresses specified — no match, not wildcard.
        return False, [], frozenset()
    ivs: List[Tuple[int, int]] = []
    tags: List[str] = []
    for addr in addresses:
        if addr in ("*", "Any", "any", "0.0.0.0/0", "::/0"):
            return True, [(0, 0xFFFFFFFF)], frozenset()
        net = _ip_network_safe(addr)
        if net is not None and isinstance(net, ipaddress.IPv4Network):
            ivs.append((int(net.network_address), int(net.broadcast_address)))
        else:
            tags.append(addr.strip())
    return False, ivs, frozenset(tags)


def _ivs_overlap(
    w1: bool, ivs1: List[Tuple[int, int]], t1: frozenset,
    w2: bool, ivs2: List[Tuple[int, int]], t2: frozenset,
) -> bool:
    """
    Pure integer CIDR overlap check.  No ipaddress objects in the hot path.
    Two IPv4 intervals [s1, e1] and [s2, e2] overlap iff s1 <= e2 and s2 <= e1.
    """
    if w1 or w2:
        return True
    if t1 and t2 and (t1 & t2):     # same service tag on both sides
        return True
    for s1, e1 in ivs1:
        for s2, e2 in ivs2:
            if s1 <= e2 and s2 <= e1:
                return True
    return False


def _dst_bucket(addr: str) -> str:
    """
    Bucket a destination address by its /24 supernet.
    Broad or wildcard destinations (prefix < /24) map to "*".
    Service tags / FQDNs map to their lowercase value (own bucket).
    """
    if addr in ("*", "Any", "any", "0.0.0.0/0", "::/0"):
        return "*"
    net = _ip_network_safe(addr)
    if net is not None and isinstance(net, ipaddress.IPv4Network):
        if net.prefixlen < 24:
            return "*"
        return str(net.supernet(new_prefix=24).network_address)
    return addr.lower()


def _fmt_addr_overlap(
    w1: bool, w2: bool, addrs1: List[str], addrs2: List[str],
) -> List[str]:
    """Format all overlapping address pairs for display — no truncation."""
    if w1 or w2:
        return ["* (wildcard)"]
    common = set(addrs1) & set(addrs2)
    if common:
        return [f"{a} \u2194 {a}" for a in sorted(common)]
    pairs: List[str] = []
    for a in addrs1:
        for b in addrs2:
            if a != b and _cidr_overlaps(a, b):
                pairs.append(f"{a} \u2194 {b}")
    return pairs or ["CIDR overlap"]

# network calls (sync helpers – run in executor)
# ---------------------------------------------------------------------------

def _sync_get(url: str, token: str) -> Optional[Dict]:
    resp = requests.get(url, headers=_make_headers(token), timeout=30)
    if resp.status_code == 200:
        return resp.json()
    logger.warning("GET %s returned %s", url, resp.status_code)
    return None


def _sync_get_paginated(url: str, token: str) -> List[Dict]:
    """Follow @nextLink pages."""
    results: List[Dict] = []
    next_url: Optional[str] = url
    while next_url:
        data = _sync_get(next_url, token)
        if data is None:
            break
        results.extend(data.get("value", []))
        next_url = data.get("nextLink")
    return results


# ---------------------------------------------------------------------------
# FirewallService
# ---------------------------------------------------------------------------

class FirewallService:
    """
    Service that uses the service principal credential to call Azure Firewall/Network APIs.
    All public methods are async-safe (synchronous Azure calls are run in a thread executor).
    """

    def __init__(self):
        from services.dual_credential_service import DualCredentialService
        self._dual_cred = DualCredentialService()
        self._token_cache: Dict[str, Tuple[str, float]] = {}

    # ------------------------------------------------------------------
    # token management
    # ------------------------------------------------------------------

    def _get_sp_token(self) -> str:
        import time
        cached = self._token_cache.get("management")
        if cached:
            token_val, expiry = cached
            if time.time() < expiry - 60:
                return token_val
        cred = self._dual_cred.get_service_principal_credential()
        token_obj = cred.get_token("https://management.azure.com/.default")
        self._token_cache["management"] = (token_obj.token, token_obj.expires_on)
        return token_obj.token

    def _get_sp_la_token(self) -> str:
        """Get a service principal token scoped to Log Analytics query API."""
        import time
        cached = self._token_cache.get("loganalytics")
        if cached:
            token_val, expiry = cached
            if time.time() < expiry - 60:
                return token_val
        cred = self._dual_cred.get_service_principal_credential()
        token_obj = cred.get_token("https://api.loganalytics.io/.default")
        self._token_cache["loganalytics"] = (token_obj.token, token_obj.expires_on)
        return token_obj.token

    # ------------------------------------------------------------------
    # subscription / firewall discovery
    # ------------------------------------------------------------------

    async def list_subscriptions(self, has_policies: bool = False) -> List[Dict]:
        """List all accessible subscriptions via the service principal.

        Args:
            has_policies: When True, only return subscriptions that contain at
                          least one Azure Firewall Policy resource.
        """
        token = await asyncio.to_thread(self._get_sp_token)
        url = f"{_MANAGEMENT_API}/subscriptions?api-version=2022-12-01"
        subs = await asyncio.to_thread(_sync_get_paginated, url, token)
        all_subs = [
            {
                "subscription_id": s.get("subscriptionId"),
                "display_name": s.get("displayName"),
                "state": s.get("state"),
            }
            for s in subs
        ]

        if not has_policies:
            return all_subs

        # Use Azure Resource Graph to find subscription IDs that have at least
        # one Microsoft.Network/firewallPolicies resource.
        arg_url = f"{_MANAGEMENT_API}/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01"
        arg_body = {
            "query": (
                "Resources "
                "| where type =~ 'microsoft.network/firewallpolicies' "
                "| distinct subscriptionId"
            )
        }
        try:
            resp = await asyncio.to_thread(
                requests.post,
                arg_url,
                json=arg_body,
                headers=_make_headers(token),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("data", {})
            policy_sub_ids: set = set()
            if isinstance(raw, list):
                # data is a list of dicts, each with a "subscriptionId" key
                policy_sub_ids = {
                    row.get("subscriptionId", "").lower()
                    for row in raw
                    if isinstance(row, dict)
                }
            elif isinstance(raw, dict):
                # data has {"columns": [...], "rows": [[val, ...], ...]}
                columns = [c.get("name") for c in raw.get("columns", [])]
                try:
                    sub_col_idx = columns.index("subscriptionId")
                except ValueError:
                    sub_col_idx = None
                if sub_col_idx is not None:
                    policy_sub_ids = {
                        row[sub_col_idx].lower()
                        for row in raw.get("rows", [])
                        if isinstance(row, list) and len(row) > sub_col_idx
                    }
        except Exception as exc:
            logger.warning("ARG query for subscriptions-with-policies failed: %s – returning all subs", exc)
            return all_subs

        return [s for s in all_subs if (s["subscription_id"] or "").lower() in policy_sub_ids]

    async def list_firewalls(self, subscription_id: str) -> List[Dict]:
        """List Azure Firewalls in a subscription."""
        token = await asyncio.to_thread(self._get_sp_token)
        url = (
            f"{_MANAGEMENT_API}/subscriptions/{subscription_id}"
            f"/providers/Microsoft.Network/azureFirewalls?api-version={_NETWORK_API_VERSION}"
        )
        firewalls = await asyncio.to_thread(_sync_get_paginated, url, token)
        result = []
        for fw in firewalls:
            props = fw.get("properties", {})
            policy_ref = props.get("firewallPolicy", {}).get("id", "")
            result.append(
                {
                    "id": fw.get("id"),
                    "name": fw.get("name"),
                    "location": fw.get("location"),
                    "resource_group": fw.get("id", "").split("/resourceGroups/")[-1].split("/")[0],
                    "sku_tier": props.get("sku", {}).get("tier", "Unknown"),
                    "provisioning_state": props.get("provisioningState"),
                    "policy_id": policy_ref,
                    "threat_intel_mode": props.get("threatIntelMode"),
                    "ip_configurations": [
                        {
                            "name": ipc.get("name"),
                            "public_ip": ipc.get("properties", {}).get("publicIPAddress", {}).get("id", "").split("/")[-1],
                            "private_ip": ipc.get("properties", {}).get("privateIPAddress"),
                        }
                        for ipc in props.get("ipConfigurations", [])
                    ],
                }
            )
        return result

    async def list_firewall_policies(self, subscription_id: str) -> List[Dict]:
        """List firewall policies in a subscription."""
        token = await asyncio.to_thread(self._get_sp_token)
        url = (
            f"{_MANAGEMENT_API}/subscriptions/{subscription_id}"
            f"/providers/Microsoft.Network/firewallPolicies?api-version={_NETWORK_API_VERSION}"
        )
        policies = await asyncio.to_thread(_sync_get_paginated, url, token)
        return [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "resource_group": p.get("id", "").split("/resourceGroups/")[-1].split("/")[0],
                "location": p.get("location"),
                "sku_tier": p.get("properties", {}).get("sku", {}).get("tier", "Standard"),
                "threat_intel_mode": p.get("properties", {}).get("threatIntelMode"),
                "dns_settings": p.get("properties", {}).get("dnsSettings"),
                "intrusion_detection": p.get("properties", {}).get("intrusionDetection", {}).get("mode"),
                "child_policies": [c.get("id") for c in p.get("properties", {}).get("childPolicies", [])],
                "base_policy_id": p.get("properties", {}).get("basePolicy", {}).get("id"),
            }
            for p in policies
        ]

    # ------------------------------------------------------------------
    # IP Groups
    # ------------------------------------------------------------------

    async def resolve_ip_group(self, ip_group_resource_id: str) -> List[str]:
        """Resolve an IP group resource ID to a flat list of CIDRs."""
        token = await asyncio.to_thread(self._get_sp_token)
        url = f"{_MANAGEMENT_API}{ip_group_resource_id}?api-version={_NETWORK_API_VERSION}"
        data = await asyncio.to_thread(_sync_get, url, token)
        if data:
            return data.get("properties", {}).get("ipAddresses", [])
        return []

    # ------------------------------------------------------------------
    # Rule collection groups
    # ------------------------------------------------------------------

    async def get_rule_collection_groups(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
    ) -> List[Dict]:
        token = await asyncio.to_thread(self._get_sp_token)
        url = (
            f"{_MANAGEMENT_API}/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Network/firewallPolicies/{policy_name}"
            f"/ruleCollectionGroups?api-version={_NETWORK_API_VERSION}"
        )
        groups = await asyncio.to_thread(_sync_get_paginated, url, token)
        return groups

    async def _resolve_ip_group_with_token(self, ip_group_resource_id: str, token: str) -> List[str]:
        """Resolve a single IP group using a pre-fetched token (used for parallel gather)."""
        url = f"{_MANAGEMENT_API}{ip_group_resource_id}?api-version={_NETWORK_API_VERSION}"
        data = await asyncio.to_thread(_sync_get, url, token)
        if data:
            return data.get("properties", {}).get("ipAddresses", [])
        return []

    async def _flatten_rules(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
        resolve_ip_groups: bool = True,
    ) -> List[Dict]:
        """
        Return a fully-normalised flat list of rules from all rule collection groups.
        Expands IP groups into concrete CIDRs.

        Results are cached for _FLATTEN_CACHE_TTL seconds to avoid re-fetching on
        every tab switch (rules → overlaps → orphans all call this).
        """
        cache_key = f"{subscription_id}:{resource_group}:{policy_name}:{resolve_ip_groups}"
        cached = _FLATTEN_CACHE.get(cache_key)
        if cached:
            rules, ts = cached
            if time.monotonic() - ts < _FLATTEN_CACHE_TTL:
                logger.info("flatten_rules cache hit for %s/%s", resource_group, policy_name)
                return rules

        groups = await self.get_rule_collection_groups(subscription_id, resource_group, policy_name)
        all_rules: List[Dict] = []

        # ── Parallel IP group resolution ─────────────────────────────────
        # Collect every unique IP group ID referenced across all rules first,
        # then resolve them all in one asyncio.gather instead of serially.
        unique_ig_ids: set = set()
        if resolve_ip_groups:
            for group in groups:
                for collection in group.get("properties", {}).get("ruleCollections", []):
                    for rule in collection.get("rules", []):
                        unique_ig_ids.update(rule.get("sourceIpGroups", []))
                        unique_ig_ids.update(rule.get("destinationIpGroups", []))

        ip_group_cache: Dict[str, List[str]] = {}
        if unique_ig_ids:
            token = await asyncio.to_thread(self._get_sp_token)
            resolved = await asyncio.gather(
                *[self._resolve_ip_group_with_token(ig_id, token) for ig_id in unique_ig_ids],
                return_exceptions=True,
            )
            for ig_id, result in zip(unique_ig_ids, resolved):
                ip_group_cache[ig_id] = result if not isinstance(result, Exception) else []

        def _get_ip_group(ig_id: str) -> List[str]:
            return ip_group_cache.get(ig_id, [])

        for group in groups:
            group_name = group.get("name", "")
            priority = group.get("properties", {}).get("priority", 0)
            for collection in group.get("properties", {}).get("ruleCollections", []):
                c_name = collection.get("name", "")
                c_type = collection.get("ruleCollectionType", "")
                c_action = collection.get("action", {}).get("type", "Unknown")
                c_priority = collection.get("priority", 0)

                for rule in collection.get("rules", []):
                    rtype = rule.get("ruleType", "")
                    name = rule.get("name", "")

                    # ---------- source resolution ----------
                    src_addrs: List[str] = list(rule.get("sourceAddresses", []))
                    if resolve_ip_groups:
                        for ig_id in rule.get("sourceIpGroups", []):
                            src_addrs.extend(_get_ip_group(ig_id))
                    src_ip_groups = rule.get("sourceIpGroups", [])

                    # ---------- destination resolution ----------
                    # API field names differ by rule type:
                    #   Network / NAT rules → destinationAddresses + destinationFqdns
                    #   Application rules   → destinationAddresses + targetFqdns + targetUrls
                    rtype_lower = rtype.lower()
                    dst_addrs: List[str] = list(rule.get("destinationAddresses", []))
                    if "application" in rtype_lower:
                        dst_fqdns: List[str] = list(rule.get("targetFqdns", []))
                        dst_urls: List[str] = list(rule.get("targetUrls", []))
                    else:
                        # Network and NAT rules use destinationFqdns
                        dst_fqdns = list(rule.get("destinationFqdns", []))
                        dst_urls = []
                    dst_tags: List[str] = list(rule.get("destinationFqdnTags", []))
                    if resolve_ip_groups:
                        for ig_id in rule.get("destinationIpGroups", []):
                            dst_addrs.extend(_get_ip_group(ig_id))
                    dst_ip_groups = rule.get("destinationIpGroups", [])

                    # ---------- ports & protocols ----------
                    dst_ports: List[str] = [str(p) for p in rule.get("destinationPorts", [])]
                    src_ports: List[str] = [str(p) for p in rule.get("sourcePorts", [])]
                    protocols: List[str] = []
                    for proto in rule.get("protocols", []):
                        if isinstance(proto, dict):
                            protocols.append(proto.get("protocolType", "Any"))
                        else:
                            protocols.append(str(proto))
                    if not protocols and rule.get("ipProtocols"):
                        protocols = [str(p) for p in rule.get("ipProtocols", [])]

                    # ---------- NAT specifics ----------
                    translated_address = rule.get("translatedAddress")
                    translated_port = rule.get("translatedPort")
                    translated_fqdn = rule.get("translatedFqdn")

                    # ---------- termination ----------
                    is_enabled = rule.get("ruleConditionType") != "Disabled"

                    all_rules.append(
                        {
                            "rule_group": group_name,
                            "rule_group_priority": priority,
                            "rule_collection": c_name,
                            "rule_collection_type": c_type,
                            "rule_collection_action": c_action,
                            "rule_collection_priority": c_priority,
                            "name": name,
                            "rule_type": rtype,
                            "source_addresses": src_addrs,
                            "source_ip_groups": src_ip_groups,
                            "destination_addresses": dst_addrs,
                            "destination_ip_groups": dst_ip_groups,
                            "destination_fqdns": dst_fqdns,
                            "destination_urls": dst_urls,
                            "destination_fqdn_tags": dst_tags,
                            "destination_ports": dst_ports,
                            "source_ports": src_ports,
                            "protocols": protocols,
                            "action": c_action,
                            "translated_address": translated_address,
                            "translated_port": translated_port,
                            "translated_fqdn": translated_fqdn,
                            "enabled": is_enabled,
                            "policy_name": policy_name,
                            "resource_group": resource_group,
                            "subscription_id": subscription_id,
                        }
                    )

        _FLATTEN_CACHE[cache_key] = (all_rules, time.monotonic())
        logger.info(
            "flatten_rules cached %d rules for %s/%s",
            len(all_rules), resource_group, policy_name,
        )
        return all_rules

    def invalidate_flatten_cache(self, subscription_id: str, resource_group: str, policy_name: str) -> None:
        """Evict cached rules for a specific policy (call after mutations)."""
        for suffix in ("True", "False"):
            _FLATTEN_CACHE.pop(f"{subscription_id}:{resource_group}:{policy_name}:{suffix}", None)

    # ------------------------------------------------------------------
    # Public: get all rules
    # ------------------------------------------------------------------

    async def get_all_rules(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
        rule_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Return all rules for a policy, optionally filtered by rule type.
        rule_types: None = all, or list of ["NetworkRule","ApplicationRule","NatRule"]
        """
        rules = await self._flatten_rules(subscription_id, resource_group, policy_name)

        if rule_types:
            rt_set = {r.lower() for r in rule_types}
            rules = [r for r in rules if r["rule_type"].lower().replace(" ", "") in rt_set or any(rt in r["rule_type"].lower() for rt in rt_set)]

        network_rules = [r for r in rules if "network" in r["rule_type"].lower()]
        app_rules = [r for r in rules if "application" in r["rule_type"].lower()]
        nat_rules = [r for r in rules if "nat" in r["rule_type"].lower()]

        return {
            "total": len(rules),
            "network_rules_count": len(network_rules),
            "application_rules_count": len(app_rules),
            "nat_rules_count": len(nat_rules),
            "rules": rules,
        }

    # ------------------------------------------------------------------
    # Public: search rules
    # ------------------------------------------------------------------


    async def search_rules_advanced(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
        groups: List[Dict],
    ) -> Dict[str, Any]:
        """
        Group-based rule search.

        Each group: {conditions: [{field, operator, value}], intra_connector, inter_connector}
        Within a group, conditions are combined by intra_connector (AND|OR).
        Groups are combined by each group's inter_connector (AND|OR) left-to-right.

        Supports arbitrary boolean expressions:
          A and (B or C)        → Group1:[A] AND Group2:[B,C](intra=OR)
          (A and B) or (C and D)→ Group1:[A,B](AND) OR Group2:[C,D](AND)
          A or (B and C and D)  → Group1:[A] OR Group2:[B,C,D](AND)
        """
        rules = await self._flatten_rules(subscription_id, resource_group, policy_name)
        matched: List[Dict] = []

        def _field_values(rule: Dict, field: str) -> List[str]:
            f = field.lower()
            if f == "name":
                return [rule["name"]]
            if f == "source":
                return rule["source_addresses"] + rule.get("source_ip_groups", [])
            if f == "destination":
                return rule["destination_addresses"] + rule.get("destination_ip_groups", [])
            if f == "fqdn":
                return rule["destination_fqdns"] + rule["destination_urls"] + rule.get("destination_fqdn_tags", [])
            if f == "port":
                return rule["destination_ports"]
            if f == "protocol":
                return rule["protocols"]
            if f == "action":
                return [rule["action"]]
            if f == "rule_type":
                return [rule["rule_type"]]
            if f == "collection":
                return [rule["rule_collection"]]
            if f == "group":
                return [rule["rule_group"]]
            return []

        def _apply_operator(values: List[str], operator: str, value: str, field: str) -> bool:
            op = operator.lower()
            v = value.lower()
            is_addr_field = field.lower() in ("source", "destination")
            is_negation = op in ("not_contains", "not_equals")

            def _positive_match(fv: str) -> bool:
                fvl = fv.lower()
                if is_addr_field:
                    if op == "is_exactly":
                        # bare IP and /32 are identical
                        v_norm = v[:-3] if v.endswith('/32') else v
                        fvl_norm = fvl[:-3] if fvl.endswith('/32') else fvl
                        return fvl_norm == v_norm
                    # 'matches' and 'equals' both do subnet-aware matching
                    if v in fvl or fvl == v:
                        return True
                    try:
                        return _cidr_contained_in(value, fv)
                    except Exception:
                        return False
                else:
                    if op in ("not_contains", "contains"):
                        return v in fvl
                    return fvl == v

            def _string_match(fv: str) -> bool:
                fvl = fv.lower()
                if op in ("starts_with", "not_contains", "contains"):
                    if op == "starts_with":
                        return fvl.startswith(v)
                    return v in fvl
                if op == "ends_with":
                    return fvl.endswith(v)
                return fvl == v

            if op in ("matches_in", "exactly_in"):
                items = [i.strip() for i in value.split(',') if i.strip()]
                for item in items:
                    item_op = "is_exactly" if op == "exactly_in" else "matches"
                    if _apply_operator(values, item_op, item, field):
                        return True
                return False

            if op in ("contains", "equals", "matches", "is_exactly"):
                for fv in values:
                    if _positive_match(fv):
                        return True
                return False

            if op in ("not_contains", "not_equals"):
                # Rule matches only if NO value is a positive match
                return not any(_positive_match(fv) for fv in values)

            if op == "starts_with":
                return any(fv.lower().startswith(v) for fv in values)

            if op == "ends_with":
                return any(fv.lower().endswith(v) for fv in values)

            return False

        def _condition_matches(rule: Dict, cond: Dict) -> tuple:
            field    = cond.get("field", "")
            operator = cond.get("operator", "contains")
            value    = cond.get("value", "").strip()
            if not value:
                return True, ""
            values = _field_values(rule, field)
            hit = _apply_operator(values, operator, value, field)
            reason = f"{field} {operator} '{value}'" if hit else ""
            return hit, reason

        def _evaluate_group(rule: Dict, group: Dict) -> bool:
            conditions = [c for c in group.get("conditions", []) if c.get("value", "").strip()]
            if not conditions:
                return True
            intra = group.get("intra_connector", "AND").upper()
            results = [_condition_matches(rule, c)[0] for c in conditions]
            if intra == "OR":
                return any(results)
            return all(results)

        matched: List[Dict] = []
        for rule in rules:
            if not groups:
                matched.append({**rule, "_match_reasons": []})
                continue

            group_results = [(_evaluate_group(rule, g), g.get("inter_connector", "AND").upper()) for g in groups]
            final = group_results[0][0]
            for i in range(1, len(group_results)):
                connector = group_results[i - 1][1]
                if connector == "OR":
                    final = final or group_results[i][0]
                else:
                    final = final and group_results[i][0]

            if final:
                reasons = [
                    f"{c.get('field')} {c.get('operator')} '{c.get('value')}'"
                    for g in groups for c in g.get("conditions", [])
                    if c.get("value", "").strip() and _condition_matches(rule, c)[0]
                ]
                matched.append({**rule, "_match_reasons": reasons})

        return {
            "query": {"groups": groups},
            "total_scanned": len(rules),
            "matches_found": len(matched),
            "rules": matched,
        }

    async def search_rules(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
        source: Optional[str] = None,
        destination: Optional[str] = None,
        port: Optional[str] = None,
        protocol: Optional[str] = None,
        rule_types: Optional[List[str]] = None,
        action: Optional[str] = None,
        name_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search rules using flexible filters.
        source/destination accept IP, CIDR, or FQDN.
        CIDR containment matching: a rule matches if the user's IP is *contained in* any rule address.
        """
        rules = await self._flatten_rules(subscription_id, resource_group, policy_name)
        matched: List[Dict] = []
        match_details: List[Dict] = []

        for rule in rules:
            reasons: List[str] = []

            # --- rule type filter ---
            if rule_types:
                rt_set = {r.lower() for r in rule_types}
                if not any(rt in rule["rule_type"].lower() for rt in rt_set):
                    continue

            # --- action filter ---
            if action and rule["action"].lower() != action.lower():
                continue

            # --- name filter ---
            if name_filter and name_filter.lower() not in rule["name"].lower():
                continue

            # --- source filter ---
            if source:
                src_match = any(_cidr_contained_in(source, addr) for addr in rule["source_addresses"] if addr)
                if not src_match:
                    continue
                reasons.append(f"Source {source!r} is contained in rule source ranges")

            # --- destination filter ---
            if destination:
                # Try IP / CIDR match first
                dst_ip_match = any(_cidr_contained_in(destination, addr) for addr in rule["destination_addresses"] if addr)
                # Then FQDN match (suffix match)
                dst_fqdn_match = any(
                    destination.lower().endswith(fqdn.lstrip("*.").lower()) or fqdn.lstrip("*.").lower() == destination.lower()
                    for fqdn in rule["destination_fqdns"] + rule["destination_urls"]
                    if fqdn
                )
                if not (dst_ip_match or dst_fqdn_match):
                    continue
                reasons.append(f"Destination {destination!r} matched rule destinations")

            # --- port filter ---
            if port:
                port_match = port in rule["destination_ports"] or "*" in rule["destination_ports"]
                if not port_match:
                    # Range check
                    for dp in rule["destination_ports"]:
                        if "-" in str(dp):
                            try:
                                lo, hi = dp.split("-", 1)
                                if int(lo) <= int(port) <= int(hi):
                                    port_match = True
                                    break
                            except ValueError:
                                pass
                if not port_match:
                    continue
                reasons.append(f"Port {port!r} matched rule destinations")

            # --- protocol filter ---
            if protocol:
                proto_match = any(protocol.lower() in p.lower() for p in rule["protocols"]) or \
                              any(p.lower() in ("any", "*") for p in rule["protocols"]) or \
                              not rule["protocols"]
                if not proto_match:
                    continue
                reasons.append(f"Protocol {protocol!r} matched")

            matched.append(rule)
            match_details.append({**rule, "_match_reasons": reasons})

        return {
            "query": {
                "source": source,
                "destination": destination,
                "port": port,
                "protocol": protocol,
                "rule_types": rule_types,
                "action": action,
                "name_filter": name_filter,
            },
            "total_scanned": len(rules),
            "matches_found": len(matched),
            "rules": match_details,
        }

    # ------------------------------------------------------------------
    # Public: overlap detection  (optimised for large policies)
    # ------------------------------------------------------------------

    async def detect_overlaps(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
        rule_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Fast, two-phase overlap detection:

        Two-phase, policy-wide overlap analysis across ALL rule collection
        groups and rule collections in the policy.

        Phase 1 – O(n) exact-duplicate detection via canonical hash.
                   Operates across every rule in the policy.

        Phase 2 – Policy-wide CIDR / FQDN overlap using source-prefix
                   bucketing.  Each rule's source addresses are mapped to
                   their IPv4 /16 supernet bucket; only rules that share a
                   bucket (or have wildcard sources) are compared pairwise.
                   Rules with broad CIDRs (< /16 prefix) or wildcard sources
                   are compared against all rules in the category.
                   For 2 000+ network rules this reduces the comparison
                   space from O(n²) ≈ 2.5 M to O(k×m²) where k is the
                   number of /16 buckets and m is the average bucket size.

        Both phases run inside asyncio.to_thread so the event loop is free.
        """
        rules = await self._flatten_rules(subscription_id, resource_group, policy_name)

        if rule_types:
            rt_set = {r.lower() for r in rule_types}
            rules = [r for r in rules if any(rt in r["rule_type"].lower() for rt in rt_set)]

        overlaps, analysis_mode = await asyncio.to_thread(
            self._overlap_analysis_sync, rules
        )

        severity_order = {"exact_duplicate": 0, "same_action": 1, "conflict": 2}
        overlaps.sort(key=lambda x: severity_order.get(x["severity"], 99))

        summary = {
            "exact_duplicates": sum(1 for o in overlaps if o["severity"] == "exact_duplicate"),
            "same_action_overlaps": sum(1 for o in overlaps if o["severity"] == "same_action"),
            "conflicting_rules": sum(1 for o in overlaps if o["severity"] == "conflict"),
        }

        return {
            "total_rules_scanned": len(rules),
            "overlaps_found": len(overlaps),
            "summary": summary,
            "overlaps": overlaps,
            "analysis_mode": analysis_mode,
        }

    def _rule_canonical_key(self, rule: Dict) -> str:
        """Deterministic key that uniquely identifies a rule's traffic match."""
        return "|".join([
            rule["rule_type"].lower(),
            rule["action"].lower(),
            ",".join(sorted(rule["source_addresses"])),
            ",".join(sorted(rule["destination_addresses"])),
            ",".join(sorted(rule["destination_fqdns"] + rule["destination_urls"])),
            ",".join(sorted(rule["destination_ports"])),
            ",".join(sorted(p.lower() for p in rule["protocols"])),
        ])

    def _overlap_analysis_sync(self, rules: List[Dict]) -> Tuple[List[Dict], str]:
        """
        Fast, policy-wide duplicate and overlap detection.

        Architecture
        ──────────────────────────────────────────────────────────────────────
        Phase 1  O(n)   Exact duplicates via canonical hash across all rules.

        Phase 2  O(k)   CIDR / FQDN overlap using integer arithmetic:
          • Pre-parse every source / destination address ONCE into (start_int,
            end_int) integer tuples — eliminates ipaddress.ip_network() calls
            from the hot comparison path entirely.
          • Bucket by DESTINATION /24 (not source).  In hub-spoke policies the
            destinations are specific service IPs spread across many /24s, giving
            small, focused comparison groups (typically <50 rules per bucket).
          • Source addresses are wide spoke ranges — keeping them as the filter
            (not the bucket key) is correct; the integer interval check is O(1).
          • Application rules (≤500): full pairwise (≈125k max, trivial).
          • NAT rules: always trivial.
          • Hard cap: 30 000 candidate pairs per category as circuit-breaker.

        Expected end-to-end time for a 2340-rule policy: ≈100-500 ms.
        """
        from collections import defaultdict

        overlaps: List[Dict] = []
        recorded_pairs: set = set()

        # ── Phase 1: O(n) exact duplicate ─────────────────────────────────────
        seen: Dict[str, int] = {}
        for idx, rule in enumerate(rules):
            key = self._rule_canonical_key(rule)
            if key in seen:
                orig_idx = seen[key]
                pair = (min(orig_idx, idx), max(orig_idx, idx))
                if pair not in recorded_pairs:
                    recorded_pairs.add(pair)
                    orig = rules[orig_idx]
                    overlaps.append({
                        "rule1": orig,
                        "rule2": rule,
                        "rule_category": self._rule_category(rule["rule_type"]),
                        "severity": "exact_duplicate",
                        "action_match": True,
                        "source_overlap": [f"{s} \u2194 {s}" for s in rule["source_addresses"]],
                        "destination_ip_overlap": [f"{d} \u2194 {d}" for d in rule["destination_addresses"]],
                        "destination_fqdn_overlap": [f"{f} \u2194 {f}" for f in (rule["destination_fqdns"] + rule["destination_urls"])],
                        "port_overlap": rule["destination_ports"],
                        "recommendation": self._overlap_recommendation("exact_duplicate", orig, rule),
                    })
            else:
                seen[key] = idx

        # ── Pre-compute per-rule data ONCE (global index == position in rules) ─
        # Integer intervals: no ipaddress objects created during the comparison loop.
        src_p: List[Tuple] = [_parse_addr_intervals(r["source_addresses"]) for r in rules]
        dst_p: List[Tuple] = [_parse_addr_intervals(r["destination_addresses"]) for r in rules]
        port_fs:  List[frozenset] = [frozenset(r["destination_ports"]) for r in rules]
        proto_fs: List[frozenset] = [frozenset(p.upper() for p in r["protocols"]) for r in rules]
        fqdn_fs:  List[frozenset] = [frozenset(r["destination_fqdns"] + r["destination_urls"]) for r in rules]

        # Port expanded sets for range-overlap check (computed lazily is fine;
        # this is only used when the frozenset intersection is empty).
        port_exp: Dict[int, set] = {}

        def _port_exp(gi: int) -> set:
            if gi not in port_exp:
                port_exp[gi] = _expand_ports(list(port_fs[gi]))
            return port_exp[gi]

        # ── Phase 2: destination-bucketed candidate generation ─────────────────
        by_category: Dict[str, List[Tuple[int, Dict]]] = defaultdict(list)
        for idx, rule in enumerate(rules):
            by_category[self._rule_category(rule["rule_type"])].append((idx, rule))

        _MAX_PAIRS = 30_000  # kept as reference only — no longer used for capping
        bucket_stats: List[str] = []

        for category, indexed_cat in by_category.items():
            n = len(indexed_cat)

            if n <= 1:
                bucket_stats.append(f"{category}: {n} rules — 0 pairs")
                continue

            # Full pairwise for all categories — no cap
            if category != "network" or n <= 500:
                candidate_pairs: set = {
                    (la, lb)
                    for la in range(n)
                    for lb in range(la + 1, n)
                }
            else:
                # Network rules: bucket by destination /24 to reduce pairs naturally.
                # No cap — all bucket pairs and broad-dst same-collection pairs evaluated.
                dst_bucket_idx: Dict[str, List[int]] = defaultdict(list)
                broad_dst: List[int] = []

                for local_i, (global_i, rule) in enumerate(indexed_cat):
                    dw, _, _ = dst_p[global_i]
                    if dw or not rule["destination_addresses"]:
                        broad_dst.append(local_i)
                        continue
                    added_buckets: set = set()
                    is_broad = False
                    for addr in rule["destination_addresses"]:
                        b = _dst_bucket(addr)
                        if b == "*":
                            is_broad = True
                            break
                        if b not in added_buckets:
                            dst_bucket_idx[b].append(local_i)
                            added_buckets.add(b)
                    if is_broad:
                        broad_dst.append(local_i)

                candidate_pairs = set()

                # All within-bucket pairs
                for bkt_locals in dst_bucket_idx.values():
                    m = len(bkt_locals)
                    for a in range(m):
                        for b in range(a + 1, m):
                            candidate_pairs.add((min(bkt_locals[a], bkt_locals[b]),
                                                 max(bkt_locals[a], bkt_locals[b])))

                # Broad-destination rules: compare within same rule-collection
                col_locals: Dict[str, List[int]] = defaultdict(list)
                for local_i, (_, rule) in enumerate(indexed_cat):
                    col_locals[rule["rule_collection"]].append(local_i)
                for li in broad_dst:
                    _, br = indexed_cat[li]
                    for lj in col_locals.get(br["rule_collection"], []):
                        if li != lj:
                            candidate_pairs.add((min(li, lj), max(li, lj)))

            n_full = n * (n - 1) // 2 or 1
            bucket_stats.append(
                f"{category}: {n} rules \u2014 {len(candidate_pairs):,}/{n_full:,} pairs"
                # bucket_stats is returned in analysis_mode for debugging only.
                # UI deliberately does not display this — see OverlapAnalysis.tsx.
                # Interpretation: checked_pairs/total_possible where skipped pairs are
                # provably non-overlapping (different /24 destination buckets).
            )

            # ── Evaluate candidates ────────────────────────────────────────────
            for la, lb in candidate_pairs:
                ga, r1 = indexed_cat[la]
                gb, r2 = indexed_cat[lb]
                pair = (min(ga, gb), max(ga, gb))
                if pair in recorded_pairs:
                    continue
                result = self._check_overlap(
                    ga, gb, r1, r2, category,
                    src_p, dst_p, port_fs, proto_fs, fqdn_fs, _port_exp,
                )
                if result:
                    recorded_pairs.add(pair)
                    overlaps.append(result)

        analysis_mode = f"full_policy_wide | {' | '.join(bucket_stats)}"
        return overlaps, analysis_mode

    def _check_overlap(
        self,
        gi: int, gj: int,
        r1: Dict, r2: Dict,
        category: str,
        src_p: List[Tuple],
        dst_p: List[Tuple],
        port_fs: List[frozenset],
        proto_fs: List[frozenset],
        fqdn_fs: List[frozenset],
        port_exp_cache: Dict,
    ) -> Optional[Dict]:
        """
        Single pair overlap check using pre-computed integer intervals.
        All rejections use integer arithmetic — no ipaddress objects created here.
        """
        sw1, si1, st1 = src_p[gi]
        sw2, si2, st2 = src_p[gj]
        dw1, di1, dt1 = dst_p[gi]
        dw2, di2, dt2 = dst_p[gj]
        p1, p2   = port_fs[gi],  port_fs[gj]
        pr1, pr2 = proto_fs[gi], proto_fs[gj]
        f1, f2   = fqdn_fs[gi],  fqdn_fs[gj]

        port_wild = "*" in p1 or "*" in p2
        proto_any = not pr1 or not pr2 or bool(pr1 & {"ANY", "*"}) or bool(pr2 & {"ANY", "*"})

        # ── Protocol (fast exit) ────────────────────────────────────────────
        if not proto_any and not (pr1 & pr2):
            return None

        # ── Source overlap ──────────────────────────────────────────────────
        if not _ivs_overlap(sw1, si1, st1, sw2, si2, st2):
            return None
        src_pairs = _fmt_addr_overlap(sw1, sw2, r1["source_addresses"], r2["source_addresses"])

        # ── Destination overlap ─────────────────────────────────────────────
        dst_ip_pairs: List[str] = []
        dst_fqdn_pairs: List[str] = []

        if category == "application":
            common_fqdn = f1 & f2
            if not common_fqdn and not (dw1 or dw2):
                return None
            dst_fqdn_pairs = [f"{f} \u2194 {f}" for f in list(common_fqdn)] or ["FQDN overlap"]
        else:
            # Network / NAT rules can target IPs or FQDNs (or both).
            # Mixed IP-vs-FQDN never overlaps: an IP rule and an FQDN rule
            # match completely different traffic — they are not duplicates.
            has_ip1  = dw1 or bool(di1) or bool(dt1)
            has_ip2  = dw2 or bool(di2) or bool(dt2)
            has_fqdn1 = bool(f1)
            has_fqdn2 = bool(f2)

            if has_ip1 and has_ip2:
                # Both IP-based: normal interval overlap check
                if not _ivs_overlap(dw1, di1, dt1, dw2, di2, dt2):
                    return None
                dst_ip_pairs = _fmt_addr_overlap(dw1, dw2, r1["destination_addresses"], r2["destination_addresses"])
            elif has_fqdn1 and has_fqdn2:
                # Both FQDN-based network rules: check FQDN intersection
                common_fqdn = f1 & f2
                if not common_fqdn:
                    return None
                dst_fqdn_pairs = [f"{f} \u2194 {f}" for f in sorted(common_fqdn)]
            elif dw1 or dw2:
                # One side is an explicit wildcard (*)
                dst_ip_pairs = ["* (wildcard)"]
            else:
                # Mixed IP-destination vs FQDN-destination, or one/both empty — no overlap
                return None

        # ── Port overlap ────────────────────────────────────────────────────
        if category == "application" or port_wild:
            port_common = list(p1 & p2) or ["*"]
        else:
            common_ports = p1 & p2
            if common_ports:
                port_common = list(common_ports)[:10]
            else:
                # Range overlap check (only reached when no exact port match)
                exp1, exp2 = port_exp_cache(gi), port_exp_cache(gj)
                if not (exp1 & exp2):
                    return None
                port_common = []

        action_match = r1["action"].lower() == r2["action"].lower()
        severity = "same_action" if action_match else "conflict"

        return {
            "rule1": r1,
            "rule2": r2,
            "rule_category": category,
            "severity": severity,
            "action_match": action_match,
            "source_overlap": src_pairs,
            "destination_ip_overlap": dst_ip_pairs,
            "destination_fqdn_overlap": dst_fqdn_pairs,
            "port_overlap": port_common,
            "recommendation": self._overlap_recommendation(severity, r1, r2),
        }

    def _rule_category(self, rule_type: str) -> str:
        rt = rule_type.lower()
        if "nat" in rt:
            return "nat"
        if "application" in rt:
            return "application"
        return "network"

    def _overlap_recommendation(self, severity: str, r1: Dict, r2: Dict) -> str:
        if severity == "exact_duplicate":
            return (
                f"Rule '{r2['name']}' is an exact duplicate of '{r1['name']}'. "
                f"Consider removing '{r2['name']}' (lower priority in group '{r2['rule_group']}')."
            )
        if severity == "same_action":
            return (
                f"Rules '{r1['name']}' and '{r2['name']}' have overlapping scope and both "
                f"perform action '{r1['action']}'. Consider consolidating."
            )
        return (
            f"Rules '{r1['name']}' (action={r1['action']}) and '{r2['name']}' "
            f"(action={r2['action']}) have overlapping scope but conflicting actions. "
            f"Review evaluation order — the higher-priority collection wins."
        )

    # ------------------------------------------------------------------
    # Public: orphan detection via Log Analytics
    # ------------------------------------------------------------------

    async def detect_orphan_rules(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
        workspace_id: str,
        days: int = 30,
        rule_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Cross-correlate rules with Azure Firewall logs from Log Analytics.
        Rules with zero log hits in the specified window are flagged as orphans.
        Returns:
          - orphan_rules: rules never hit
          - active_rules: rules with hit counts
          - top_rules: top 10 most-hit rules
        """
        rules = await self._flatten_rules(subscription_id, resource_group, policy_name)
        if rule_types:
            rt_set = {r.lower() for r in rule_types}
            rules = [r for r in rules if any(rt in r["rule_type"].lower() for rt in rt_set)]

        # Query Azure Firewall logs
        # Returns dict keyed by "rulecollection|rulename"
        log_hits = await self._query_firewall_logs(workspace_id, days)

        orphans = []
        active = []
        for rule in rules:
            # Build composite key matching what _parse_log_hits produces.
            # Try exact collection match first; fall back to name-only across all collections.
            collection_key = f"{rule['rule_collection'].lower()}|{rule['name'].lower()}"
            nocollection_key = f"__nocollection__|{rule['name'].lower()}"

            hits = log_hits.get(collection_key, 0)
            if hits == 0:
                hits = log_hits.get(nocollection_key, 0)
            if hits == 0:
                # Last resort: check any collection that has this rule name
                # (handles cases where AzureDiagnostics parse couldn't extract collection)
                name_lower = rule['name'].lower()
                hits = next(
                    (v for k, v in log_hits.items() if k.endswith(f"|{name_lower}")),
                    0,
                )

            entry = {**rule, "log_hits": hits, "days_observed": days}
            if hits == 0:
                orphans.append(entry)
            else:
                active.append(entry)

        active.sort(key=lambda x: x["log_hits"], reverse=True)
        top_rules = active[:10]

        return {
            "days_observed": days,
            "total_rules": len(rules),
            "orphan_count": len(orphans),
            "active_count": len(active),
            "workspace_id": workspace_id,
            "orphan_rules": orphans,
            "active_rules": active,
            "top_rules": top_rules,
            "log_available": len(log_hits) > 0,
        }

    async def _query_firewall_logs(self, workspace_id: str, days: int) -> Dict[str, int]:
        """
        Query firewall log tables via Log Analytics REST API.
        Uses a token scoped to api.loganalytics.io (not management.azure.com).
        Returns dict of rule_name.lower() -> hit count.
        """
        mgmt_token = await asyncio.to_thread(self._get_sp_token)
        la_token   = await asyncio.to_thread(self._get_sp_la_token)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)

        # isfuzzy=true silently ignores tables that don't exist in the workspace
        # (e.g. Dedicated-mode tables absent in AzureDiagnostics-only workspaces)
        ts_start = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
        ts_end   = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
        kql = f"""
union isfuzzy=true
  // Dedicated-mode tables — key on RuleCollection + Rule for uniqueness
  (AZFWNetworkRule
  | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
  | where isnotempty(Rule)
  | summarize Hits = count() by RuleCollection, RuleName = Rule),
  (AZFWApplicationRule
  | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
  | where isnotempty(Rule)
  | summarize Hits = count() by RuleCollection, RuleName = Rule),
  (AZFWNatRule
  | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
  | where isnotempty(Rule)
  | summarize Hits = count() by RuleCollection, RuleName = Rule),
  // AzureDiagnostics fallback — extract both collection and rule name from msg_s
  (AzureDiagnostics | where Category == "AZFWNetworkRule"
  | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
  | extend _parts = extract_all(@"Rule Collection Group: ([^.]+)\\. Rule Collection: ([^.]+)\\. Rule: ([^.\\r\\n]+)", msg_s)
  | mv-expand _parts
  | extend RuleCollection = trim(" ", tostring(_parts[1])), RuleName = trim(" ", tostring(_parts[2]))
  | where isnotempty(RuleName)
  | summarize Hits = count() by RuleCollection, RuleName),
  (AzureDiagnostics | where Category == "AZFWApplicationRule"
  | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
  | extend _parts = extract_all(@"Rule Collection Group: ([^.]+)\\. Rule Collection: ([^.]+)\\. Rule: ([^.\\r\\n]+)", msg_s)
  | mv-expand _parts
  | extend RuleCollection = trim(" ", tostring(_parts[1])), RuleName = trim(" ", tostring(_parts[2]))
  | where isnotempty(RuleName)
  | summarize Hits = count() by RuleCollection, RuleName)
| summarize Hits = sum(Hits) by RuleCollection, RuleName
| order by Hits desc
"""

        # Resolve workspace GUID using management token (ARM call)
        workspace_guid = await self._resolve_workspace_guid(workspace_id, mgmt_token)

        url = f"https://api.loganalytics.io/v1/workspaces/{workspace_guid}/query"

        def _run():
            resp = requests.post(
                url,
                headers=_make_headers(la_token),
                json={"query": kql},
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Log Analytics query returned %s: %s", resp.status_code, resp.text[:200])
            return None

        data = await asyncio.to_thread(_run)
        return self._parse_log_hits(data)

    async def _resolve_workspace_guid(self, workspace_id: str, token: str) -> str:
        """Resolve workspace resource ID to GUID if needed."""
        if len(workspace_id) == 36 and workspace_id.count("-") == 4:
            return workspace_id
        if workspace_id.startswith("/subscriptions/"):
            url = f"{_MANAGEMENT_API}{workspace_id}?api-version=2021-06-01"
            data = await asyncio.to_thread(_sync_get, url, token)
            if data:
                guid = data.get("properties", {}).get("customerId")
                if guid:
                    return guid
        return workspace_id

    def _parse_log_hits(self, data: Optional[Dict]) -> Dict[str, int]:
        """
        Returns dict keyed by "rulecollection|rulename" (both lowercased).
        Falls back to "__nocollection__|rulename" when RuleCollection is absent/empty.
        """
        if not data:
            return {}
        result: Dict[str, int] = {}
        try:
            tables = data.get("tables", [])
            if not tables:
                return {}
            rows = tables[0].get("rows", [])
            cols = [c["name"].lower() for c in tables[0].get("columns", [])]
            name_idx       = next((i for i, c in enumerate(cols) if c == "rulename"), None)
            hits_idx       = next((i for i, c in enumerate(cols) if c == "hits"), None)
            collection_idx = next((i for i, c in enumerate(cols) if c == "rulecollection"), None)
            if name_idx is None or hits_idx is None:
                return {}
            for row in rows:
                rule_name = str(row[name_idx]).lower().strip()
                hits      = int(row[hits_idx])
                collection = str(row[collection_idx]).lower().strip() if collection_idx is not None and row[collection_idx] else "__nocollection__"
                key = f"{collection}|{rule_name}"
                result[key] = result.get(key, 0) + hits
        except Exception as e:
            logger.warning("Failed to parse log analytics response: %s", e)
        return result

    # ------------------------------------------------------------------
    # Public: Log correlation for a specific rule
    # ------------------------------------------------------------------

    async def get_rule_log_detail(
        self,
        workspace_id: str,
        rule_name: str,
        days: int = 7,
    ) -> Dict[str, Any]:
        """
        Fetch detailed traffic log entries for a specific rule name from Log Analytics.
        Returns time series + top source/destination combos.
        """
        mgmt_token = await asyncio.to_thread(self._get_sp_token)
        la_token   = await asyncio.to_thread(self._get_sp_la_token)
        workspace_guid = await self._resolve_workspace_guid(workspace_id, mgmt_token)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        ts_start = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
        ts_end   = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Sanitise rule_name to prevent KQL injection
        rule_name_safe = rule_name.replace('"', '').replace("'", "")

        kql = f"""
union isfuzzy=true
  // Dedicated-mode
  (AZFWNetworkRule
   | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
   | where Rule =~ "{rule_name_safe}"
   | project TimeGenerated, RuleName=Rule, SourceIP=SourceIp, DestIP=DestinationIp, DestinationPort=tostring(DestinationPort), Protocol, Action),
  (AZFWApplicationRule
   | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
   | where Rule =~ "{rule_name_safe}"
   | project TimeGenerated, RuleName=Rule, SourceIP=SourceIp, DestIP=coalesce(Fqdn, TargetUrl, ""), DestinationPort=tostring(DestinationPort), Protocol=Protocol, Action),
  (AZFWNatRule
   | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
   | where Rule =~ "{rule_name_safe}"
   | project TimeGenerated, RuleName=Rule, SourceIP=SourceIp, DestIP=DestinationIp, DestinationPort=tostring(DestinationPort), Protocol, Action="DNAT"),
  // AzureDiagnostics fallback
  (AzureDiagnostics | where Category == "AZFWNetworkRule"
   | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
   | extend RuleName = trim(" ", tostring(extract(@"Rule: ([^.\\r\\n]+)", 1, msg_s)))
   | where RuleName =~ "{rule_name_safe}"
   | extend SourceIP=tostring(extract(@"from (\\S+):\\d+", 1, msg_s)), DestIP=tostring(extract(@"to (\\S+):\\d+", 1, msg_s))
   | extend DestinationPort=tostring(extract(@"to \\S+:(\\d+)", 1, msg_s))
   | project TimeGenerated, RuleName, SourceIP, DestIP, DestinationPort, Protocol="Network", Action=tostring(extract(@"action: (\\w+)", 1, msg_s))),
  (AzureDiagnostics | where Category == "AZFWApplicationRule"
   | where TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))
   | extend RuleName = trim(" ", tostring(extract(@"Rule: ([^.\\r\\n]+)", 1, msg_s)))
   | where RuleName =~ "{rule_name_safe}"
   | extend SourceIP=tostring(extract(@"from (\\S+):\\d+", 1, msg_s)), DestIP=tostring(extract(@"to (\\S+)", 1, msg_s))
   | extend DestinationPort=tostring(extract(@"to \\S+:(\\d+)", 1, msg_s))
   | project TimeGenerated, RuleName, SourceIP, DestIP, DestinationPort, Protocol="Application", Action=tostring(extract(@"action: (\\w+)", 1, msg_s)))
| order by TimeGenerated desc
| take 500
"""
        url = f"https://api.loganalytics.io/v1/workspaces/{workspace_guid}/query"

        def _run():
            resp = requests.post(url, headers=_make_headers(la_token), json={"query": kql}, timeout=45)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Rule log detail query returned %s: %s", resp.status_code, resp.text[:200])
            return None

        data = await asyncio.to_thread(_run)

        entries = []
        if data:
            try:
                tables = data.get("tables", [])
                if tables:
                    cols = [c["name"] for c in tables[0].get("columns", [])]
                    for row in tables[0].get("rows", []):
                        entries.append(dict(zip(cols, row)))
            except Exception:
                pass

        return {
            "rule_name": rule_name,
            "days_observed": days,
            "total_hits": len(entries),
            "entries": entries[:100],  # cap
        }

    # ------------------------------------------------------------------
    # Public: Traffic Intelligence
    # ------------------------------------------------------------------

    async def get_traffic_intelligence(
        self,
        workspace_id: str,
        days: int = 7,
        rule_types: Optional[List[str]] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run 9 parallel KQL queries to produce a full traffic intelligence report:
          - denied_sources, denied_destinations, denied_ports, deny_trend
          - top_rules (by hit count, all actions)
          - top_talkers (src IPs by total traffic)
          - east_west_ratio (RFC1918 vs public)
          - threat_intel_hits (AZFWThreatIntel)
          - idps_signatures (AZFWIdpsSignature)
        """
        mgmt_token = await asyncio.to_thread(self._get_sp_token)
        la_token   = await asyncio.to_thread(self._get_sp_la_token)
        workspace_guid = await self._resolve_workspace_guid(workspace_id, mgmt_token)
        url = f"https://api.loganalytics.io/v1/workspaces/{workspace_guid}/query"

        if start_time and end_time:
            ts_start = start_time
            ts_end   = end_time
            delta    = datetime.fromisoformat(end_time.replace("Z", "+00:00")) - datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            days     = max(1, delta.days)
        else:
            end_dt   = datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(days=days)
            ts_start = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            ts_end   = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        time_bin   = "1h" if days <= 3 else ("6h" if days <= 14 else "1d")

        rt_filter = ""
        if rule_types:
            rt_lower = [r.lower() for r in rule_types]
            parts = []
            if any("network" in r for r in rt_lower):
                parts.append("network")
            if any("application" in r for r in rt_lower):
                parts.append("application")
            if any("nat" in r for r in rt_lower):
                parts.append("nat")
            rt_filter = "| where RuleType in~ (" + ",".join(f'"{p}"' for p in parts) + ")" if parts else ""

        def _post(kql: str):
            resp = requests.post(url, headers=_make_headers(la_token), json={"query": kql}, timeout=60)
            return resp.json() if resp.status_code == 200 else None

        def _rows(data, cols=None):
            if not data: return []
            try:
                t = data["tables"][0]
                c = [x["name"] for x in t["columns"]]
                return [dict(zip(c, r)) for r in t.get("rows", [])]
            except Exception:
                return []

        # ── KQL fragments ────────────────────────────────────────────────────
        time_filter = f"TimeGenerated between (datetime({ts_start}) .. datetime({ts_end}))"

        kql_denied_src = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | where Action =~ "Deny" | summarize Count=count() by Source=SourceIp),
  (AZFWApplicationRule | where {time_filter} | where Action =~ "Deny" | summarize Count=count() by Source=SourceIp),
  (AZFWNatRule         | where {time_filter} | where Action =~ "Deny" | summarize Count=count() by Source=SourceIp),
  (AzureDiagnostics | where Category == "AZFWNetworkRule" | where {time_filter} | where msg_s contains "Denied"
   | extend Source=tostring(extract(@"from (\S+):\d+", 1, msg_s)) | summarize Count=count() by Source),
  (AzureDiagnostics | where Category == "AZFWApplicationRule" | where {time_filter} | where msg_s contains "Denied"
   | extend Source=tostring(extract(@"from (\S+):\d+", 1, msg_s)) | summarize Count=count() by Source)
| where isnotempty(Source)
| summarize Count=sum(Count) by Source | order by Count desc | take 20"""

        kql_denied_dst = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | where Action =~ "Deny" | summarize Count=count() by Dest=DestinationIp),
  (AZFWApplicationRule | where {time_filter} | where Action =~ "Deny"
   | extend Dest=coalesce(TargetUrl, DestinationIp) | summarize Count=count() by Dest),
  (AzureDiagnostics | where Category == "AZFWNetworkRule" | where {time_filter} | where msg_s contains "Denied"
   | extend Dest=tostring(extract(@"to (\S+):\d+", 1, msg_s)) | summarize Count=count() by Dest),
  (AzureDiagnostics | where Category == "AZFWApplicationRule" | where {time_filter} | where msg_s contains "Denied"
   | extend Dest=tostring(extract(@"to (\S+)", 1, msg_s)) | summarize Count=count() by Dest)
| where isnotempty(Dest)
| summarize Count=sum(Count) by Dest | order by Count desc | take 20"""

        kql_denied_ports = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | where Action =~ "Deny"
   | summarize Count=count() by Port=tostring(DestinationPort), Protocol),
  (AzureDiagnostics | where Category == "AZFWNetworkRule" | where {time_filter} | where msg_s contains "Denied"
   | extend Port=tostring(extract(@"to \S+:(\d+)", 1, msg_s)), Protocol="Network"
   | summarize Count=count() by Port, Protocol)
| where isnotempty(Port)
| summarize Count=sum(Count) by Port, Protocol | order by Count desc | take 20"""

        kql_deny_trend = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | where Action =~ "Deny" | summarize Count=count() by T=bin(TimeGenerated,{time_bin})),
  (AZFWApplicationRule | where {time_filter} | where Action =~ "Deny" | summarize Count=count() by T=bin(TimeGenerated,{time_bin})),
  (AzureDiagnostics | where Category == "AZFWNetworkRule" | where {time_filter} | where msg_s contains "Denied" | summarize Count=count() by T=bin(TimeGenerated,{time_bin})),
  (AzureDiagnostics | where Category == "AZFWApplicationRule" | where {time_filter} | where msg_s contains "Denied" | summarize Count=count() by T=bin(TimeGenerated,{time_bin}))
| summarize Count=sum(Count) by T | order by T asc"""

        kql_top_rules = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | where isnotempty(Rule)
   | extend FwRuleType="Network"      | summarize Count=count() by Rule, RuleCollection, FwRuleType),
  (AZFWApplicationRule | where {time_filter} | where isnotempty(Rule)
   | extend FwRuleType="Application"  | summarize Count=count() by Rule, RuleCollection, FwRuleType),
  (AZFWNatRule         | where {time_filter} | where isnotempty(Rule)
   | extend FwRuleType="NAT"          | summarize Count=count() by Rule, RuleCollection, FwRuleType),
  (AzureDiagnostics | where Category == "AZFWNetworkRule" | where {time_filter}
   | extend Rule=trim(" ",tostring(extract(@'Rule: ([^.]+)', 1, msg_s))), RuleCollection="(legacy)", FwRuleType="Network"
   | where isnotempty(Rule) | summarize Count=count() by Rule, RuleCollection, FwRuleType),
  (AzureDiagnostics | where Category == "AZFWApplicationRule" | where {time_filter}
   | extend Rule=trim(" ",tostring(extract(@'Rule: ([^.]+)', 1, msg_s))), RuleCollection="(legacy)", FwRuleType="Application"
   | where isnotempty(Rule) | summarize Count=count() by Rule, RuleCollection, FwRuleType)
| summarize Count=sum(Count) by Rule, RuleCollection, RuleType=FwRuleType
| order by Count desc | take 30"""

        kql_top_talkers = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | summarize Count=count() by Source=SourceIp),
  (AZFWApplicationRule | where {time_filter} | summarize Count=count() by Source=SourceIp),
  (AzureDiagnostics | where Category == "AZFWNetworkRule" | where {time_filter}
   | extend Source=tostring(extract(@"from (\S+):\d+", 1, msg_s)) | summarize Count=count() by Source),
  (AzureDiagnostics | where Category == "AZFWApplicationRule" | where {time_filter}
   | extend Source=tostring(extract(@"from (\S+):\d+", 1, msg_s)) | summarize Count=count() by Source)
| where isnotempty(Source)
| summarize Count=sum(Count) by Source | order by Count desc | take 20"""

        kql_ew_ratio = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | project SourceIp),
  (AZFWApplicationRule | where {time_filter} | project SourceIp),
  (AzureDiagnostics | where Category == "AZFWNetworkRule" | where {time_filter}
   | extend SourceIp=tostring(extract(@"from (\S+):\d+", 1, msg_s)) | project SourceIp),
  (AzureDiagnostics | where Category == "AZFWApplicationRule" | where {time_filter}
   | extend SourceIp=tostring(extract(@"from (\S+):\d+", 1, msg_s)) | project SourceIp)
| where isnotempty(SourceIp)
| extend IsInternal = ipv4_is_private(SourceIp)
| summarize Count=count() by IsInternal"""

        kql_threat_intel = f"""
AZFWThreatIntel
| where {time_filter}
| summarize Count=count() by Fqdn=coalesce(Fqdn,DestinationIp), ThreatDescription, Action
| order by Count desc | take 20"""

        kql_idps = f"""
AZFWIdpsSignature
| where {time_filter}
| summarize Count=count() by SignatureId=tostring(SignatureId), Description, Severity, Action
| order by Count desc | take 20"""

        kql_proto_anomalies = f"""
let known_ports = dynamic({{"TCP":{{22,25,53,80,443,3389,8080,8443}},"UDP":{{53,123,161,500,4500}}}});
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter}
   | where Protocol in~ ("TCP","UDP") and isnotempty(DestinationPort)
   | summarize Count=count() by Protocol, Port=tostring(DestinationPort)),
  (AzureDiagnostics | where Category == "AZFWNetworkRule" | where {time_filter}
   | extend Protocol="Network", Port=tostring(extract(@"to \S+:(\d+)", 1, msg_s))
   | where isnotempty(Port) | summarize Count=count() by Protocol, Port)
| summarize Count=sum(Count) by Protocol, Port
| extend PortNum=toint(Port)
| extend IsAnomaly = (Protocol =~ "TCP" and PortNum !in (22,25,53,80,443,3389,8080,8443))
       or (Protocol =~ "UDP" and PortNum !in (53,123,161,500,4500))
| where IsAnomaly == true
| order by Count desc | take 20"""

        kql_denied_combos = f"""
union isfuzzy=true
  (AZFWNetworkRule
   | where {time_filter} | where Action =~ "Deny"
   | extend Destination=coalesce(DestinationIp,"")
   | summarize Count=count() by
       SourceIp, Destination,
       Port=tostring(DestinationPort), Protocol,
       Rule=coalesce(Rule,"(no rule)"),
       RuleCollection=coalesce(RuleCollection,"(default)"),
       RuleType="Network"),
  (AZFWApplicationRule
   | where {time_filter} | where Action =~ "Deny"
   | extend Destination=coalesce(Fqdn, TargetUrl, DestinationIp, "")
   | summarize Count=count() by
       SourceIp, Destination,
       Port=tostring(DestinationPort), Protocol,
       Rule=coalesce(Rule,"(no rule)"),
       RuleCollection=coalesce(RuleCollection,"(default)"),
       RuleType="Application"),
  (AzureDiagnostics | where Category == "AZFWNetworkRule"
   | where {time_filter} | where msg_s contains "Denied"
   | extend
       SourceIp=tostring(extract(@"from (\S+):\d+", 1, msg_s)),
       Destination=tostring(extract(@"to (\S+):\d+", 1, msg_s)),
       Port=tostring(extract(@"to \S+:(\d+)", 1, msg_s)),
       Protocol="Network",
       Rule=coalesce(trim(" ",tostring(extract(@"Rule: ([^.]+)", 1, msg_s))),"(default action)"),
       RuleCollection="(legacy)",
       RuleType="Network"
   | summarize Count=count() by SourceIp, Destination, Port, Protocol, Rule, RuleCollection, RuleType),
  (AzureDiagnostics | where Category == "AZFWApplicationRule"
   | where {time_filter} | where msg_s contains "Denied"
   | extend
       SourceIp=tostring(extract(@"from (\S+):\d+", 1, msg_s)),
       Destination=tostring(extract(@"to (\S+)", 1, msg_s)),
       Port="", Protocol="Application",
       Rule=coalesce(trim(" ",tostring(extract(@"Rule: ([^.]+)", 1, msg_s))),"(default action)"),
       RuleCollection="(legacy)",
       RuleType="Application"
   | summarize Count=count() by SourceIp, Destination, Port, Protocol, Rule, RuleCollection, RuleType)
| where isnotempty(SourceIp)
| summarize Count=sum(Count) by SourceIp, Destination, Port, Protocol, Rule, RuleCollection, RuleType
| order by Count desc | take 30"""

        kql_nat_activity = f"""
union isfuzzy=true
  (AZFWNatRule
   | where {time_filter}
   | extend
       ExternalPort=tostring(DestinationPort),
       TranslatedIp=coalesce(TranslatedIp,""),
       TranslatedPort=tostring(coalesce(TranslatedPort,0)),
       RuleName=coalesce(Rule,"(unknown)"),
       Collection=coalesce(RuleCollection,"")
   | summarize
       Count=count(),
       UniqueSources=dcount(SourceIp),
       UniqueExternalIPs=dcount(DestinationIp)
     by ExternalPort, Protocol, TranslatedIp, TranslatedPort, RuleName, Collection),
  (AzureDiagnostics | where Category == "AZFWNetworkRule"
   | where {time_filter} | where msg_s contains "DNAT"
   | extend
       ExternalPort=tostring(extract(@"to \S+:(\d+)", 1, msg_s)),
       TranslatedIp=tostring(extract(@"Translated to (\S+):\d+", 1, msg_s)),
       TranslatedPort=tostring(extract(@"Translated to \S+:(\d+)", 1, msg_s)),
       Protocol="Network",
       RuleName=coalesce(trim(" ",tostring(extract(@"Rule: ([^.]+)", 1, msg_s))),"(legacy)"),
       Collection="(legacy)"
   | summarize
       Count=count(),
       UniqueSources=dcount(extract(@"from (\S+):\d+", 1, msg_s)),
       UniqueExternalIPs=dcount(extract(@"from \S+ to (\S+):\d+", 1, msg_s))
     by ExternalPort, Protocol, TranslatedIp, TranslatedPort, RuleName, Collection)
| summarize
    Count=sum(Count),
    UniqueSources=sum(UniqueSources),
    UniqueExternalIPs=sum(UniqueExternalIPs)
  by ExternalPort, Protocol, TranslatedIp, TranslatedPort, RuleName, Collection
| order by Count desc | take 20"""

        kql_nat_sources = f"""
union isfuzzy=true
  (AZFWNatRule
   | where {time_filter}
   | summarize Count=count(), UniqueDestinations=dcount(DestinationIp) by Source=SourceIp),
  (AzureDiagnostics | where Category == "AZFWNetworkRule"
   | where {time_filter} | where msg_s contains "DNAT"
   | extend Source=tostring(extract(@"from (\S+):\d+", 1, msg_s))
   | where isnotempty(Source)
   | summarize Count=count(), UniqueDestinations=dcount(tostring(extract(@"to (\S+):\d+", 1, msg_s))) by Source)
| where isnotempty(Source)
| summarize Count=sum(Count), UniqueDestinations=sum(UniqueDestinations) by Source
| order by Count desc | take 15"""

        kql_dns_top = f"""
union isfuzzy=true
  (AZFWDnsQuery
   | where {time_filter}
   | summarize Count=count(), UniqueClients=dcount(SourceIp) by QueryName),
  (AzureDiagnostics | where Category == "AZFWDnsProxy"
   | where {time_filter}
   | extend QueryName=tostring(extract(@"DNS Request: \S+ \d+ IN (\S+)", 1, msg_s))
   | where isnotempty(QueryName)
   | summarize Count=count(), UniqueClients=dcount(tostring(extract(@"from (\S+):\d+", 1, msg_s))) by QueryName)
| where isnotempty(QueryName)
| summarize Count=sum(Count), UniqueClients=sum(UniqueClients) by QueryName
| order by Count desc | take 15"""

        # ── Real unique/total counts (dcount — no take limit) ────────────────
        kql_total_deny_events = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | where Action =~ "Deny" | count),
  (AZFWApplicationRule | where {time_filter} | where Action =~ "Deny" | count),
  (AzureDiagnostics | where Category in ("AZFWNetworkRule","AZFWApplicationRule")
   | where {time_filter} | where msg_s contains "Denied" | count)
| summarize Total=sum(Count_)"""

        kql_unique_deny_sources = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | where Action =~ "Deny" | summarize by SourceIp),
  (AZFWApplicationRule | where {time_filter} | where Action =~ "Deny" | summarize by SourceIp),
  (AzureDiagnostics | where Category in ("AZFWNetworkRule","AZFWApplicationRule")
   | where {time_filter} | where msg_s contains "Denied"
   | extend SourceIp=tostring(extract(@"from (\S+):\d+", 1, msg_s))
   | where isnotempty(SourceIp) | summarize by SourceIp)
| summarize UniqueCount=dcount(SourceIp)"""

        kql_unique_rules_hit = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | where isnotempty(Rule) | summarize by Rule),
  (AZFWApplicationRule | where {time_filter} | where isnotempty(Rule) | summarize by Rule),
  (AZFWNatRule         | where {time_filter} | where isnotempty(Rule) | summarize by Rule),
  (AzureDiagnostics | where Category in ("AZFWNetworkRule","AZFWApplicationRule")
   | where {time_filter}
   | extend Rule=trim(" ",tostring(extract(@"Rule: ([^.]+)", 1, msg_s)))
   | where isnotempty(Rule) | summarize by Rule)
| summarize UniqueCount=dcount(Rule)"""

        kql_unique_talkers = f"""
union isfuzzy=true
  (AZFWNetworkRule     | where {time_filter} | summarize by SourceIp),
  (AZFWApplicationRule | where {time_filter} | summarize by SourceIp),
  (AzureDiagnostics | where Category in ("AZFWNetworkRule","AZFWApplicationRule")
   | where {time_filter}
   | extend SourceIp=tostring(extract(@"from (\S+):\d+", 1, msg_s))
   | where isnotempty(SourceIp) | summarize by SourceIp)
| summarize UniqueCount=dcount(SourceIp)"""

        import concurrent.futures
        loop = asyncio.get_event_loop()
        queries = [
            kql_denied_src, kql_denied_dst, kql_denied_ports, kql_deny_trend,
            kql_top_rules, kql_top_talkers, kql_ew_ratio,
            kql_threat_intel, kql_idps, kql_proto_anomalies, kql_denied_combos,
            kql_nat_activity, kql_nat_sources, kql_dns_top,
            kql_total_deny_events, kql_unique_deny_sources,
            kql_unique_rules_hit, kql_unique_talkers,
        ]
        results = await asyncio.gather(*[asyncio.to_thread(_post, q) for q in queries])
        (r_dsrc, r_ddst, r_dport, r_trend,
         r_top, r_talkers, r_ew,
         r_threat, r_idps, r_proto, r_combos, r_nat,
         r_nat_src, r_dns_top,
         r_total_deny, r_unique_src,
         r_unique_rules, r_unique_talkers) = results

        ew_rows = _rows(r_ew)
        internal = sum(r["Count"] for r in ew_rows if r.get("IsInternal"))
        external = sum(r["Count"] for r in ew_rows if not r.get("IsInternal"))

        def _scalar(data, field: str, default=0):
            rows = _rows(data)
            return rows[0].get(field, default) if rows else default

        return {
            "days":                days,
            "workspace_id":        workspace_id,
            "denied_sources":      _rows(r_dsrc),
            "denied_destinations": _rows(r_ddst),
            "denied_ports":        _rows(r_dport),
            "deny_trend":          _rows(r_trend),
            "top_rules":           _rows(r_top),
            "top_talkers":         _rows(r_talkers),
            "east_west": {
                "internal": internal,
                "external": external,
                "total":    internal + external,
            },
            "threat_intel_hits":   _rows(r_threat),
            "idps_signatures":     _rows(r_idps),
            "protocol_anomalies":  _rows(r_proto),
            "denied_combinations": _rows(r_combos),
            "nat_activity":        _rows(r_nat),
            "nat_top_sources":     _rows(r_nat_src),
            "dns_top_queries":     _rows(r_dns_top),
            # Real totals (no take cap)
            "total_deny_events":   _scalar(r_total_deny,    "Total"),
            "unique_deny_sources": _scalar(r_unique_src,    "UniqueCount"),
            "unique_rules_hit":    _scalar(r_unique_rules,  "UniqueCount"),
            "unique_talkers":      _scalar(r_unique_talkers,"UniqueCount"),
        }

    # ------------------------------------------------------------------
    # Public: Log Search (condition-based, custom time range)
    # ------------------------------------------------------------------

    async def search_logs(
        self,
        workspace_id: str,
        groups: List[Dict],
        start_time: str,
        end_time: str,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """
        Build and execute a KQL query from structured condition groups.

        Each group: {conditions: [{field, operator, value}], intra_connector, inter_connector}
        Supports full boolean grouping: A and (B or C), (A and B) or (C and D), etc.

        Fields: source_ip, dest_ip, dest_port, dest_fqdn, protocol, action,
                rule_name, rule_collection, rule_group, threat_category, type
        """
        # 0 = no cap (Max)
        effective_limit = 100_000 if limit == 0 else limit
        mgmt_token = await asyncio.to_thread(self._get_sp_token)
        la_token   = await asyncio.to_thread(self._get_sp_la_token)
        workspace_guid = await self._resolve_workspace_guid(workspace_id, mgmt_token)
        url = f"https://api.loganalytics.io/v1/workspaces/{workspace_guid}/query"

        FIELD_MAP = {
            "source_ip":        ("SourceIp",          "SourceIp"),
            "dest_ip":          ("DestinationIp",      "DestinationIp"),
            "dest_port":        ("DestinationPort",    "DestinationPort"),
            "dest_fqdn":        ("TargetUrl",          "TargetUrl"),
            "protocol":         ("Protocol",           "Protocol"),
            "action":           ("Action",             "Action"),
            "rule_name":        ("Rule",               "Rule"),
            "rule_collection":  ("RuleCollection",     "RuleCollection"),
            "rule_group":       ("RuleCollectionGroup","RuleCollectionGroup"),
            "threat_category":  ("ThreatDescription",  "ThreatDescription"),
        }

        IP_COLS = {"SourceIp", "DestinationIp"}

        def _kql_op(col: str, operator: str, value: str) -> str:
            v_safe = value.replace('"', '').strip()
            op = operator.lower()
            # CIDR value on an IP column → use ipv4_is_in_range instead of string equality
            is_cidr = "/" in v_safe and col in IP_COLS
            if op in ("matches_in", "exactly_in"):
                items = [i.strip().replace('"', '') for i in value.split(',') if i.strip()]
                item_op = "is_exactly" if op == "exactly_in" else "matches"
                clauses = [_kql_op(col, item_op, item) for item in items]
                return '(' + ' or '.join(clauses) + ')' if clauses else '(1==0)'

            if op in ("equals", "matches"):
                return f'ipv4_is_in_range({col}, "{v_safe}")' if is_cidr else f'{col} =~ "{v_safe}"'
            if op == "is_exactly":
                # /32 and bare IP are the same host
                if col in IP_COLS:
                    bare = v_safe[:-3] if v_safe.endswith('/32') else v_safe
                    return f'({col} == "{bare}" or {col} == "{bare}/32")'
                return f'{col} == "{v_safe}"'
            if op == "not_equals":
                return f'not(ipv4_is_in_range({col}, "{v_safe}"))' if is_cidr else f'{col} !~ "{v_safe}"'
            if op == "contains":      return f'{col} contains "{v_safe}"'
            if op == "not_contains":  return f'{col} !contains "{v_safe}"'
            if op == "starts_with":   return f'{col} startswith "{v_safe}"'
            if op == "ends_with":     return f'{col} endswith "{v_safe}"'
            return f'{col} contains "{v_safe}"'

        NUMERIC_COLS = {"DestinationPort"}

        def _kql_op_for_col(col: str, operator: str, value: str) -> str:
            # Numeric cols need tostring() for string operators; IP cols keep raw name for ipv4_is_in_range
            effective_col = f"tostring({col})" if col in NUMERIC_COLS else col
            return _kql_op(effective_col, operator, value)

        def _build_arm_filter_groups(col_overrides: dict) -> str:
            """
            Build a KQL | where clause from condition groups for a specific table arm.

            col_overrides: maps field name → column name override for this arm.
              "_skip_" means this arm has no such column.
              AND-group: any _skip_ field → whole group is (1==0) (never matches).
              OR-group:  _skip_ fields are dropped; if all drop → (1==0).

            type field is always excluded here — applied post-union via post_filter.
            """
            group_kql: List[tuple] = []  # (kql_expr, inter_connector)

            for group in groups:
                conds = [
                    c for c in group.get("conditions", [])
                    if c.get("value", "").strip() and c.get("field") != "type"
                ]
                if not conds:
                    continue

                intra = group.get("intra_connector", "AND").upper()
                inter = group.get("inter_connector", "AND").upper()

                # Resolve each condition to a KQL expression or None (= false for this arm)
                cond_exprs: List[str | None] = []
                for cond in conds:
                    field = cond.get("field", "")
                    col = col_overrides.get(field) or FIELD_MAP.get(field, ("Action", "Action"))[0]
                    if col == "_skip_":
                        cond_exprs.append(None)
                    else:
                        cond_exprs.append(_kql_op_for_col(col, cond.get("operator", "contains"), cond.get("value", "")))

                if intra == "AND":
                    if any(e is None for e in cond_exprs):
                        group_kql.append(("(1==0)", inter))
                    else:
                        parts = [f"({e})" for e in cond_exprs]
                        group_kql.append((f"({' and '.join(parts)})" if len(parts) > 1 else parts[0], inter))
                else:  # OR
                    valid = [f"({e})" for e in cond_exprs if e is not None]
                    if not valid:
                        group_kql.append(("(1==0)", inter))
                    else:
                        group_kql.append((f"({' or '.join(valid)})" if len(valid) > 1 else valid[0], inter))

            if not group_kql:
                return ""

            result = group_kql[0][0]
            for i in range(1, len(group_kql)):
                connector = group_kql[i - 1][1].lower()
                result = f"({result}) {connector} ({group_kql[i][0]})"
            return f"| where {result}"

        # Post-union filter for 'type' field (RuleType is a projected column, not in source tables)
        type_conds = [
            c for g in groups for c in g.get("conditions", [])
            if c.get("field") == "type" and c.get("value", "").strip()
        ]
        if type_conds:
            post_filter = "| where " + " and ".join(
                f"({_kql_op('RuleType', c.get('operator', 'equals'), c['value'])})" for c in type_conds
            )
        else:
            post_filter = ""

        filter_network     = _build_arm_filter_groups({"dest_fqdn": "_skip_", "threat_category": "_skip_"})
        filter_application = _build_arm_filter_groups({
            "dest_ip":         "_skip_",
            "dest_fqdn":       'coalesce(Fqdn, TargetUrl, "")',
            "threat_category": "_skip_",
        })
        filter_nat         = _build_arm_filter_groups({"dest_fqdn": "_skip_", "threat_category": "_skip_"})
        filter_threat      = _build_arm_filter_groups({
            "dest_fqdn":        "Fqdn",
            "threat_category":  "ThreatDescription",
            "rule_name":        "_skip_",
            "rule_collection":  "_skip_",
            "rule_group":       "_skip_",
        })

        ts_start_safe = start_time.replace('"','')
        ts_end_safe   = end_time.replace('"','')
        time_filter   = f"TimeGenerated between (datetime({ts_start_safe}) .. datetime({ts_end_safe}))"

        # Run each table as a separate query so every returned row is a self-contained
        # data point from exactly one log entry — no union schema ambiguity.
        # Each tuple: (label, data_kql, count_kql)
        ARMS = [
            ("Network",
             f"""AZFWNetworkRule
| where {time_filter}
{filter_network}
| project
    TimeGenerated,
    RuleType            = "Network",
    Action              = tostring(Action),
    SourceIp            = tostring(SourceIp),
    DestinationIp       = tostring(DestinationIp),
    DestinationPort     = tostring(DestinationPort),
    Protocol            = tostring(Protocol),
    Rule                = tostring(Rule),
    RuleCollection      = tostring(RuleCollection),
    RuleCollectionGroup = tostring(RuleCollectionGroup),
    TargetUrl           = "",
    ThreatDescription   = ""
| order by TimeGenerated desc
| take {effective_limit}""",
             f"""AZFWNetworkRule
| where {time_filter}
{filter_network}
| count"""),
            ("Application",
             f"""AZFWApplicationRule
| where {time_filter}
{filter_application}
| project
    TimeGenerated,
    RuleType            = "Application",
    Action              = tostring(Action),
    SourceIp            = tostring(SourceIp),
    DestinationIp       = "",
    DestinationPort     = tostring(DestinationPort),
    Protocol            = tostring(Protocol),
    Rule                = tostring(Rule),
    RuleCollection      = tostring(RuleCollection),
    RuleCollectionGroup = tostring(RuleCollectionGroup),
    TargetUrl           = tostring(coalesce(Fqdn, TargetUrl, "")),
    ThreatDescription   = ""
| order by TimeGenerated desc
| take {effective_limit}""",
             f"""AZFWApplicationRule
| where {time_filter}
{filter_application}
| count"""),
            ("NAT",
             f"""AZFWNatRule
| where {time_filter}
{filter_nat}
| project
    TimeGenerated,
    RuleType            = "NAT",
    Action              = "DNAT",
    SourceIp            = tostring(SourceIp),
    DestinationIp       = tostring(DestinationIp),
    DestinationPort     = tostring(DestinationPort),
    Protocol            = tostring(Protocol),
    Rule                = tostring(Rule),
    RuleCollection      = tostring(RuleCollection),
    RuleCollectionGroup = tostring(RuleCollectionGroup),
    TargetUrl           = "",
    ThreatDescription   = ""
| order by TimeGenerated desc
| take {effective_limit}""",
             f"""AZFWNatRule
| where {time_filter}
{filter_nat}
| count"""),
            ("ThreatIntel",
             f"""AZFWThreatIntel
| where {time_filter}
{filter_threat}
| project
    TimeGenerated,
    RuleType            = "ThreatIntel",
    Action              = tostring(Action),
    SourceIp            = tostring(SourceIp),
    DestinationIp       = tostring(DestinationIp),
    DestinationPort     = tostring(DestinationPort),
    Protocol            = tostring(Protocol),
    Rule                = "ThreatIntel",
    RuleCollection      = "",
    RuleCollectionGroup = "",
    TargetUrl           = tostring(coalesce(Fqdn, "")),
    ThreatDescription   = tostring(coalesce(ThreatDescription, ""))
| order by TimeGenerated desc
| take {effective_limit}""",
             f"""AZFWThreatIntel
| where {time_filter}
{filter_threat}
| count"""),
        ]

        def _run_kql(kql: str):
            resp = requests.post(url, headers=_make_headers(la_token), json={"query": kql}, timeout=60)
            if resp.status_code != 200:
                return []
            try:
                t = resp.json()["tables"][0]
                cols = [c["name"] for c in t["columns"]]
                return [dict(zip(cols, r)) for r in t.get("rows", [])]
            except Exception:
                return []

        def _run_count(kql: str) -> int:
            resp = requests.post(url, headers=_make_headers(la_token), json={"query": kql}, timeout=60)
            if resp.status_code != 200:
                return 0
            try:
                rows = resp.json()["tables"][0]["rows"]
                return int(rows[0][0]) if rows else 0
            except Exception:
                return 0

        # Run data queries and count queries in parallel (8 total)
        # ARM_TYPE_MAP: maps RuleType label → index in ARMS so type filter can pick the right counts
        ARM_TYPE_MAP = {"Network": 0, "Application": 1, "NAT": 2, "ThreatIntel": 3}
        data_tasks  = [asyncio.to_thread(_run_kql,   data_kql)  for _, data_kql, _         in ARMS]
        count_tasks = [asyncio.to_thread(_run_count, count_kql) for _, _,        count_kql in ARMS]
        all_results = await asyncio.gather(*data_tasks, *count_tasks)
        arm_results   = all_results[:len(ARMS)]
        arm_counts    = all_results[len(ARMS):]

        # Merge rows, apply post-union type filter if present, sort, and limit
        all_entries: list = []
        for rows in arm_results:
            all_entries.extend(rows)

        total_matched = sum(arm_counts)

        if post_filter:
            import re as _re
            m = _re.search(r'\| where (.+)', post_filter)
            if m:
                type_filter_str = m.group(1)
                wanted = set(_re.findall(r'"([^"]+)"', type_filter_str))
                if wanted:
                    all_entries = [
                        e for e in all_entries
                        if any(e.get("RuleType", "").lower() == w.lower() for w in wanted)
                    ]
                    # Sum only the arm counts that match the wanted types — exact count regardless of display limit
                    total_matched = sum(
                        arm_counts[ARM_TYPE_MAP[t]]
                        for t in wanted
                        if t in ARM_TYPE_MAP
                    )

        # Sort descending by TimeGenerated and enforce display limit
        all_entries.sort(key=lambda e: e.get("TimeGenerated") or "", reverse=True)
        all_entries = all_entries[:effective_limit]

        return {
            "total": len(all_entries),
            "matched": total_matched,
            "entries": all_entries,
            "start_time": start_time,
            "end_time": end_time,
        }


    # ------------------------------------------------------------------
    # Public: execute raw KQL against a Log Analytics workspace
    # ------------------------------------------------------------------

    async def execute_kql(
        self,
        workspace_id: str,
        kql: str,
        start_time: str,
        end_time: str,
        limit: int = 5000,
    ) -> Dict[str, Any]:
        """
        Execute arbitrary KQL against a Log Analytics workspace.
        Injects a TimeGenerated time-range guard unless the query already contains one.
        Returns generic columns + rows so the frontend can render any shape.
        """
        la_token     = await asyncio.to_thread(self._get_sp_la_token)
        mgmt_token   = await asyncio.to_thread(self._get_sp_token)
        workspace_guid = await self._resolve_workspace_guid(workspace_id, mgmt_token)
        url = f"https://api.loganalytics.io/v1/workspaces/{workspace_guid}/query"

        ts_start = start_time.replace('"', '')
        ts_end   = end_time.replace('"', '')
        # Pass time as a separate `timespan` parameter — same as Azure Portal.
        # LA applies it as a server-side gate on TimeGenerated, never touching the KQL text.
        # If the user's KQL already has a TimeGenerated filter, LA takes the intersection
        # (more restrictive wins) — identical behaviour to the Portal warning
        # "Query includes a time filter that overrides the time range selector".
        timespan = f"{ts_start}/{ts_end}"

        user_kql = kql.strip()

        # Enforce row cap — add | take N only if the query doesn't already have one
        if "| take" not in user_kql.lower():
            user_kql = user_kql + f"\n| take {limit}"

        def _run():
            resp = requests.post(
                url,
                headers=_make_headers(la_token),
                json={"query": user_kql, "timespan": timespan},
                timeout=120,
            )
            if resp.status_code != 200:
                return {"error": resp.json().get("error", {}).get("message", "Query failed"), "columns": [], "rows": []}
            try:
                t = resp.json()["tables"][0]
                return {
                    "columns": [c["name"] for c in t["columns"]],
                    "rows": t.get("rows", []),
                    "error": None,
                }
            except Exception as exc:
                return {"error": str(exc), "columns": [], "rows": []}

        result = await asyncio.to_thread(_run)
        return {
            "columns": result["columns"],
            "rows": result["rows"],
            "total": len(result["rows"]),
            "error": result["error"],
            "kql_executed": user_kql,
            "timespan": timespan,
        }

    # ------------------------------------------------------------------
    # Public: find firewall linked to a policy + detect diagnostic workspace
    # ------------------------------------------------------------------

    async def get_policy_diagnostic_info(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
        management_token: str,
    ) -> Dict[str, Any]:
        """
        Given a firewall policy:
          1. Find the Azure Firewall(s) linked to this policy in the subscription.
          2. For each firewall, fetch its diagnostic settings.
          3. Return the workspace(s) ordered by preference: Dedicated > AzureDiagnostics.

        Uses the management token directly (the service principal token) so no user
        token is required for this side-channel call.
        """
        token = management_token

        # Step 1 – find firewalls that reference this policy
        policy_resource_id = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Network/firewallPolicies/{policy_name}"
        ).lower()

        firewalls = await self.list_firewalls(subscription_id)
        linked_firewalls = [
            fw for fw in firewalls
            if fw.get("policy_id", "").lower() == policy_resource_id
        ]

        if not linked_firewalls:
            return {
                "firewall_found": False,
                "firewall_name": None,
                "firewall_id": None,
                "has_diagnostics": False,
                "primary_workspace": None,
                "fallback_workspace": None,
                "log_mode": None,
                "all_settings": [],
                "message": "No Azure Firewall found linked to this policy in the subscription.",
            }

        # Use the first linked firewall
        fw = linked_firewalls[0]
        fw_id = fw["id"]

        # Step 2 – fetch diagnostic settings for the firewall resource
        from services.diagnostic_settings_service import DiagnosticSettingsService
        diag_svc = DiagnosticSettingsService()

        # Build a minimal user_info dict with the SP token so DiagnosticSettingsService
        # uses the management REST call (it only needs 'management_token' or 'token')
        user_info_stub = {"management_token": token, "token": token}
        diag_result = await diag_svc.get_log_mode_for_resource(
            resource_id=fw_id,
            user_info=user_info_stub,
            subscription_id=subscription_id,
        )

        primary = diag_result.get("primary")
        fallback = diag_result.get("fallback")

        return {
            "firewall_found": True,
            "firewall_name": fw["name"],
            "firewall_id": fw_id,
            "firewall_resource_group": fw["resource_group"],
            "has_diagnostics": diag_result.get("has_diagnostics", False),
            "primary_workspace": primary.get("workspace_id") if primary else None,
            "primary_workspace_name": primary.get("workspace_name") if primary else None,
            "log_mode": primary.get("mode") if primary else None,
            "fallback_workspace": fallback.get("workspace_id") if fallback else None,
            "fallback_workspace_name": fallback.get("workspace_name") if fallback else None,
            "all_settings": diag_result.get("all_settings", []),
            "message": (
                f"Found firewall '{fw['name']}' with {primary['mode']} diagnostics → "
                f"workspace '{primary.get('workspace_name')}'"
                if primary else
                f"Found firewall '{fw['name']}' but no Log Analytics diagnostic settings configured."
            ),
        }

    # ------------------------------------------------------------------
    # Public: firewall summary stats
    # ------------------------------------------------------------------

    async def get_policy_summary(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
    ) -> Dict[str, Any]:
        """Return a quick summary of the policy for dashboard KPI cards."""
        rules = await self._flatten_rules(subscription_id, resource_group, policy_name)
        allow = sum(1 for r in rules if r["action"].lower() == "allow")
        deny = sum(1 for r in rules if r["action"].lower() in ("deny", "dndeny"))
        network = sum(1 for r in rules if "network" in r["rule_type"].lower())
        application = sum(1 for r in rules if "application" in r["rule_type"].lower())
        nat = sum(1 for r in rules if "nat" in r["rule_type"].lower())
        groups = len({r["rule_group"] for r in rules})

        return {
            "total_rules": len(rules),
            "allow_rules": allow,
            "deny_rules": deny,
            "network_rules": network,
            "application_rules": application,
            "nat_rules": nat,
            "rule_collection_groups": groups,
        }

    # ------------------------------------------------------------------
    # Azure Monitor Metrics for a specific firewall resource
    # ------------------------------------------------------------------

    async def get_firewall_metrics(
        self,
        subscription_id: str,
        resource_group: str,
        firewall_name: str,
        metric_name: str,
        aggregation: str,          # Avg | Min | Max | Total | Count
        interval: str,             # ISO 8601 e.g. PT1M PT5M PT1H P1D
        timespan_start: str,       # ISO 8601 UTC
        timespan_end: str,         # ISO 8601 UTC
        split_by: str = "",        # dimension name, e.g. "Protocol" or ""
    ) -> Dict[str, Any]:
        """
        Call Azure Monitor Metrics REST API for an Azure Firewall resource.
        Returns { metric, aggregation, unit, series: [{name, data: [{t, v}]}] }

        Azure Monitor Metrics API:
          GET {resource_id}/providers/microsoft.insights/metrics
          ?api-version=2018-01-01
          &metricnames={metric}
          &aggregation={aggregation}
          &interval={interval}
          &timespan={start}/{end}
          &$filter={dim} eq '*'    (for dimension splitting)
        """
        token = await asyncio.to_thread(self._get_sp_token)
        resource_id = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Network/azureFirewalls/{firewall_name}"
        )
        url = (
            f"{_MANAGEMENT_API}{resource_id}"
            f"/providers/microsoft.insights/metrics"
            f"?api-version=2018-01-01"
            f"&metricnames={metric_name}"
            f"&aggregation={aggregation}"
            f"&interval={interval}"
            f"&timespan={timespan_start}/{timespan_end}"
        )
        if split_by:
            url += f"&$filter={split_by} eq '*'"

        headers = _make_headers(token)
        resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        agg_key = aggregation.lower()
        # Azure Monitor returns "total" as "total", "average" as "average" etc.
        # Map our UI labels to the JSON key in timeseries data
        agg_map = {
            "average": "average",
            "avg":     "average",
            "total":   "total",
            "sum":     "total",
            "minimum": "minimum",
            "min":     "minimum",
            "maximum": "maximum",
            "max":     "maximum",
            "count":   "count",
        }
        json_key = agg_map.get(agg_key, agg_key)

        unit = ""
        series: List[Dict] = []

        for metric_def in data.get("value", []):
            unit = metric_def.get("unit", "")
            for ts in metric_def.get("timeseries", []):
                # Build series name from dimension metadata
                dim_vals = [
                    f"{md.get('name', {}).get('value', '')}={md.get('value', '')}"
                    for md in ts.get("metadatavalues", [])
                ]
                name = " · ".join(dim_vals) if dim_vals else metric_name
                data_points = []
                for pt in ts.get("data", []):
                    v = pt.get(json_key)
                    if v is not None:
                        data_points.append({"t": pt["timeStamp"], "v": round(v, 4)})
                if data_points:
                    series.append({"name": name, "data": data_points})

        return {
            "metric":      metric_name,
            "aggregation": aggregation,
            "unit":        unit,
            "interval":    interval,
            "timespan_start": timespan_start,
            "timespan_end":   timespan_end,
            "split_by":    split_by,
            "series":      series,
        }

    # ------------------------------------------------------------------
    # Firewall configuration (full ARM resource properties)
    # ------------------------------------------------------------------

    async def get_firewall_configuration(
        self,
        subscription_id: str,
        resource_group: str,
        firewall_name: str,
    ) -> Dict[str, Any]:
        """
        Fetch the full Azure Firewall ARM resource and return a structured
        configuration snapshot covering: identity, SKU, availability zones,
        IP configuration / SNAT public IPs, forced-tunnelling management NIC,
        network (VNet / subnet), threat intelligence, DNS proxy, IDPS (Premium),
        TLS inspection (Premium), auto-learn SNAT private ranges, policy linkage,
        and provisioning state.
        """
        token = await asyncio.to_thread(self._get_sp_token)
        url = (
            f"{_MANAGEMENT_API}/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Network/azureFirewalls/{firewall_name}"
            f"?api-version={_NETWORK_API_VERSION}"
        )
        headers = _make_headers(token)
        resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=30)
        resp.raise_for_status()
        fw = resp.json()

        props = fw.get("properties", {})

        # Log all top-level property keys and additionalProperties so we can see
        # exactly where enableFatFlowLogging / enableTcpConnectionLogging live.
        import logging as _logging
        _log = _logging.getLogger(__name__)
        _log.info(
            "FW config – props keys: %s | additionalProperties: %s",
            sorted(props.keys()),
            props.get("additionalProperties", {}),
        )
        ip_configs = []
        for ipc in props.get("ipConfigurations", []):
            p = ipc.get("properties", {})
            subnet_id = p.get("subnet", {}).get("id", "")
            subnet_name = subnet_id.split("/subnets/")[-1] if "/subnets/" in subnet_id else ""
            vnet_name   = subnet_id.split("/virtualNetworks/")[-1].split("/")[0] if "/virtualNetworks/" in subnet_id else ""
            ip_configs.append({
                "name":       ipc.get("name"),
                "public_ip":  p.get("publicIPAddress", {}).get("id", "").split("/")[-1],
                "private_ip": p.get("privateIPAddress"),
                "subnet":     subnet_name,
                "vnet":       vnet_name,
            })

        # ── Management NIC (forced tunnelling) ───────────────────────
        mgmt_nic = props.get("managementIpConfiguration")
        management_ip = None
        if mgmt_nic:
            mp = mgmt_nic.get("properties", {})
            management_ip = {
                "name":      mgmt_nic.get("name"),
                "public_ip": mp.get("publicIPAddress", {}).get("id", "").split("/")[-1],
                "private_ip": mp.get("privateIPAddress"),
            }

        # ── Availability zones ────────────────────────────────────────
        zones = fw.get("zones", [])

        # ── SKU ───────────────────────────────────────────────────────
        sku = props.get("sku", {})
        sku_name = sku.get("name", "AZFW_VNet")   # AZFW_VNet | AZFW_Hub
        sku_tier = sku.get("tier", "Standard")    # Basic | Standard | Premium

        # ── Throughput capacity per SKU (docs) ────────────────────────
        throughput_map = {
            "Basic":    "250 Mbps (fixed, 2 VM instances)",
            "Standard": "Up to 30 Gbps (auto-scale)",
            "Premium":  "Up to 100 Gbps (auto-scale)",
        }
        max_throughput = throughput_map.get(sku_tier, "Unknown")

        # ── Threat Intelligence ───────────────────────────────────────
        threat_intel_mode = props.get("threatIntelMode", "Alert")
        threat_intel_whitelist = props.get("threatIntelWhitelist", {})

        # ── Policy linkage (must be resolved before DNS fetch) ────────
        policy_id   = props.get("firewallPolicy", {}).get("id", "")
        policy_name = policy_id.split("/firewallPolicies/")[-1] if "/firewallPolicies/" in policy_id else ""

        # ── DNS proxy, IDPS, TLS — all live on the firewall POLICY ─────────
        dns_proxy_enabled = False
        dns_servers: List[str] = []
        idps_config: dict = {}
        tls_inspection: dict = {}
        if policy_id:
            try:
                pol_url = f"{_MANAGEMENT_API}{policy_id}?api-version={_NETWORK_API_VERSION}"
                pol_resp = await asyncio.to_thread(requests.get, pol_url, headers=headers, timeout=30)
                if pol_resp.ok:
                    pol_props = pol_resp.json().get("properties", {})
                    pol_dns = pol_props.get("dnsSettings", {})
                    dns_proxy_enabled = pol_dns.get("enableProxy", False)
                    dns_servers = pol_dns.get("servers", [])

                    # IDPS (Premium only) — properties.intrusionDetection
                    idps_raw = pol_props.get("intrusionDetection") or {}
                    if idps_raw:
                        idps_cfg = idps_raw.get("configuration") or {}
                        idps_config = {
                            "mode":    idps_raw.get("mode"),       # Off | Alert | Deny
                            "profile": idps_raw.get("profile"),    # Core | Emerging | Extended | Off
                            "signature_overrides_count": len(idps_cfg.get("signatureOverrides") or []),
                            "bypass_rules_count":        len(idps_cfg.get("bypassTrafficSettings") or []),
                        }

                    # TLS inspection (Premium only) — properties.transportSecurity
                    tls_raw = pol_props.get("transportSecurity") or {}
                    if tls_raw:
                        ca = tls_raw.get("certificateAuthority") or {}
                        tls_inspection = {
                            "enabled":    True,
                            "ca_name":    ca.get("name"),
                            "kv_secret":  ca.get("keyVaultSecretId"),
                        }
            except Exception:
                pass  # non-fatal; leave defaults

        # ── Hub / VNet deployment type ────────────────────────────────
        virtual_hub = props.get("virtualHub", {}).get("id", "")
        deployment_type = "VNet" if not virtual_hub else "Secured vHub"

        # ── Additional properties (Premium / IDPS etc.) ───────────────
        additional = props.get("additionalProperties", {})
        idps_mode = additional.get("Network.IDPS.SignatureOverrides.Mode", None)

        # Fat flow and flow trace are direct boolean properties on the firewall ARM resource
        # (set via enableFatFlowLogging / enableTcpConnectionLogging), not in additionalProperties.
        fat_flow_logging   = props.get("enableFatFlowLogging", False)
        flow_trace_logging = props.get("enableTcpConnectionLogging", False)

        # ── Autoscale / prescaling configuration ─────────────────────
        # props.autoscaleConfiguration is set when the user configures capacity
        # reservation (prescaling). null values mean "service default".
        autoscale_cfg = props.get("autoscaleConfiguration") or {}
        autoscale_min_capacity = autoscale_cfg.get("minCapacity")   # int or None
        autoscale_max_capacity = autoscale_cfg.get("maxCapacity")   # int or None

        # ── SNAT private ranges (auto-learn / manual) ─────────────────
        snat_private_ranges = props.get("snatPrivateRanges", [])

        # ── Provisioning / operational state ─────────────────────────
        provisioning_state = props.get("provisioningState", "Unknown")

        # ── Maintenance configuration (Microsoft.Maintenance) ─────────
        # Customer-controlled maintenance is stored as a configurationAssignment
        # linked to the firewall resource via the Maintenance RP.
        maintenance_config = None
        fw_resource_id = fw.get("id", "")
        try:
            maint_url = (
                f"{_MANAGEMENT_API}{fw_resource_id}"
                f"/providers/Microsoft.Maintenance/configurationAssignments"
                f"?api-version=2023-09-01-preview"
            )
            maint_resp = await asyncio.to_thread(requests.get, maint_url, headers=headers, timeout=30)
            if maint_resp.ok:
                assignments = maint_resp.json().get("value", [])
                if assignments:
                    # Use the first assignment; firewalls support only one
                    a = assignments[0]
                    a_props = a.get("properties", {})
                    cfg_id = a_props.get("maintenanceConfigurationId", "")
                    # Fetch the referenced maintenance configuration for schedule details
                    if cfg_id:
                        cfg_resp = await asyncio.to_thread(
                            requests.get,
                            f"{_MANAGEMENT_API}{cfg_id}?api-version=2023-09-01-preview",
                            headers=headers,
                            timeout=30,
                        )
                        if cfg_resp.ok:
                            cfg = cfg_resp.json()
                            cfg_props = cfg.get("properties", {})
                            win = cfg_props.get("maintenanceWindow", {})
                            maintenance_config = {
                                "name":               cfg.get("name"),
                                "start_date_time":    win.get("startDateTime"),
                                "expiration_date_time": win.get("expirationDateTime"),
                                "duration":           win.get("duration"),
                                "time_zone":          win.get("timeZone"),
                                "recur_every":        win.get("recurEvery"),
                            }
        except Exception:
            pass  # non-fatal

        return {
            # Identity
            "name":              fw.get("name"),
            "location":          fw.get("location"),
            "resource_group":    resource_group,
            "subscription_id":   subscription_id,
            "resource_id":       fw.get("id"),
            # SKU & capacity
            "sku_name":          sku_name,
            "sku_tier":          sku_tier,
            "max_throughput":    max_throughput,
            "deployment_type":   deployment_type,
            # HA
            "availability_zones": zones,
            "ha_mode":           "Active-Active (multi-instance)" if zones else "Active-Active (single-zone)",
            # Network
            "ip_configurations":  ip_configs,
            "management_ip":      management_ip,
            "forced_tunneling":   management_ip is not None,
            "virtual_hub":        virtual_hub.split("/")[-1] if virtual_hub else None,
            # Policy
            "policy_name":        policy_name,
            # Threat Intelligence
            "threat_intel_mode":      threat_intel_mode,
            "threat_intel_whitelist_ips":   threat_intel_whitelist.get("ipAddresses", []),
            "threat_intel_whitelist_fqdns": threat_intel_whitelist.get("fqdns", []),
            # DNS
            "dns_proxy_enabled":  dns_proxy_enabled,
            "dns_servers":        dns_servers,
            # Premium features
            "idps_mode":          idps_mode,
            "fat_flow_logging":   bool(fat_flow_logging),
            "flow_trace_logging": bool(flow_trace_logging),
            # SNAT
            "snat_private_ranges": snat_private_ranges,
            # State
            "provisioning_state": provisioning_state,
            # Autoscale / prescaling (live from ARM; None = service default)
            "autoscale_min_capacity": autoscale_min_capacity,
            "autoscale_max_capacity": autoscale_max_capacity,
            # Maintenance configuration (None if not configured)
            "maintenance_config": maintenance_config,
            # Resource tags
            "tags": fw.get("tags") or {},
            # IDPS (from policy; empty dict if not Premium / not configured)
            "idps_config": idps_config,
            # TLS inspection (from policy; empty dict if not Premium / not configured)
            "tls_inspection": tls_inspection,
        }


# Singleton
firewall_service = FirewallService()
