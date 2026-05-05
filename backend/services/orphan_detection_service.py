"""
Orphan Detection Service
Identifies wasted or abandoned Azure resources across categories.

Orphan resources (no attachment / no purpose):
  - Unattached managed disks
  - Unassociated Standard public IPs
  - Old snapshots (>N days)
  - Empty paid App Service Plans
  - Orphan NICs, empty NSGs, empty route tables

Stopped / deallocated services (still consuming storage, IPs, or plan capacity):
  - Deallocated VMs
  - Stopped AKS clusters
  - Stopped App Services
  - Stopped / disabled Logic Apps (Standard)
  - Paused Azure SQL Databases (serverless)
  - Stopped Azure SQL Managed Instances
  - Stopped PostgreSQL Flexible Servers
  - Stopped MySQL Flexible Servers
  - Stopped MariaDB Servers
  - Stopped Azure Spring Apps
  - Stopped Azure Container Instances

All queries use Azure Resource Graph for fast, subscription-wide scanning.
No cost estimates are stored in code — costs vary by region, SKU, and agreement.
"""
import logging
import asyncio
from typing import List, Dict, Any
import httpx

logger = logging.getLogger(__name__)

ARG_ENDPOINT = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"

# Created lazily per-instance so hot-reload never strands an old event loop's semaphore
_ARG_SEMAPHORE: asyncio.Semaphore | None = None
_ARG_SEMAPHORE_LIMIT = 3  # ARG allows ~15 req/5s; stay well under with room for other ARG callers


class OrphanDetectionService:

    def _get_token(self, user_info: Dict[str, Any]) -> str:
        return user_info.get("token") or user_info.get("management_token") or ""

    def _semaphore(self) -> asyncio.Semaphore:
        """Return the per-event-loop semaphore, creating it if needed."""
        global _ARG_SEMAPHORE
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        # Recreate if missing or loop has changed (e.g. after uvicorn hot-reload)
        if _ARG_SEMAPHORE is None or getattr(_ARG_SEMAPHORE, '_loop', loop) is not loop:
            _ARG_SEMAPHORE = asyncio.Semaphore(_ARG_SEMAPHORE_LIMIT)
        return _ARG_SEMAPHORE

    async def _run_arg_query_full(
        self,
        token: str,
        subscription_ids: List[str],
        query: str,
        name: str = "unknown",
        include_extended: bool = False,
    ) -> List[Dict[str, Any]]:
        """Run an ARG query with concurrency limiting and 429 retry."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        options: Dict[str, Any] = {"$top": 1000}
        if include_extended:
            options["$includeExtendedProperties"] = True
        payload = {
            "subscriptions": subscription_ids,
            "query": query,
            "options": options,
        }
        results = []
        skip_token = None

        async with self._semaphore():
            async with httpx.AsyncClient(timeout=30) as client:
                while True:
                    if skip_token:
                        payload["options"]["$skipToken"] = skip_token

                    for attempt in range(3):
                        try:
                            response = await client.post(ARG_ENDPOINT, headers=headers, json=payload)
                        except Exception as e:
                            logger.warning(f"ARG request failed: {e}")
                            return results

                        if response.status_code == 429:
                            retry_after = int(response.headers.get("Retry-After", "5"))
                            logger.warning(f"ARG rate limited — waiting {retry_after}s (attempt {attempt + 1}/3)")
                            await asyncio.sleep(retry_after)
                            continue

                        if response.status_code != 200:
                            logger.warning(f"[{name}] ARG query HTTP {response.status_code}: {response.text[:300]}")
                            return results

                        break
                    else:
                        logger.warning("ARG query failed after 3 rate-limit retries")
                        return results

                    try:
                        data = response.json()
                    except Exception as e:
                        logger.warning(f"ARG response JSON parse failed: {e}")
                        return results

                    raw = data.get("data", {})

                    # ARG can return data as a tabular dict {columns, rows} OR a list of objects
                    if isinstance(raw, list):
                        # Object-array format — each item is already a dict
                        results.extend(r if isinstance(r, dict) else {} for r in raw)
                    elif isinstance(raw, dict):
                        columns = [c["name"] for c in raw.get("columns", [])]
                        for row in raw.get("rows", []):
                            results.append(dict(zip(columns, row)))
                    # else: unexpected format, skip

                    skip_token = data.get("$skipToken")
                    if not skip_token:
                        break

        return results

    # ──────────────────────────────────────────────────────────────
    # Orphan detectors
    # ──────────────────────────────────────────────────────────────

    async def _detect_unattached_disks(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.compute/disks'
            | where properties.diskState =~ 'Unattached'
            | where sku.name !startswith 'UltraSSD'
            | project id, name, resourceGroup, subscriptionId, location,
                      diskSizeGB = toint(properties.diskSizeGB),
                      skuName    = tostring(sku.name),
                      timeCreated = tostring(properties.timeCreated)
        """)
        return [dict(r, reason=f"Unattached {r.get('skuName','disk')} ({r.get('diskSizeGB','?')} GB) — not connected to any VM") for r in rows]

    async def _detect_unassociated_public_ips(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.network/publicipaddresses'
            | where sku.name =~ 'Standard'
            | where isnull(properties.ipConfiguration)
            | where isnull(properties.natGateway)
            | project id, name, resourceGroup, subscriptionId, location,
                      skuName = tostring(sku.name),
                      allocationMethod = tostring(properties.publicIPAllocationMethod)
        """, name="unassociated_public_ips")
        return [dict(r, reason="Standard SKU public IP not associated with any resource or NAT gateway") for r in rows]

    async def _detect_old_snapshots(self, token, sub_ids, age_days):
        rows = await self._run_arg_query_full(token, sub_ids, f"""
            Resources
            | where type =~ 'microsoft.compute/snapshots'
            | extend ageInDays  = datetime_diff('day', now(), todatetime(properties.timeCreated))
            | where ageInDays > {age_days}
            | project id, name, resourceGroup, subscriptionId, location,
                      diskSizeGB  = toint(properties.diskSizeGB),
                      ageInDays,
                      timeCreated = tostring(properties.timeCreated)
        """)
        return [dict(r, reason=f"Snapshot is {r.get('ageInDays','?')} days old") for r in rows]

    async def _detect_empty_app_service_plans(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.web/serverfarms'
            | extend skuTier = tostring(sku.tier)
            | where skuTier !in ('Free', 'Shared', 'Dynamic', 'ElasticPremium', 'WorkflowStandard')
            | where toint(properties.numberOfSites) == 0
            | project id, name, resourceGroup, subscriptionId, location,
                      skuName  = tostring(sku.name),
                      skuTier,
                      capacity = toint(sku.capacity)
        """, name="empty_app_service_plans")
        return [dict(r, reason=f"App Service Plan ({r.get('skuName','?')}, {r.get('skuTier','?')}) has 0 apps") for r in rows]

    async def _detect_orphan_nics(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.network/networkinterfaces'
            | where isnull(properties.virtualMachine)
            | where isnull(properties.privateEndpoint)
            | where isnull(properties.privateLinkService)
            | where tostring(properties.nicType) !~ 'Internal'
            | extend subnetId = tolower(tostring(properties.ipConfigurations[0].properties.subnet.id))
            | join kind=leftanti (
                Resources
                | where type =~ 'microsoft.network/virtualnetworks'
                | mv-expand subnet = properties.subnets
                | where array_length(subnet.properties.delegations) > 0
                | mv-expand delegation = subnet.properties.delegations
                | where tostring(delegation.properties.serviceName) =~ 'Microsoft.Netapp/volumes'
                | project subnetId = tolower(tostring(subnet.id))
            ) on subnetId
            | project id, name, resourceGroup, subscriptionId, location
        """, name="orphan_nics")
        return [dict(r, reason="NIC not attached to any VM, private endpoint, private link service, internal service, or NetApp volume — likely a leftover from a deleted VM") for r in rows]

    async def _detect_empty_nsgs(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.network/networksecuritygroups'
            | extend nicCount    = array_length(properties.networkInterfaces)
            | extend subnetCount = array_length(properties.subnets)
            | where (isnotnull(nicCount) and nicCount == 0 or isnull(nicCount))
                and (isnotnull(subnetCount) and subnetCount == 0 or isnull(subnetCount))
            | project id, name, resourceGroup, subscriptionId, location
        """, name="empty_nsgs")
        return [dict(r, reason="NSG not associated with any subnet or NIC") for r in rows]

    async def _detect_empty_route_tables(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.network/routetables'
            | extend subnetCount = array_length(properties.subnets)
            | where isnotnull(subnetCount) and subnetCount == 0 or isnull(subnetCount)
            | project id, name, resourceGroup, subscriptionId, location
        """, name="empty_route_tables")
        return [dict(r, reason="Route table not associated with any subnet") for r in rows]

    # ──────────────────────────────────────────────────────────────
    # Stopped / deallocated service detectors
    # ──────────────────────────────────────────────────────────────

    async def _detect_deallocated_vms(self, token, sub_ids):
        # properties.extended requires $includeExtendedProperties — use a separate payload option
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.compute/virtualmachines'
            | extend powerState = tostring(properties.extended.instanceView.powerState.code)
            | where powerState =~ 'PowerState/deallocated' or powerState =~ 'PowerState/stopped'
            | project id, name, resourceGroup, subscriptionId, location,
                      vmSize     = tostring(properties.hardwareProfile.vmSize),
                      powerState
        """, name="deallocated_vms", include_extended=True)
        return [dict(r, reason=f"VM is {r.get('powerState','stopped')} — compute not billed but attached disks, public IPs, and NICs continue to incur charges") for r in rows]

    async def _detect_stopped_aks(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.containerservice/managedclusters'
            | where properties.powerState.code =~ 'Stopped'
            | project id, name, resourceGroup, subscriptionId, location,
                      kubernetesVersion = tostring(properties.kubernetesVersion),
                      nodeCount = toint(properties.agentPoolProfiles[0].count)
        """)
        return [dict(r, reason="AKS cluster is stopped — node VMs are deallocated but control plane and storage still incur charges") for r in rows]

    async def _detect_stopped_app_services(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.web/sites'
            | where properties.state =~ 'Stopped'
            | project id, name, resourceGroup, subscriptionId, location,
                      kind             = tostring(kind),
                      appServicePlan   = tostring(split(properties.serverFarmId, '/')[8])
        """)
        return [dict(r, reason="App Service is stopped — the underlying App Service Plan continues to bill") for r in rows]

    async def _detect_stopped_logic_apps(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.logic/workflows'
            | extend state = tostring(properties.state)
            | where state =~ 'Disabled' or state =~ 'Suspended'
            | project id, name, resourceGroup, subscriptionId, location,
                      state,
                      kind = tostring(kind)
        """, name="stopped_logic_apps")
        return [dict(r, reason=f"Logic App is {r.get('state','disabled')} — if on a Standard plan the underlying App Service Plan continues to bill") for r in rows]

    async def _detect_paused_sql_databases(self, token, sub_ids):
        # Serverless Azure SQL DBs can be auto-paused; still bill for storage
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.sql/servers/databases'
            | where properties.status =~ 'Paused'
            | project id, name, resourceGroup, subscriptionId, location,
                      edition         = tostring(properties.edition),
                      serviceObjective = tostring(properties.currentServiceObjectiveName),
                      maxSizeBytes    = tolong(properties.maxSizeBytes)
        """)
        return [dict(r, reason="Azure SQL Database is paused (serverless auto-pause) — storage costs continue") for r in rows]

    async def _detect_stopped_sql_managed_instances(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.sql/managedinstances'
            | where properties.state =~ 'Stopped'
            | project id, name, resourceGroup, subscriptionId, location,
                      sku = tostring(sku.name),
                      vCores = toint(properties.vCores)
        """)
        return [dict(r, reason="SQL Managed Instance is stopped — storage and backup costs continue") for r in rows]

    async def _detect_stopped_postgresql(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.dbforpostgresql/flexibleservers'
            | where properties.state =~ 'Stopped'
            | project id, name, resourceGroup, subscriptionId, location,
                      skuName = tostring(sku.name),
                      storageSizeGB = toint(properties.storage.storageSizeGB)
        """)
        return [dict(r, reason="PostgreSQL Flexible Server is stopped — storage costs continue; compute resumes after 7 days if not started") for r in rows]

    async def _detect_stopped_mysql(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.dbformysql/flexibleservers'
            | where properties.state =~ 'Stopped'
            | project id, name, resourceGroup, subscriptionId, location,
                      skuName = tostring(sku.name),
                      storageSizeGB = toint(properties.storage.storageSizeGB)
        """)
        return [dict(r, reason="MySQL Flexible Server is stopped — storage costs continue; Azure auto-restarts after 7 days") for r in rows]

    async def _detect_stopped_mariadb(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.dbformariadb/servers'
            | where properties.userVisibleState =~ 'Disabled'
            | project id, name, resourceGroup, subscriptionId, location,
                      skuName = tostring(sku.name)
        """)
        return [dict(r, reason="MariaDB server is disabled — storage costs continue") for r in rows]

    async def _detect_stopped_spring_apps(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.appplatform/spring'
            | where properties.provisioningState =~ 'Succeeded'
            | extend tier     = tostring(sku.tier)
            | extend appCount = toint(properties.appCount)
            | where tier !in~ ('', 'Basic')
            | where appCount == 0
            | project id, name, resourceGroup, subscriptionId, location, tier, appCount
        """)
        return [dict(r, reason="Azure Spring Apps service has no apps deployed — tier costs still apply") for r in rows]

    async def _detect_stopped_container_instances(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.containerinstance/containergroups'
            | extend containerState = tostring(properties.instanceView.currentState.state)
            | where containerState =~ 'Stopped' or containerState =~ 'Terminated'
            | project id, name, resourceGroup, subscriptionId, location,
                      osType        = tostring(properties.osType),
                      restartPolicy = tostring(properties.restartPolicy),
                      containerState
        """)
        return [dict(r, reason="Container Instance group is stopped/terminated — persisted data volumes may still cost") for r in rows]

    # ──────────────────────────────────────────────────────────────
    # Structural idle detectors (running but serving nothing)
    # Detected via configuration facts in ARG — no metrics needed
    # ──────────────────────────────────────────────────────────────

    async def _detect_empty_load_balancers(self, token, sub_ids):
        # Only flag LBs that have zero backend pools defined — simplest safe check via ARG
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.network/loadbalancers'
            | where sku.name !~ 'Basic'
            | where array_length(properties.backendAddressPools) == 0
            | project id, name, resourceGroup, subscriptionId, location,
                      skuName = tostring(sku.name)
        """)
        return [dict(r, reason=f"Standard Load Balancer ({r.get('skuName','?')}) has no backend pools — billed with no traffic") for r in rows]

    async def _detect_empty_app_gateways(self, token, sub_ids):
        rows = await self._run_arg_query_full(token, sub_ids, """
            Resources
            | where type =~ 'microsoft.network/applicationgateways'
            | where properties.operationalState !~ 'Stopped'
            | where array_length(properties.backendAddressPools) == 0
            | project id, name, resourceGroup, subscriptionId, location,
                      skuName = tostring(properties.sku.name),
                      tier    = tostring(properties.sku.tier)
        """)
        return [dict(r, reason=f"Application Gateway ({r.get('skuName','?')}) has no backend address pools configured") for r in rows]

    # ──────────────────────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────────────────────

    async def detect_orphans(
        self,
        subscription_ids: List[str],
        user_info: Dict[str, Any],
        snapshot_age_days: int = 30,
    ) -> Dict[str, Any]:
        """
        Run all orphan and stopped-service detectors in parallel.
        Returns structured summary with categories, counts, and item details.
        """
        token = self._get_token(user_info)
        if not token:
            return {"success": False, "error": "Authentication token required"}

        logger.info(f"Running orphan detection across {len(subscription_ids)} subscription(s)")

        _detectors = [
            # Orphan
            ("unattached_disks",              self._detect_unattached_disks(token, subscription_ids)),
            ("unassociated_public_ips",       self._detect_unassociated_public_ips(token, subscription_ids)),
            ("old_snapshots",                 self._detect_old_snapshots(token, subscription_ids, snapshot_age_days)),
            ("empty_app_service_plans",       self._detect_empty_app_service_plans(token, subscription_ids)),
            # Stopped
            ("deallocated_vms",               self._detect_deallocated_vms(token, subscription_ids)),
            ("stopped_aks",                   self._detect_stopped_aks(token, subscription_ids)),
            ("stopped_app_services",          self._detect_stopped_app_services(token, subscription_ids)),
            ("stopped_logic_apps",            self._detect_stopped_logic_apps(token, subscription_ids)),
            ("paused_sql_databases",          self._detect_paused_sql_databases(token, subscription_ids)),
            ("stopped_sql_managed_instances", self._detect_stopped_sql_managed_instances(token, subscription_ids)),
            ("stopped_postgresql",            self._detect_stopped_postgresql(token, subscription_ids)),
            ("stopped_mysql",                 self._detect_stopped_mysql(token, subscription_ids)),
            ("stopped_mariadb",               self._detect_stopped_mariadb(token, subscription_ids)),
            ("stopped_spring_apps",           self._detect_stopped_spring_apps(token, subscription_ids)),
            ("stopped_container_instances",   self._detect_stopped_container_instances(token, subscription_ids)),
            # Structural idle
            ("empty_load_balancers",          self._detect_empty_load_balancers(token, subscription_ids)),
            ("empty_app_gateways",            self._detect_empty_app_gateways(token, subscription_ids)),
            # Hygiene
            ("orphan_nics",                   self._detect_orphan_nics(token, subscription_ids)),
            ("empty_nsgs",                    self._detect_empty_nsgs(token, subscription_ids)),
            ("empty_route_tables",            self._detect_empty_route_tables(token, subscription_ids)),
        ]

        _names, _coros = zip(*_detectors)
        raw_results = await asyncio.gather(*_coros, return_exceptions=True)

        # Map results back to names; replace any exception with an empty list
        result_map: Dict[str, List[Dict]] = {}
        for name, result in zip(_names, raw_results):
            if isinstance(result, Exception):
                logger.warning(f"Detector '{name}' failed: {result}")
                result_map[name] = []
            else:
                result_map[name] = result

        def _cat(key: str, label: str, group: str) -> Dict:
            return {
                "label": label,
                "group": group,
                "count": len(result_map[key]),
                "items": result_map[key],
            }

        categories = {
            # Orphan
            "unattached_disks":              _cat("unattached_disks",              "Unattached Managed Disks",          "orphan"),
            "unassociated_public_ips":       _cat("unassociated_public_ips",       "Unassociated Standard Public IPs",  "orphan"),
            "old_snapshots":                 _cat("old_snapshots",                 f"Snapshots Older Than {snapshot_age_days} Days", "orphan"),
            "empty_app_service_plans":       _cat("empty_app_service_plans",       "Empty Paid App Service Plans",      "orphan"),
            # Stopped
            "deallocated_vms":               _cat("deallocated_vms",               "Deallocated VMs",                   "stopped"),
            "stopped_aks":                   _cat("stopped_aks",                   "Stopped AKS Clusters",              "stopped"),
            "stopped_app_services":          _cat("stopped_app_services",          "Stopped App Services",              "stopped"),
            "stopped_logic_apps":            _cat("stopped_logic_apps",            "Disabled Logic Apps",               "stopped"),
            "paused_sql_databases":          _cat("paused_sql_databases",          "Paused Azure SQL Databases",        "stopped"),
            "stopped_sql_managed_instances": _cat("stopped_sql_managed_instances", "Stopped SQL Managed Instances",    "stopped"),
            "stopped_postgresql":            _cat("stopped_postgresql",            "Stopped PostgreSQL Servers",        "stopped"),
            "stopped_mysql":                 _cat("stopped_mysql",                 "Stopped MySQL Servers",             "stopped"),
            "stopped_mariadb":               _cat("stopped_mariadb",               "Stopped MariaDB Servers",           "stopped"),
            "stopped_spring_apps":           _cat("stopped_spring_apps",           "Empty Azure Spring Apps Services",  "stopped"),
            "stopped_container_instances":   _cat("stopped_container_instances",   "Stopped Container Instances",       "stopped"),
            # Structural idle
            "empty_load_balancers":          _cat("empty_load_balancers",          "Load Balancers With No Backends",   "idle"),
            "empty_app_gateways":            _cat("empty_app_gateways",            "App Gateways With No Backends",     "idle"),
            # Hygiene
            "orphan_nics":                   _cat("orphan_nics",                   "Orphan NICs",                       "hygiene"),
            "empty_nsgs":                    _cat("empty_nsgs",                    "Empty NSGs",                        "hygiene"),
            "empty_route_tables":            _cat("empty_route_tables",            "Empty Route Tables",                "hygiene"),
        }

        total_orphans = sum(c["count"] for c in categories.values())
        by_group = {
            "orphan":  sum(c["count"] for c in categories.values() if c["group"] == "orphan"),
            "stopped": sum(c["count"] for c in categories.values() if c["group"] == "stopped"),
            "idle":    sum(c["count"] for c in categories.values() if c["group"] == "idle"),
            "hygiene": sum(c["count"] for c in categories.values() if c["group"] == "hygiene"),
        }

        logger.info(
            f"Orphan detection complete: {total_orphans} total "
            f"(orphan={by_group['orphan']}, stopped={by_group['stopped']}, "
            f"idle={by_group['idle']}, hygiene={by_group['hygiene']})"
        )

        result = {
            "success": True,
            "total_orphans": total_orphans,
            "by_group": by_group,
            "snapshot_age_days": snapshot_age_days,
            "categories": categories,
        }

        return result


orphan_detection_service = OrphanDetectionService()
