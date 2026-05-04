"""
Microsoft Graph API Service

Uses the application's Service Principal credentials to resolve Azure AD object
information (e.g., group display names) that user tokens cannot access without
elevated Graph API permissions.

Required SP permission: Group.Read.All (Application) on Microsoft Graph.
"""

import logging
from typing import Dict, List

import httpx

from services.cache_service import cache_service
from services.dual_credential_service import dual_cred_service
from config import settings

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_CACHE_TTL = settings.GRAPH_CACHE_TTL   # sourced from config.py
_CACHE_NS = "sp"                         # namespace used for SP-level cache keys
_BATCH_SIZE = settings.GRAPH_BATCH_SIZE  # MS Graph $batch limit


class GraphService:
    """
    Thin client over the Microsoft Graph API, authenticated as the service principal.
    All public methods are safe to call when the SP permission is absent — they
    will log a warning and fall back gracefully to the raw GUID.
    """

    async def resolve_group_names(self, group_ids: List[str]) -> Dict[str, str]:
        """
        Resolve Azure AD group object IDs to their display names.

        Results are cached per group ID so repeated calls within a session
        never hit the Graph API more than once per group.

        Args:
            group_ids: List of Azure AD group object ID GUIDs.

        Returns:
            Mapping of {group_id: display_name}.  For any ID that could not be
            resolved the GUID itself is returned as the display name so callers
            always receive a complete map.
        """
        if not group_ids:
            return {}

        resolved: Dict[str, str] = {}
        ids_to_fetch: List[str] = []

        for group_id in group_ids:
            cached = await cache_service.get(_CACHE_NS, f"group_name:{group_id}")
            if cached:
                resolved[group_id] = cached
            else:
                ids_to_fetch.append(group_id)

        if not ids_to_fetch:
            return resolved

        try:
            token = dual_cred_service.get_service_principal_credential().get_token(
                _GRAPH_SCOPE
            )
            headers = {
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                for batch_start in range(0, len(ids_to_fetch), _BATCH_SIZE):
                    batch_ids = ids_to_fetch[batch_start : batch_start + _BATCH_SIZE]

                    batch_requests = [
                        {
                            "id": str(i),
                            "method": "GET",
                            "url": f"/groups/{gid}?$select=id,displayName",
                        }
                        for i, gid in enumerate(batch_ids)
                    ]

                    response = await client.post(
                        f"{_GRAPH_BASE}/$batch",
                        headers=headers,
                        json={"requests": batch_requests},
                    )

                    if response.status_code != 200:
                        logger.warning(
                            "Graph $batch returned %s — falling back to GUIDs",
                            response.status_code,
                        )
                        for gid in batch_ids:
                            resolved.setdefault(gid, gid)
                        continue

                    for resp in response.json().get("responses", []):
                        idx = int(resp["id"])
                        gid = batch_ids[idx]
                        if resp.get("status") == 200:
                            display_name = resp["body"].get("displayName") or gid
                            resolved[gid] = display_name
                            await cache_service.set(
                                _CACHE_NS,
                                f"group_name:{gid}",
                                display_name,
                                ttl=_CACHE_TTL,
                            )
                        else:
                            logger.debug(
                                "Could not resolve group %s: Graph status %s",
                                gid,
                                resp.get("status"),
                            )
                            resolved[gid] = gid

        except Exception as exc:
            logger.warning(
                "Graph API group resolution failed (%s) — displaying raw GUIDs", exc
            )

        # Ensure every requested ID has an entry (even if just the GUID itself)
        for gid in ids_to_fetch:
            resolved.setdefault(gid, gid)

        return resolved


graph_service = GraphService()
