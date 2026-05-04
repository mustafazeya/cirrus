"""
Packet Walk Service
===================
Simulates the full Azure networking path for a given 5-tuple:
  source (IP or CIDR), destination (IP/CIDR/FQDN), port, protocol

Pipeline:
  1. Source resolution     — NIC → subnet → VNet, or gateway ingress
  2. Route evaluation      — UDR (Resource Graph) + BGP (getLearnedRoutes) with
                             correct longest-prefix-match semantics
  3. NSG evaluation        — subnet + NIC outbound rules
  4. Firewall evaluation   — full rule priority ordering (group → collection → rule)
                             with IP group resolution already done by _flatten_rules
  5. AI reasoning          — OpenAI synthesises all findings into a verdict with
                             near-miss explanation and remediation advice

Limitations disclosed transparently in results:
  - BGP getLearnedRoutes is eventually consistent (~30-90 s on fresh gateway boot)
  - Dynamic routes via ExpressRoute FastPath may not appear in ARM
  - IPv6 not supported (Azure Firewall Premium only, out of scope)
"""

import asyncio
import ipaddress
import json
from config import settings
import logging
import socket
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_MGMT = "https://management.azure.com"
_ARG_URL = f"{_MGMT}/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01"
_NET_VER = "2024-05-01"
_ARG_SUB_BATCH = settings.ARG_SUB_BATCH  # sourced from config.py


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _net(cidr: str):
    if not cidr:
        return None
    try:
        return ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return None


def _ip_in_cidr(ip: str, cidr: str) -> bool:
    n = _net(cidr)
    if n is None:
        return False
    try:
        return ipaddress.ip_address(ip.strip()) in n
    except ValueError:
        return False


def _cidr_in_cidr(candidate: str, container: str) -> bool:
    """True if candidate subnet is fully within container."""
    nc = _net(candidate)
    nr = _net(container)
    if nc is None or nr is None:
        return False
    try:
        return nc.subnet_of(nr) or nc == nr
    except TypeError:
        return False


def _longest_prefix_match(routes: List[Dict], dest_ip: str) -> Optional[Dict]:
    """
    Find the route with the longest matching prefix for dest_ip.
    Routes must have an 'addressPrefix' key.
    Source priority (UDR > BGP > System) breaks ties at equal prefix length.
    """
    SOURCE_PRIORITY = {"User": 0, "BGP": 1, "VPNGateway": 1, "Default": 2}
    best: Optional[Dict] = None
    best_len = -1
    best_src_prio = 99

    for route in routes:
        prefix = route.get("addressPrefix", "")
        if not prefix:
            continue
        if prefix in ("0.0.0.0/0", "::/0", "*"):
            prefix_len = 0
        else:
            n = _net(prefix)
            if n is None:
                continue
            try:
                if ipaddress.ip_address(dest_ip.split("/")[0]) not in n:
                    continue
            except ValueError:
                continue
            prefix_len = n.prefixlen

        src_prio = SOURCE_PRIORITY.get(route.get("source", "Default"), 2)
        if prefix_len > best_len or (prefix_len == best_len and src_prio < best_src_prio):
            best = route
            best_len = prefix_len
            best_src_prio = src_prio

    return best


def _resolve_fqdn(fqdn: str) -> List[str]:
    """Best-effort DNS resolution of an FQDN to IPs."""
    try:
        results = socket.getaddrinfo(fqdn, None)
        return list({r[4][0] for r in results})
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Resource Graph helpers
# ---------------------------------------------------------------------------

def _arg_query(token: str, query: str, subscriptions: List[str]) -> List[Dict]:
    body = {"query": query, "subscriptions": subscriptions, "options": {"$top": 1000}}
    resp = requests.post(_ARG_URL, headers=_headers(token), json=body, timeout=30)
    if resp.status_code != 200:
        logger.warning("ARG query failed %s: %s", resp.status_code, resp.text[:200])
        return []
    data = resp.json()
    return data.get("data", {}).get("rows", []) or data.get("data", []) or []


def _arg_query_with_cols(token: str, query: str, subscriptions: List[str]) -> Tuple[List[str], List[List]]:
    """
    Execute a Resource Graph query across the given subscriptions.
    Batches into groups of _ARG_SUB_BATCH to avoid payload limits.
    Follows $skipToken pagination so mv-expand queries on large resources
    (e.g. route tables with 300+ entries) return all rows, not just the first page.
    Returns (columns, rows) — both empty on any failure.
    """
    all_cols: List[str] = []
    all_rows: List[List] = []

    batches = [
        subscriptions[i: i + _ARG_SUB_BATCH]
        for i in range(0, max(len(subscriptions), 1), _ARG_SUB_BATCH)
    ]

    for batch in batches:
        skip_token: Optional[str] = None
        page = 0
        while True:
            page += 1
            options: Dict[str, Any] = {"$top": 1000}
            if skip_token:
                options["$skipToken"] = skip_token
            body = {"query": query, "subscriptions": batch, "options": options}
            try:
                resp = requests.post(_ARG_URL, headers=_headers(token), json=body, timeout=30)
            except Exception as exc:
                logger.warning("ARG request exception: %s", exc)
                break
            if resp.status_code != 200:
                logger.warning("ARG query failed %s (batch %d subs, page %d): %s",
                               resp.status_code, len(batch), page, resp.text[:300])
                break
            payload = resp.json()
            raw = payload.get("data", {})
            if isinstance(raw, list):
                if raw:
                    cols = list(raw[0].keys())
                    rows = [list(item.values()) for item in raw]
                    if not all_cols:
                        all_cols = cols
                    all_rows.extend(rows)
            if isinstance(raw, list):
                if raw:
                    cols = list(raw[0].keys())
                    rows = [list(item.values()) for item in raw]
                    if not all_cols:
                        all_cols = cols
                    all_rows.extend(rows)
            elif isinstance(raw, dict):
                cols = [c["name"] for c in raw.get("columns", [])]
                rows = raw.get("rows", [])
                if rows:
                    if not all_cols:
                        all_cols = cols
                    all_rows.extend(rows)
            else:
                logger.warning("ARG unexpected data type: %s — %s", type(raw).__name__, str(raw)[:100])

            skip_token = payload.get("$skipToken")
            if not skip_token:
                break
            logger.debug("ARG pagination: fetching page %d (skip_token present)", page + 1)

    return all_cols, all_rows


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------

class PacketWalkService:
    def __init__(self, firewall_service):
        self._fw = firewall_service

    def _get_token(self) -> str:
        return self._fw._get_sp_token()

    # ------------------------------------------------------------------
    # Source resolution
    # ------------------------------------------------------------------

    def _resolve_source(
        self, source: str, subscription_ids: List[str], token: str
    ) -> Dict[str, Any]:
        """
        Resolve source IP/CIDR to its Azure context.
        Returns a dict with keys: type, subnet_id, subnet_cidr, vnet_id,
        route_table_id, nsg_id, nic_id, detail, source_ip (representative IP)
        """
        # Normalise — pick the network address as representative IP for lookups
        # Accept '*' or 'any' as shorthand for 0.0.0.0/0
        if source.strip() in ("*", "any", "Any"):
            source = "0.0.0.0/0"
        n = _net(source)
        if n is None:
            return {"type": "unknown", "detail": f"Cannot parse source: {source}"}

        is_cidr = "/" in source and n.prefixlen < 32
        rep_ip = str(n.network_address) if is_cidr else source.split("/")[0]

        # 1 — try exact NIC match (single IP only)
        # Use mv-expand on ipConfigurations — ARG [0] array indexing is unreliable
        # when NICs have secondary IPs or when the serialised form differs between regions.
        if not is_cidr:
            cols, rows = _arg_query_with_cols(token, f"""
                resources
                | where type =~ 'microsoft.network/networkinterfaces'
                | mv-expand ipConfig = properties.ipConfigurations
                | extend privateIP = tostring(ipConfig.properties.privateIPAddress)
                | where privateIP =~ '{rep_ip}'
                | project nicId=id,
                          subnetId=tostring(ipConfig.properties.subnet.id),
                          privateIP,
                          nsgId=tostring(properties.networkSecurityGroup.id),
                          nicName=name,
                          resourceGroup,
                          subscriptionId
                | limit 1
            """, subscription_ids)
            if rows:
                row = dict(zip(cols, rows[0]))
                subnet_info = self._resolve_subnet(row.get("subnetId", ""), token, subscription_ids)
                nic_label = row.get("nicName") or row.get("nicId", "").split("/")[-1]
                return {
                    "type": "azure_nic",
                    "nic_id": row.get("nicId"),
                    "nic_name": nic_label,
                    "resource_group": row.get("resourceGroup"),
                    "subscription_id": row.get("subscriptionId"),
                    "subnet_id": row.get("subnetId"),
                    "subnet_cidr": subnet_info.get("cidr"),
                    "vnet_id": subnet_info.get("vnet_id"),
                    "route_table_id": subnet_info.get("route_table_id"),
                    "nsg_id": row.get("nsgId") or subnet_info.get("nsg_id"),
                    "detail": (
                        f"Azure NIC '{nic_label}' ({rep_ip}) in subnet "
                        f"{subnet_info.get('name', '')} ({subnet_info.get('cidr', '')}), "
                        f"subscription {row.get('subscriptionId', '')[:8]}…"
                    ),
                }

        # 2 — try subnet containment (covers CIDRs and on-prem-looking IPs that belong to Azure subnets)
        # Use network_address(ip) and address_prefix comparison via KQL string ops — ipv4_is_in_range
        # is available in ARG queries (same engine as Log Analytics)
        cols, rows = _arg_query_with_cols(token, f"""
            resources
            | where type =~ 'microsoft.network/virtualnetworks'
            | mv-expand subnet = properties.subnets
            | where ipv4_is_in_range('{rep_ip}', tostring(subnet.properties.addressPrefix))
            | project vnetId=id, subnetId=tostring(subnet.id),
                      subnetName=tostring(subnet.name),
                      subnetCidr=tostring(subnet.properties.addressPrefix),
                      routeTableId=tostring(subnet.properties.routeTable.id),
                      nsgId=tostring(subnet.properties.networkSecurityGroup.id)
            | limit 1
        """, subscription_ids)
        if rows:
            row = dict(zip(cols, rows[0]))
            subnet_net = _net(row.get("subnetCidr", ""))
            # For CIDR sources: the source must be fully contained within the matched subnet.
            # If source is larger than the subnet (e.g. /12 vs /24), the network address of
            # the /12 happens to fall inside the /24 — but that's a false match.
            # Skip and fall through to external_unknown (and skip UDR evaluation).
            if is_cidr and subnet_net is not None:
                try:
                    if not n.subnet_of(subnet_net):
                        rows = []
                except TypeError:
                    rows = []

        if rows:
            row = dict(zip(cols, rows[0]))
            return {
                "type": "azure_subnet",
                "subnet_id": row.get("subnetId"),
                "subnet_cidr": row.get("subnetCidr"),
                "vnet_id": row.get("vnetId"),
                "route_table_id": row.get("routeTableId") or None,
                "nsg_id": row.get("nsgId") or None,
                "detail": f"IP falls within Azure subnet {row.get('subnetName')} ({row.get('subnetCidr')})",
            }

        # 3 — check for VPN/ER gateways (traffic enters Azure from outside)
        gw_info = self._find_gateway_for_source(rep_ip, subscription_ids, token)
        if gw_info:
            return {
                "type": "external_via_gateway",
                "gateway_id": gw_info.get("gateway_id"),
                "gateway_name": gw_info.get("gateway_name"),
                "gateway_type": gw_info.get("gateway_type"),
                "circuit_name": gw_info.get("circuit_name"),
                "hub_subnet_id": gw_info.get("gateway_subnet_id"),
                "route_table_id": gw_info.get("route_table_id"),
                "detail": gw_info.get("detail"),
                "bgp_routes": gw_info.get("bgp_routes", []),
            }

        return {
            "type": "external_unknown",
            "detail": f"Source {source} not found in Azure (no NIC, subnet, or gateway match). "
                      "If this is a broad CIDR spanning multiple subnets, routing and NSG evaluation "
                      "are skipped — firewall policy evaluation only.",
        }

    def _resolve_subnet(self, subnet_id: str, token: str, subscription_ids: List[str]) -> Dict:
        if not subnet_id:
            return {}
        cols, rows = _arg_query_with_cols(token, f"""
            resources
            | where type =~ 'microsoft.network/virtualnetworks'
            | mv-expand subnet = properties.subnets
            | where tostring(subnet.id) =~ '{subnet_id}'
            | project vnetId=id, subnetName=tostring(subnet.name),
                      cidr=tostring(subnet.properties.addressPrefix),
                      routeTableId=tostring(subnet.properties.routeTable.id),
                      nsgId=tostring(subnet.properties.networkSecurityGroup.id)
            | limit 1
        """, subscription_ids)
        if rows:
            row = dict(zip(cols, rows[0]))
            return {
                "vnet_id": row.get("vnetId"),
                "name": row.get("subnetName"),
                "cidr": row.get("cidr"),
                "route_table_id": row.get("routeTableId") or None,
                "nsg_id": row.get("nsgId") or None,
            }
        return {}

    def _find_gateway_for_source(
        self, source_ip: str, subscription_ids: List[str], token: str
    ) -> Optional[Dict]:
        """
        Find if source_ip could enter Azure via an ER or VPN gateway.
        Calls getLearnedRoutes on each gateway to check BGP advertisements.
        """
        cols, rows = _arg_query_with_cols(token, """
            resources
            | where type =~ 'microsoft.network/virtualnetworkgateways'
            | project gwId=id, gwName=name, gwType=properties.gatewayType,
                      subscriptionId=subscriptionId, resourceGroup,
                      gwSubnetId=tostring(properties.ipConfigurations[0].properties.subnet.id)
        """, subscription_ids)
        if not rows:
            return None

        gateways = [dict(zip(cols, r)) for r in rows]

        for gw in gateways:
            gw_id = gw.get("gwId", "")
            if not gw_id:
                continue
            try:
                # POST to trigger learned routes fetch
                url = f"{_MGMT}{gw_id}/getLearnedRoutes?api-version={_NET_VER}"
                resp = requests.post(url, headers=_headers(token), json={}, timeout=90)
                if resp.status_code not in (200, 202):
                    continue

                # Handle async operation (202 + Azure-AsyncOperation header)
                if resp.status_code == 202:
                    async_url = resp.headers.get("Azure-AsyncOperation") or resp.headers.get("Location")
                    if not async_url:
                        continue
                    for _ in range(12):  # max 60s wait
                        import time
                        time.sleep(5)
                        poll = requests.get(async_url, headers=_headers(token), timeout=15)
                        if poll.status_code == 200:
                            poll_data = poll.json()
                            if poll_data.get("status") == "Succeeded":
                                # ARM LRO result: properties.output.value holds the BGP table
                                output = poll_data.get("properties", {}).get("output", {})
                                routes = output.get("value", []) if isinstance(output, dict) else []
                                # Re-assign resp-like data for the extraction below
                                resp = type("_R", (), {"json": lambda s, r=routes: {"value": r}, "status_code": 200})()
                            break

                data = resp.json()
                routes = data.get("value", []) if isinstance(data, dict) else []

                for route in routes:
                    prefix = route.get("network", "")
                    if prefix and _cidr_in_cidr(source_ip + "/32", prefix):
                        gw_subnet_info = self._resolve_subnet(gw.get("gwSubnetId", ""), token, subscription_ids)
                        return {
                            "gateway_id": gw_id,
                            "gateway_name": gw.get("gwName"),
                            "gateway_type": str(gw.get("gwType", "")),
                            "gateway_subnet_id": gw.get("gwSubnetId"),
                            "route_table_id": gw_subnet_info.get("route_table_id"),
                            "circuit_name": route.get("sourcePeer", "unknown"),
                            "bgp_routes": routes,
                            "detail": (
                                f"Source {source_ip} matches BGP prefix {prefix} "
                                f"learned by gateway '{gw.get('gwName')}' "
                                f"(type: {gw.get('gwType')}, peer: {route.get('sourcePeer', 'unknown')})"
                            ),
                        }
            except Exception as e:
                logger.warning("getLearnedRoutes failed for %s: %s", gw_id, e)

        return None

    # ------------------------------------------------------------------
    # Route evaluation
    # ------------------------------------------------------------------

    def _fetch_all_gateway_routes(self, token: str, subscription_ids: List[str]) -> List[Dict]:
        """
        Fetch BGP-learned routes from all VPN/ER gateways visible to the SP.
        Used when a subnet has BGP propagation enabled so gateway routes are
        injected into its effective route table automatically by Azure.
        """
        cols, rows = _arg_query_with_cols(token, """
            resources
            | where type =~ 'microsoft.network/virtualnetworkgateways'
            | project gwId=id, gwName=name
        """, subscription_ids)
        if not rows:
            return []

        all_routes: List[Dict] = []
        for r in rows:
            gw = dict(zip(cols, r))
            gw_id = gw.get("gwId", "")
            if not gw_id:
                continue
            try:
                url = f"{_MGMT}{gw_id}/getLearnedRoutes?api-version={_NET_VER}"
                resp = requests.post(url, headers=_headers(token), json={}, timeout=90)
                if resp.status_code == 202:
                    import time
                    async_url = resp.headers.get("Azure-AsyncOperation") or resp.headers.get("Location")
                    if not async_url:
                        continue
                    for _ in range(12):
                        time.sleep(5)
                        poll = requests.get(async_url, headers=_headers(token), timeout=15)
                        if poll.status_code == 200:
                            pd = poll.json()
                            if pd.get("status") == "Succeeded":
                                output = pd.get("properties", {}).get("output", {})
                                all_routes.extend(output.get("value", []) if isinstance(output, dict) else [])
                            break
                elif resp.status_code == 200:
                    all_routes.extend(resp.json().get("value", []))
            except Exception as e:
                logger.warning("getLearnedRoutes failed for %s: %s", gw_id, e)

        return all_routes

    def _get_effective_routes(
        self,
        route_table_id: Optional[str],
        bgp_routes: List[Dict],
        token: str,
        subscription_ids: List[str],
    ) -> List[Dict]:
        """
        Build unified route table: UDR (from Resource Graph) + BGP-learned routes.
        UDR wins over BGP at equal prefix length per Azure semantics.
        """
        routes: List[Dict] = []
        bgp_propagation_enabled = True  # Azure default

        # Static UDR routes from route table.
        if route_table_id and route_table_id.strip():
            cols, rows = _arg_query_with_cols(token, f"""
                resources
                | where type =~ 'microsoft.network/routetables'
                | where id =~ '{route_table_id}'
                | project routes=properties.routes,
                          disableBgpRoutePropagation=tobool(properties.disableBgpRoutePropagation)
            """, subscription_ids)
            if rows:
                row = dict(zip(cols, rows[0]))
                if row.get("disableBgpRoutePropagation"):
                    bgp_propagation_enabled = False
                raw_routes = row.get("routes") or []
                if isinstance(raw_routes, str):
                    try:
                        raw_routes = json.loads(raw_routes)
                    except Exception:
                        raw_routes = []
                for rt in raw_routes:
                    props = rt.get("properties", {}) if isinstance(rt, dict) else {}
                    routes.append({
                        "name": rt.get("name", ""),
                        "addressPrefix": props.get("addressPrefix", ""),
                        "nextHopType": props.get("nextHopType", ""),
                        "nextHopIpAddress": props.get("nextHopIpAddress", ""),
                        "source": "User",
                    })
                logger.info("[PW:routes] route table expanded in Python: %d routes", len(routes))

        routes.append({"addressPrefix": "0.0.0.0/0", "nextHopType": "Internet", "source": "Default"})

        # BGP-learned routes: injected when either:
        #   a) source resolved via gateway (bgp_routes passed in), or
        #   b) source is an Azure subnet with BGP propagation enabled on its route table
        # In case (b) we fetch gateway routes here so Azure VMs also get correct LPM.
        effective_bgp = list(bgp_routes)
        if bgp_propagation_enabled and not bgp_routes:
            effective_bgp = self._fetch_all_gateway_routes(token, subscription_ids)

        for bgp in effective_bgp:
            routes.append({
                "addressPrefix": bgp.get("network", ""),
                "nextHopType": "VirtualNetworkGateway",
                "nextHopIpAddress": bgp.get("nextHop", ""),
                "source": "BGP",
                "asPath": bgp.get("asPath", ""),
                "sourcePeer": bgp.get("sourcePeer", ""),
            })

        return routes

    def _evaluate_route(
        self,
        routes: List[Dict],
        dest_ip: str,
        firewall_private_ip: Optional[str],
    ) -> Dict[str, Any]:
        """
        Apply longest-prefix-match and determine if traffic reaches the firewall.
        Returns a structured finding including all candidate routes that matched.
        """
        winning_route = _longest_prefix_match(routes, dest_ip)
        if not winning_route:
            return {
                "reaches_firewall": False,
                "verdict": "no_route",
                "detail": "No matching route found. Traffic would be dropped.",
                "winning_route": None,
                "security_flag": None,
                "all_matching_routes": [],
            }

        next_hop_type = winning_route.get("nextHopType", "")
        next_hop_ip = winning_route.get("nextHopIpAddress", "")
        prefix = winning_route.get("addressPrefix", "")
        source = winning_route.get("source", "")

        reaches_fw = (
            next_hop_type == "VirtualAppliance"
            and firewall_private_ip
            and next_hop_ip == firewall_private_ip
        )

        security_flag = None
        if not reaches_fw and source == "BGP":
            has_default_udr = any(
                r.get("addressPrefix") == "0.0.0.0/0" and r.get("source") == "User"
                for r in routes
            )
            if has_default_udr:
                security_flag = (
                    f"BGP-BYPASS: BGP-learned route {prefix} (/{_net(prefix).prefixlen if _net(prefix) else '?'}) "
                    f"takes precedence over your UDR 0.0.0.0/0→Firewall due to longer prefix match. "
                    f"Traffic bypasses Azure Firewall inspection. "
                    f"Remediation: add explicit UDR {prefix} → Firewall on this subnet."
                )

        # Collect all routes that match the destination (any prefix that contains dest_ip),
        # sorted by prefix length descending so the user can see what was evaluated.
        all_matching: List[Dict] = []
        for r in routes:
            p = r.get("addressPrefix", "")
            if not p:
                continue
            if p in ("0.0.0.0/0", "::/0", "*"):
                match = True
            else:
                n = _net(p)
                try:
                    match = n is not None and ipaddress.ip_address(dest_ip.split("/")[0]) in n
                except ValueError:
                    match = False
            if match:
                all_matching.append({
                    "prefix": p,
                    "source": r.get("source", ""),
                    "nextHopType": r.get("nextHopType", ""),
                    "nextHopIp": r.get("nextHopIpAddress", "") or "",
                    "winning": r is winning_route,
                })
        # Sort by prefix length desc (most specific first)
        all_matching.sort(
            key=lambda x: _net(x["prefix"]).prefixlen if _net(x["prefix"]) else 0,
            reverse=True,
        )

        return {
            "reaches_firewall": reaches_fw,
            "next_hop_type": next_hop_type,
            "next_hop_ip": next_hop_ip,
            "winning_prefix": prefix,
            "route_source": source,
            "bgp_peer": winning_route.get("sourcePeer"),
            "security_flag": security_flag,
            "verdict": "via_firewall" if reaches_fw else "bypasses_firewall",
            "detail": (
                f"Route {prefix} (source: {source}) → {next_hop_type} {next_hop_ip or ''}".strip()
            ),
            "all_matching_routes": all_matching,
        }

    # ------------------------------------------------------------------
    # NSG evaluation
    # ------------------------------------------------------------------

    def _evaluate_nsg(
        self,
        nsg_id: Optional[str],
        source_ip: str,
        dest_ip: str,
        port: str,
        protocol: str,
        token: str,
        subscription_ids: List[str],
        direction: str = "Outbound",
    ) -> Dict[str, Any]:
        if not nsg_id or not nsg_id.strip():
            return {"has_nsg": False, "verdict": "no_nsg", "detail": "No NSG on this subnet/NIC."}

        cols, rows = _arg_query_with_cols(token, f"""
            resources
            | where type =~ 'microsoft.network/networksecuritygroups'
            | where id =~ '{nsg_id}'
            | mv-expand rule = properties.securityRules
            | where tostring(rule.properties.direction) =~ '{direction}'
            | project priority=toint(rule.properties.priority),
                      name=tostring(rule.name),
                      access=tostring(rule.properties.access),
                      protocol=tostring(rule.properties.protocol),
                      srcPrefix=tostring(rule.properties.sourceAddressPrefix),
                      dstPrefix=tostring(rule.properties.destinationAddressPrefix),
                      srcPortRange=tostring(rule.properties.sourcePortRange),
                      dstPortRange=tostring(rule.properties.destinationPortRange)
            | order by priority asc
        """, subscription_ids)

        rules = [dict(zip(cols, r)) for r in rows]
        near_misses: List[str] = []

        for rule in rules:
            src_match = self._nsg_addr_match(source_ip, rule.get("srcPrefix", "*"))
            dst_match = self._nsg_addr_match(dest_ip, rule.get("dstPrefix", "*"))
            proto_match = self._nsg_proto_match(protocol, rule.get("protocol", "*"))
            port_match = self._nsg_port_match(port, rule.get("dstPortRange", "*"))

            all_match = src_match and dst_match and proto_match and port_match

            if all_match:
                return {
                    "has_nsg": True,
                    "verdict": "allow" if rule.get("access") == "Allow" else "deny",
                    "matching_rule": rule.get("name"),
                    "priority": rule.get("priority"),
                    "detail": f"NSG rule '{rule.get('name')}' (priority {rule.get('priority')}) → {rule.get('access')}",
                    "near_misses": near_misses,
                }

            # Near miss: 3 of 4 criteria match
            score = sum([src_match, dst_match, proto_match, port_match])
            if score == 3:
                missing = []
                if not src_match: missing.append(f"src {source_ip} ∉ {rule.get('srcPrefix')}")
                if not dst_match: missing.append(f"dst {dest_ip} ∉ {rule.get('dstPrefix')}")
                if not proto_match: missing.append(f"proto {protocol} ≠ {rule.get('protocol')}")
                if not port_match: missing.append(f"port {port} ∉ {rule.get('dstPortRange')}")
                near_misses.append(
                    f"Rule '{rule.get('name')}' (p{rule.get('priority')}) almost matched — {', '.join(missing)}"
                )

        return {
            "has_nsg": True,
            "verdict": "allow",  # Azure implicit allow if no deny matched
            "detail": "No NSG deny rule matched — implicit allow.",
            "near_misses": near_misses,
        }

    def _nsg_addr_match(self, ip: str, prefix: str) -> bool:
        if prefix in ("*", "Any", "0.0.0.0/0", "Internet", "VirtualNetwork"):
            return True
        return _ip_in_cidr(ip, prefix) if "/" in prefix else ip == prefix

    def _nsg_proto_match(self, protocol: str, rule_proto: str) -> bool:
        if rule_proto in ("*", "Any"):
            return True
        return protocol.upper() == rule_proto.upper()

    def _nsg_port_match(self, port: str, rule_range: str) -> bool:
        if rule_range in ("*", "Any"):
            return True
        if str(port).strip() in ("*", "any", "Any", "0"):
            return True  # caller asking "any port" — treat as wildcard
        try:
            p = int(port)
            if "-" in rule_range:
                lo, hi = rule_range.split("-")
                return int(lo) <= p <= int(hi)
            return p == int(rule_range)
        except (ValueError, TypeError):
            return False

    # ------------------------------------------------------------------
    # Firewall rule evaluation
    # ------------------------------------------------------------------

    def _evaluate_firewall_rules(
        self,
        rules: List[Dict],
        source: str,
        dest: str,
        dest_ips: List[str],
        port: str,
        protocol: str,
    ) -> Dict[str, Any]:
        """
        Evaluate rules in correct Azure priority order:
          1. Rule Collection Group priority (ascending)
          2. Within group: Rule Collection priority (ascending)
          3. Application rules evaluated before Network rules at same tier
          4. First matching rule wins (terminate)
        """
        # Sort by group priority → collection priority → type (app before net)
        TYPE_ORDER = {"ApplicationRule": 0, "NetworkRule": 1, "NatRule": 2}
        sorted_rules = sorted(
            rules,
            key=lambda r: (
                r.get("rule_group_priority", 9999),
                r.get("rule_collection_priority", 9999),
                TYPE_ORDER.get(r.get("rule_type", ""), 9),
            ),
        )

        source_n = _net(source + ("/32" if "/" not in source else ""))
        near_misses: List[Dict] = []
        partial_matches: List[Dict] = []  # rules that cover only a subset of the source CIDR

        for rule in sorted_rules:
            if not rule.get("enabled", True):
                continue

            src_match, src_partial, src_covered = self._fw_addr_match(source, source_n, rule.get("source_addresses", []))
            dst_match = self._fw_dst_match(dest, dest_ips, rule)
            port_match = self._fw_port_match(port, rule.get("destination_ports", []))
            proto_match = self._fw_proto_match(protocol, rule.get("protocols", []))

            all_match = src_match and dst_match and port_match and proto_match

            if all_match:
                rule_ports = rule.get("destination_ports", [])
                port_is_wildcard = str(port).strip() in ("*", "any", "Any", "0")
                rule_info = {
                    "verdict": rule.get("action", "Unknown").lower(),
                    "matching_rule": rule.get("name"),
                    "rule_collection": rule.get("rule_collection"),
                    "rule_group": rule.get("rule_group"),
                    "rule_type": rule.get("rule_type"),
                    "group_priority": rule.get("rule_group_priority"),
                    "collection_priority": rule.get("rule_collection_priority"),
                    "action": rule.get("action"),
                    # When input port was *, surface the rule's actual ports so the AI
                    # can qualify its verdict correctly
                    "effective_ports": rule_ports if port_is_wildcard and rule_ports and "*" not in rule_ports else None,
                    "near_misses": near_misses,
                    "detail": (
                        f"Rule '{rule.get('name')}' in collection "
                        f"'{rule.get('rule_collection')}' (group priority {rule.get('rule_group_priority')}, "
                        f"collection priority {rule.get('rule_collection_priority')}) → {rule.get('action')}"
                    ),
                }
                if src_partial:
                    rule_info["partial_source_match"] = True
                    rule_info["covered_ranges"] = src_covered
                    rule_info["detail"] += (
                        f" [partial: only {', '.join(src_covered)} within {source}]"
                    )
                    partial_matches.append(rule_info)
                    # Do NOT terminate — keep scanning for rules that cover other parts
                    continue
                # Full source match — definitive result, include any partials found so far
                rule_info["partial_coverage_rules"] = partial_matches
                return rule_info

            # Collect near misses (3 of 4 match)
            score = sum([src_match, dst_match, port_match, proto_match])
            if score == 3:
                missing = []
                if not src_match:
                    missing.append(f"source {source} not in {rule.get('source_addresses', [])[:3]}")
                if not dst_match:
                    missing.append(f"destination {dest} not matched")
                if not port_match:
                    missing.append(f"port {port} not in {rule.get('destination_ports', [])[:3]}")
                if not proto_match:
                    missing.append(f"protocol {protocol} not in {rule.get('protocols', [])}")
                near_misses.append({
                    "rule": rule.get("name"),
                    "collection": rule.get("rule_collection"),
                    "action": rule.get("action"),
                    "reason": ", ".join(missing),
                })

        # No full-coverage rule found — return implicit deny, but surface partial matches
        return {
            "verdict": "deny",
            "matching_rule": None,
            "detail": "No matching allow rule found — implicit deny.",
            "near_misses": near_misses,
            "partial_coverage_rules": partial_matches,
        }

    def _fw_addr_match(self, source: str, source_n, addrs: List[str]) -> tuple:
        """
        Returns (matches: bool, partial: bool, covered: list[str]).
        partial=True means the rule covers only a subset of the source CIDR.
        covered lists the rule address entries that overlap.
        """
        if not addrs:
            return False, False, []

        overlapping = []
        for addr in addrs:
            if addr in ("*", "Any", "0.0.0.0/0"):
                return True, False, [addr]
            rule_n = _net(addr)
            if rule_n and source_n:
                if source_n.subnet_of(rule_n):
                    # rule fully covers source — definitive full match
                    return True, False, [addr]
                if rule_n.subnet_of(source_n):
                    # rule covers a subset of source — partial match
                    overlapping.append(addr)
                    continue
            # point-in-cidr fallback for single IPs
            if _ip_in_cidr(source.split("/")[0], addr):
                return True, False, [addr]

        if overlapping:
            return True, True, overlapping
        return False, False, []

    def _fw_dst_match(self, dest: str, dest_ips: List[str], rule: Dict) -> bool:
        # IP destinations
        dst_addrs = rule.get("destination_addresses", [])
        check_ips = [dest] + dest_ips
        for check_ip in check_ips:
            for addr in dst_addrs:
                if addr in ("*", "Any", "0.0.0.0/0"):
                    return True
                if _ip_in_cidr(check_ip.split("/")[0], addr):
                    return True

        # FQDN destinations (network + application rules)
        dst_fqdns = rule.get("destination_fqdns", []) + rule.get("destination_urls", [])
        dest_lower = dest.lower()
        for fqdn in dst_fqdns:
            f = fqdn.lower()
            if f == dest_lower:
                return True
            if f.startswith("*.") and dest_lower.endswith(f[1:]):
                return True
            # Check if dest_ips match any resolved IPs for this FQDN (best-effort)
        return False

    def _fw_port_match(self, port: str, ports: List[str]) -> bool:
        if not ports or "*" in ports:
            return True
        if str(port).strip() in ("*", "any", "Any", "0"):
            return True  # caller asking "any port" — treat as wildcard
        try:
            p = int(port)
            for pr in ports:
                if "-" in str(pr):
                    lo, hi = str(pr).split("-")
                    if int(lo) <= p <= int(hi):
                        return True
                elif int(pr) == p:
                    return True
        except (ValueError, TypeError):
            pass
        return False

    def _fw_proto_match(self, protocol: str, protocols: List[str]) -> bool:
        if not protocols:
            return True
        proto_up = protocol.upper()
        for p in protocols:
            pu = p.upper() if isinstance(p, str) else p.get("protocolType", "").upper()
            if pu in ("ANY", "*", proto_up):
                return True
            # HTTPS/HTTP map to TCP for network rules
            if proto_up in ("HTTPS", "HTTP") and pu == "TCP":
                return True
        return False

    # ------------------------------------------------------------------
    # AI reasoning
    # ------------------------------------------------------------------

    async def _ai_verdict(
        self,
        ai_service,
        source: str,
        dest: str,
        port: str,
        protocol: str,
        source_resolution: Dict,
        route_finding: Optional[Dict],
        nsg_finding: Optional[Dict],
        fw_finding: Optional[Dict],
        firewall_name: str,
        dest_is_ip: bool = False,
        source_is_cidr: bool = False,
        fw_source: Optional[str] = None,
    ) -> str:
        if not ai_service or not ai_service.is_available():
            return {}

        caveats = []
        if fw_finding and fw_finding.get("effective_ports"):
            caveats.append(
                f"Port input was a wildcard ('*'). The first matching rule only covers ports "
                f"{', '.join(str(p) for p in fw_finding['effective_ports'])}. "
                f"Traffic on other ports may be handled by different rules or denied. "
                f"Re-run with a specific port for a definitive answer."
            )
        if dest_is_ip:
            caveats.append(
                "Destination is an IP address. Azure Firewall application rules match via FQDN "
                "using its internal DNS proxy — these cannot be evaluated here. If the result is "
                "an implicit deny, an application rule for this IP's hostname may still allow it."
            )
        if source_is_cidr and fw_finding and fw_finding.get("partial_coverage_rules"):
            partials = fw_finding["partial_coverage_rules"]
            caveats.append(
                f"Source is a CIDR ({source}). The following sub-ranges within it ARE allowed "
                f"to reach {dest}:{port}/{protocol} by specific rules: "
                + "; ".join(
                    f"{', '.join(p.get('covered_ranges', []))} via rule '{p.get('matching_rule')}' "
                    f"in collection '{p.get('rule_collection')}' ({p.get('action')})"
                    for p in partials
                )
                + f". The rest of {source} is implicitly denied."
            )
        elif source_is_cidr:
            caveats.append(
                f"Source is a CIDR ({source}). No sub-range within it matched any rule for "
                f"{dest}:{port}/{protocol} — the entire range is implicitly denied."
            )

        context = {
            "query": f"{source} → {dest}:{port}/{protocol}",
            "source_resolution": source_resolution,
            "route_evaluation": route_finding,
            "nsg_evaluation": nsg_finding,
            "firewall_evaluation": fw_finding,
            "firewall_name": firewall_name,
            "caveats": caveats if caveats else None,
        }

        system = """You are an Azure network engineer explaining a packet walk result to a colleague.
Write a short analysis in clean markdown. Use this structure:

**<ALLOWED / BLOCKED / PARTIALLY ALLOWED / BYPASSES FIREWALL>** — one sentence verdict.

<2-3 sentences explaining what happened and why, naming the exact route, rule, and collection. Write naturally.>

If partial_coverage_rules are present in the firewall_evaluation, explicitly list each allowed sub-range:
For each partial rule, state: "Traffic from `<covered_range>` to `<dest>:<port>/<protocol>` is **allowed** by rule `<rule>` in `<collection>`."
Then state which parts of the source CIDR are NOT covered by any rule and are implicitly denied.

<If there is something worth flagging — a risk, a broad catch-all rule, or a bypass — add one sentence starting with > ⚠️>

Use backticks for rule names, IPs, and prefixes. Keep it concise. No headers beyond the verdict. No fluff."""

        try:
            resp = await ai_service.client.chat.completions.create(
                model=ai_service.deployment_name,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(context, default=str)},
                ],
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning("AI verdict failed: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def analyze(
        self,
        subscription_id: str,
        resource_group: str,
        policy_name: str,
        source: str,
        destination: str,
        port: str,
        protocol: str,
        subscription_ids: List[str],
        ai_service=None,
    ) -> Dict[str, Any]:
        """
        Full packet walk. Returns a structured result with per-stage findings
        and an AI-synthesised verdict.
        """
        token = await asyncio.to_thread(self._get_token)

        # Expand subscription scope to ALL subscriptions the SP can see.
        # The source NIC/VNet is almost always in a different subscription from
        # the firewall policy — using only the policy subscription causes the
        # Resource Graph NIC/subnet queries to miss the source entirely.
        try:
            all_subs = await self._fw.list_subscriptions()
            all_sub_ids = [s["subscription_id"] for s in all_subs if s.get("subscription_id")]
        except Exception as e:
            logger.warning("packet_walk: list_subscriptions failed: %s", e)
            all_sub_ids = []
        # Merge caller-supplied list with everything we discovered
        arg_scope = list({*subscription_ids, *all_sub_ids}) or subscription_ids

        # Normalise destination — if FQDN, resolve to IPs in parallel
        dest_ips: List[str] = []
        is_fqdn = not _net(destination)
        if is_fqdn:
            dest_ips = await asyncio.to_thread(_resolve_fqdn, destination)

        dest_ip_for_routing = dest_ips[0] if dest_ips else (
            destination.split("/")[0] if not is_fqdn else ""
        )

        # When destination is an IP, application rules that match via FQDN (e.g. *.amazonaws.com)
        # cannot be evaluated — Azure resolves rule FQDNs internally via its DNS proxy and we
        # have no way to reverse-map an IP to all matching FQDNs reliably. Flag this so the AI
        # can surface it in the verdict.
        dest_is_ip = not is_fqdn

        # Fetch firewall rules + resolve source simultaneously
        rules_task = asyncio.create_task(
            self._fw._flatten_rules(subscription_id, resource_group, policy_name)
        )
        source_task = asyncio.to_thread(
            self._resolve_source, source, arg_scope, token
        )

        rules, source_info = await asyncio.gather(rules_task, source_task)

        # Get firewall private IP
        fw_private_ip = await self._get_firewall_private_ip(
            subscription_id, resource_group, token
        )

        # Route evaluation (skip for fully external unknown sources)
        route_finding: Optional[Dict] = None
        if source_info.get("type") not in ("external_unknown",):
            route_table_id = source_info.get("route_table_id")
            bgp_routes = source_info.get("bgp_routes", [])
            routes = await asyncio.to_thread(
                self._get_effective_routes,
                route_table_id,
                bgp_routes,
                token,
                arg_scope,
            )
            if dest_ip_for_routing:
                route_finding = self._evaluate_route(routes, dest_ip_for_routing, fw_private_ip)

        # NSG evaluation (only if routing reaches firewall or source is in Azure subnet)
        nsg_finding: Optional[Dict] = None
        if source_info.get("nsg_id") and dest_ip_for_routing:
            nsg_finding = await asyncio.to_thread(
                self._evaluate_nsg,
                source_info.get("nsg_id"),
                source.split("/")[0],
                dest_ip_for_routing,
                port,
                protocol,
                token,
                arg_scope,
                "Outbound",
            )

        source_n_obj = _net(source)
        source_is_cidr = source_n_obj is not None and "/" in source and source_n_obj.prefixlen < 32

        # Firewall evaluation — pass raw source CIDR; _fw_addr_match handles overlap detection
        fw_finding = self._evaluate_firewall_rules(
            rules,
            source,
            destination,
            dest_ips,
            port,
            protocol,
        )

        # Overall verdict
        overall = self._overall_verdict(source_info, route_finding, nsg_finding, fw_finding)

        # AI reasoning (parallel-safe, best-effort)
        ai_summary = await self._ai_verdict(
            ai_service,
            source, destination, port, protocol,
            source_info, route_finding, nsg_finding, fw_finding,
            policy_name,
            dest_is_ip=dest_is_ip,
            source_is_cidr=source_is_cidr,
        )

        return {
            "query": {
                "source": source,
                "destination": destination,
                "port": port,
                "protocol": protocol,
            },
            "destination_ips": dest_ips,
            "firewall_private_ip": fw_private_ip,
            "overall_verdict": overall["verdict"],
            "overall_detail": overall["detail"],
            "stages": {
                "source_resolution": source_info,
                "route_evaluation": route_finding,
                "nsg_evaluation": nsg_finding,
                "firewall_evaluation": fw_finding,
            },
            "security_flags": [
                f for f in [
                    route_finding.get("security_flag") if route_finding else None,
                ] if f
            ],
            "ai_analysis": ai_summary,
        }

    def _overall_verdict(
        self,
        source: Dict,
        route: Optional[Dict],
        nsg: Optional[Dict],
        fw: Optional[Dict],
    ) -> Dict[str, str]:
        if nsg and nsg.get("verdict") == "deny":
            return {
                "verdict": "blocked_nsg",
                "detail": f"Blocked by NSG before reaching firewall: {nsg.get('detail')}",
            }
        if route and not route.get("reaches_firewall"):
            hop = route.get("next_hop_type", "unknown")
            if hop in ("None", ""):
                return {"verdict": "dropped_routing", "detail": "Route next hop is None — traffic dropped."}
            if route.get("security_flag"):
                return {"verdict": "bypasses_firewall_bgp", "detail": route["security_flag"]}
            return {"verdict": "bypasses_firewall", "detail": route.get("detail", "")}
        if fw:
            v = fw.get("verdict", "deny")
            if "allow" in v.lower():
                return {"verdict": "allowed", "detail": fw.get("detail", "")}
            # Implicit deny but some sub-ranges of the source CIDR are partially allowed
            if fw.get("partial_coverage_rules"):
                return {
                    "verdict": "partially_allowed",
                    "detail": (
                        f"{len(fw['partial_coverage_rules'])} sub-range(s) within the source are allowed "
                        f"by specific rules; the remainder is implicitly denied."
                    ),
                }
            return {"verdict": "blocked_firewall", "detail": fw.get("detail", "")}
        return {"verdict": "unknown", "detail": "Insufficient data to determine outcome."}

    async def _get_firewall_private_ip(
        self, subscription_id: str, resource_group: str, token: str
    ) -> Optional[str]:
        url = (
            f"{_MGMT}/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Network/azureFirewalls?api-version={_NET_VER}"
        )
        try:
            resp = await asyncio.to_thread(
                lambda: requests.get(url, headers=_headers(token), timeout=15)
            )
            if resp.status_code != 200:
                return None
            for fw in resp.json().get("value", []):
                for ipc in fw.get("properties", {}).get("ipConfigurations", []):
                    ip = ipc.get("properties", {}).get("privateIPAddress")
                    if ip:
                        return ip
        except Exception:
            pass
        return None
