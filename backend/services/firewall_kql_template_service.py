"""
Firewall KQL Template Service
------------------------------
All saved KQL queries are team-shared and visible to everyone with firewall access.
Any authorised user can delete any saved template.
Favourites are per-user but also stored permanently.

Redis key layout:
  fw_kql_tpl:{id}       - template JSON       (no TTL)
  fw_kql_shared_index   - JSON list of all IDs (no TTL)
  fw_kql_favs:{user_id} - JSON list of favourited IDs (no TTL)
"""
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.cache_service import cache_service

logger = logging.getLogger(__name__)

_PREFIX_TPL   = "fw_kql_tpl:"
_KEY_SHARED   = "fw_kql_shared_index"
_PREFIX_FAVS  = "fw_kql_favs:"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FirewallKqlTemplateService:
    """Permanent Redis storage for team-shared firewall KQL templates."""

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _redis(self):
        rc = cache_service.redis_client
        if not rc:
            raise RuntimeError("Redis not available")
        return rc

    async def _set_permanent(self, key: str, value: Any) -> None:
        rc = await self._redis()
        await rc.set(key, json.dumps(value, default=str))

    async def _get_json(self, key: str) -> Optional[Any]:
        rc = await self._redis()
        raw = await rc.get(key)
        return json.loads(raw) if raw else None

    async def _load_id_list(self, key: str) -> List[str]:
        data = await self._get_json(key)
        return data if isinstance(data, list) else []

    async def _save_id_list(self, key: str, ids: List[str]) -> None:
        await self._set_permanent(key, ids)

    # ── public API ────────────────────────────────────────────────────────────

    async def save(self, user_name: str, name: str, description: str, kql: str) -> Dict[str, Any]:
        """Save a KQL query shared with the whole team."""
        template_id = str(uuid.uuid4())
        template = {
            "id":          template_id,
            "user_name":   user_name,
            "name":        name,
            "description": description,
            "kql":         kql,
            "created_at":  _now(),
        }
        await self._set_permanent(f"{_PREFIX_TPL}{template_id}", template)

        shared_ids = await self._load_id_list(_KEY_SHARED)
        if template_id not in shared_ids:
            shared_ids.append(template_id)
        await self._save_id_list(_KEY_SHARED, shared_ids)

        logger.info("Saved firewall KQL template %s by %s", template_id, user_name)
        return template

    async def list_all(self) -> List[Dict[str, Any]]:
        """Return all shared templates, newest first."""
        rc = await self._redis()
        shared_ids = await self._load_id_list(_KEY_SHARED)
        results = []
        for tid in shared_ids:
            raw = await rc.get(f"{_PREFIX_TPL}{tid}")
            if raw:
                results.append(json.loads(raw))
        results.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        return results

    async def delete(self, template_id: str) -> bool:
        """Delete a template. Any authorised user can delete."""
        rc = await self._redis()
        exists = await rc.exists(f"{_PREFIX_TPL}{template_id}")
        if not exists:
            return False
        await rc.delete(f"{_PREFIX_TPL}{template_id}")
        shared_ids = await self._load_id_list(_KEY_SHARED)
        await self._save_id_list(_KEY_SHARED, [i for i in shared_ids if i != template_id])
        logger.info("Deleted firewall KQL template %s", template_id)
        return True

    async def toggle_favourite(self, user_id: str, template_id: str) -> bool:
        """Toggle a template as favourite for this user. Returns True if added."""
        key = f"{_PREFIX_FAVS}{user_id}"
        ids = await self._load_id_list(key)
        if template_id in ids:
            ids.remove(template_id)
            added = False
        else:
            ids.append(template_id)
            added = True
        await self._save_id_list(key, ids)
        logger.info("User %s %s favourite %s", user_id, "added" if added else "removed", template_id)
        return added

    async def get_favourites(self, user_id: str) -> List[str]:
        """Return list of favourite template IDs for this user."""
        return await self._load_id_list(f"{_PREFIX_FAVS}{user_id}")


firewall_kql_template_service = FirewallKqlTemplateService()
