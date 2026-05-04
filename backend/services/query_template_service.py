"""
Query Template Service
Manages saved query templates with in-memory + Redis storage

Storage Strategy:
- In-memory: Fast access during runtime
- Redis: 30-day TTL for persistence across restarts
- Future: Migrate to Azure Cosmos DB for long-term retention and production scale

Templates are automatically cleared from Redis after 30 days.
System templates are always available (loaded from JSON on startup).
"""
from typing import Dict, Optional, List
from datetime import datetime, timedelta
import logging
import json
import sys
from config import settings
import os
import asyncio
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.timezone_utils import now_cet
from models.query_template import (
    QueryTemplate,
    QueryTemplateCreate,
    QueryTemplateUpdate,
    TemplateSearchFilters,
    QueryType,
    TemplateCategory
)
from services.cache_service import cache_service

logger = logging.getLogger(__name__)

# Template storage TTL (30 days)
# After this period, templates are auto-cleared from Redis
# Future: Move to Cosmos DB for permanent storage
TEMPLATE_TTL_SECONDS = settings.TEMPLATE_TTL_SECONDS

class QueryTemplateService:
    """Manage query templates with dual storage (memory + Redis)"""
    
    def __init__(self):
        # In-memory storage: template_id -> QueryTemplate
        self._templates: Dict[str, QueryTemplate] = {}
        # User index: user_id -> List[template_id]
        self._user_templates: Dict[str, List[str]] = {}
        # Public templates cache
        self._public_template_ids: List[str] = []
        # Track if we've loaded from Redis
        self._redis_loaded = False
        # AI intent cache for fast suggestions (cache last 50 queries)
        self._intent_cache: Dict[str, set] = {}
        # Redis pub/sub for real-time sync across workers
        self._pubsub = None
        self._pubsub_task = None
        self._pubsub_channel = "cloudex:templates:changes"
        
        # Initialize with system templates
        self._initialize_system_templates()
    
    def _initialize_system_templates(self):
        """Load built-in system templates from JSON file"""
        try:
            # Get the path to system_templates.json
            current_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(os.path.dirname(current_dir), 'config', 'system_templates.json')
            
            # Load templates from JSON
            with open(config_path, 'r', encoding='utf-8') as f:
                templates_data = json.load(f)
            
            # Create QueryTemplate objects from JSON data
            system_templates = []
            for tmpl_data in templates_data:
                template = QueryTemplate(
                    id=tmpl_data['id'],
                    user_id="system",
                    name=tmpl_data['name'],
                    description=tmpl_data['description'],
                    query_type=tmpl_data['query_type'],
                    query=tmpl_data['query'],
                    category=tmpl_data['category'],
                    tags=tmpl_data['tags'],
                    is_public=True,
                    is_system=True,
                    created_by_name="CIRRUS System"
                )
                system_templates.append(template)
            
            logger.info(f" Loaded {len(system_templates)} system templates from {config_path}")
        except FileNotFoundError:
            logger.warning(f" System templates file not found at {config_path}, using fallback templates")
            # Fallback to a minimal set if file not found
            system_templates = [
                QueryTemplate(
                    id="system-cost-monthly",
                    user_id="system",
                    name="Monthly Cost Breakdown",
                    description="Show cost breakdown by service for the current month",
                    query_type="natural_language",
                    query="show me my costs for this month grouped by service",
                    category="cost",
                    tags=["cost", "monthly", "breakdown"],
                    is_public=True,
                    is_system=True,
                    created_by_name="CIRRUS System"
                )
            ]
        except Exception as e:
            logger.error(f" Failed to load system templates: {e}")
            system_templates = []
        
        # Store templates in memory
        for template in system_templates:
            self._templates[template.id] = template
            self._public_template_ids.append(template.id)
        
        logger.info(f" Initialized {len(system_templates)} system templates")
    
    async def create_template(
        self,
        user_id: str,
        template_data: QueryTemplateCreate,
        user_name: Optional[str] = None
    ) -> QueryTemplate:
        """Create a new query template"""
        template = QueryTemplate(
            user_id=user_id,
            name=template_data.name,
            description=template_data.description,
            query_type=template_data.query_type,
            query=template_data.query,
            parameters=template_data.parameters,
            category=template_data.category,
            tags=template_data.tags,
            is_public=template_data.is_public,  # Allow users to share templates with team
            created_by_name=user_name
        )
        
        # Store in memory
        self._templates[template.id] = template
        
        # Update user index
        if user_id not in self._user_templates:
            self._user_templates[user_id] = []
        self._user_templates[user_id].append(template.id)
        
        # Update public template index if public
        if template.is_public and not template.is_system:
            if template.id not in self._public_template_ids:
                self._public_template_ids.append(template.id)
                logger.debug(f"Added template {template.id} to public index")
        
        # Persist to Redis if available
        await self._persist_template(template)
        
        # Notify other workers via pub/sub
        await self._publish_template_change("created", template.id, user_id)
        
        logger.info(f"Created template {template.id} for user {user_id}: {template.name} (public={template.is_public})")
        return template
    
    async def get_template(self, template_id: str) -> Optional[QueryTemplate]:
        """Get a template by ID"""
        # Try memory first
        template = self._templates.get(template_id)
        if template:
            return template
        
        # Try loading from Redis
        template = await self._load_template_from_cache(template_id)
        if template:
            self._templates[template_id] = template
        
        return template
    
    async def update_template(
        self,
        template_id: str,
        user_id: str,
        update_data: QueryTemplateUpdate
    ) -> Optional[QueryTemplate]:
        """Update an existing template"""
        template = await self.get_template(template_id)
        if not template:
            return None
        
        # Check ownership (or admin)
        if template.user_id != user_id and not template.is_system:
            logger.warning(f"User {user_id} attempted to update template {template_id} owned by {template.user_id}")
            return None
        
        # System templates cannot be updated
        if template.is_system:
            logger.warning(f"Attempted to update system template {template_id}")
            return None
        
        # Apply updates
        if update_data.name is not None:
            template.name = update_data.name
        if update_data.description is not None:
            template.description = update_data.description
        if update_data.query is not None:
            template.query = update_data.query
        if update_data.parameters is not None:
            template.parameters = update_data.parameters
        if update_data.category is not None:
            template.category = update_data.category
        if update_data.tags is not None:
            template.tags = update_data.tags
        if update_data.is_public is not None:
            old_is_public = template.is_public
            template.is_public = update_data.is_public
            # Update public index
            if update_data.is_public and not old_is_public:
                # Changed from private to public
                if template.id not in self._public_template_ids:
                    self._public_template_ids.append(template.id)
                    logger.info(f"Template {template_id} is now public (added to marketplace)")
            elif not update_data.is_public and old_is_public:
                # Changed from public to private
                if template.id in self._public_template_ids:
                    self._public_template_ids.remove(template.id)
                    logger.info(f"Template {template_id} is now private (removed from marketplace)")
        
        template.updated_at = now_cet()
        
        # Persist changes
        await self._persist_template(template)
        
        # Notify other workers via pub/sub
        await self._publish_template_change("updated", template_id, user_id)
        
        logger.info(f"Updated template {template_id}")
        return template
    
    async def delete_template(self, template_id: str, user_id: str, is_admin: bool = False) -> bool:
        """Delete a template
        
        Args:
            template_id: ID of the template to delete
            user_id: ID of the user requesting deletion
            is_admin: Whether the user is an admin (can delete any public template)
        
        Returns:
            True if deleted successfully, False otherwise
        """
        template = await self.get_template(template_id)
        if not template:
            return False
        
        # System templates cannot be deleted (even by admins)
        if template.is_system:
            logger.warning(f"Attempted to delete system template {template_id}")
            return False
        
        # Check authorization:
        # - Owner can always delete their own templates
        # - Admins can delete any public shared template
        is_owner = template.user_id == user_id
        can_admin_delete = is_admin and template.is_public
        
        if not is_owner and not can_admin_delete:
            logger.warning(
                f"User {user_id} (admin={is_admin}) attempted to delete template {template_id} "
                f"owned by {template.user_id} (public={template.is_public})"
            )
            return False
        
        # Remove from memory
        del self._templates[template_id]
        
        # Remove from user index
        if user_id in self._user_templates:
            self._user_templates[user_id] = [
                tid for tid in self._user_templates[user_id] if tid != template_id
            ]
        
        # Remove from public index if needed
        if template.is_public and template_id in self._public_template_ids:
            self._public_template_ids.remove(template_id)
        
        # Remove from Redis
        await self._delete_template_from_cache(template_id)
        
        # Notify other workers via pub/sub
        await self._publish_template_change("deleted", template_id, template.user_id)
        
        logger.info(f"Deleted template {template_id}")
        return True
    
    async def get_user_templates(
        self,
        user_id: str,
        include_public: bool = True
    ) -> List[QueryTemplate]:
        """Get all templates for a user"""
        
        # Load user templates from Redis if not in memory (handles multi-worker scenario)
        if user_id not in self._user_templates or not self._user_templates[user_id]:
            await self._load_user_templates_from_redis(user_id)
        
        templates = []
        seen_ids = set()  # Prevent duplicates
        
        # User's own templates (both private and public)
        template_ids = self._user_templates.get(user_id, [])
        for tid in template_ids:
            if tid not in seen_ids:
                # Try memory first
                template = self._templates.get(tid)
                
                # If not in memory, try Redis (multi-worker sync)
                if not template:
                    template = await self._load_template_from_cache(tid)
                    if template:
                        self._templates[tid] = template
                
                if template:
                    templates.append(template)
                    seen_ids.add(tid)
        
        # Include public templates from other users
        if include_public:
            for tid in self._public_template_ids:
                if tid not in seen_ids:  # Skip if already added (user's own public template)
                    # Try memory first
                    template = self._templates.get(tid)
                    
                    # If not in memory, try Redis (multi-worker sync)
                    if not template:
                        template = await self._load_template_from_cache(tid)
                        if template:
                            self._templates[tid] = template
                    
                    if template:
                        templates.append(template)
                        seen_ids.add(tid)
        
        # Sort by last used, then by name
        templates.sort(key=lambda t: (
            t.last_used_at if t.last_used_at else datetime.min.replace(tzinfo=t.created_at.tzinfo),
            t.name
        ), reverse=True)
        
        return templates
    
    async def search_templates(
        self,
        user_id: str,
        filters: TemplateSearchFilters
    ) -> List[QueryTemplate]:
        """Search templates with filters"""
        # Start with user's templates + public templates
        templates = await self.get_user_templates(user_id, include_public=True)
        
        # Apply filters
        if filters.query:
            query_lower = filters.query.lower()
            templates = [
                t for t in templates
                if query_lower in t.name.lower() or query_lower in t.description.lower()
            ]
        
        if filters.query_type:
            templates = [t for t in templates if t.query_type == filters.query_type]
        
        if filters.category:
            templates = [t for t in templates if t.category == filters.category]
        
        if filters.tags:
            # Match templates that have ANY of the specified tags
            filter_tags = set(tag.lower() for tag in filters.tags)
            templates = [
                t for t in templates
                if any(tag in filter_tags for tag in t.tags)
            ]
        
        if filters.is_public is not None:
            templates = [t for t in templates if t.is_public == filters.is_public]
        
        return templates
    
    async def increment_template_usage(self, template_id: str):
        """Track template usage"""
        template = await self.get_template(template_id)
        if template:
            template.increment_usage()
            await self._persist_template(template)
    
    async def make_template_public(
        self,
        template_id: str,
        admin_user_id: str
    ) -> Optional[QueryTemplate]:
        """Make a template public (admin only)"""
        template = await self.get_template(template_id)
        if not template:
            return None
        
        if template.is_system:
            return template  # Already public
        
        template.is_public = True
        template.updated_at = now_cet()
        
        # Add to public index
        if template_id not in self._public_template_ids:
            self._public_template_ids.append(template_id)
        
        await self._persist_template(template)
        logger.info(f"Admin {admin_user_id} made template {template_id} public")
        
        return template
    
    # ============================================================================
    # Storage/Persistence Helpers
    # ============================================================================
    
    async def _persist_template(self, template: QueryTemplate):
        """
        Persist template to Redis cache with 30-day TTL
        
        Templates are automatically cleared after 30 days to prevent 
        unbounded growth. System templates are unaffected (loaded from JSON).
        
        Future: Migrate to Cosmos DB for permanent storage
        """
        cache_key = f"template:{template.id}"
        try:
            await cache_service.set(
                "global",  # Global namespace for templates (creates key: user:global:template:{id})
                cache_key,
                template.dict(),
                ttl=TEMPLATE_TTL_SECONDS  # 30 days auto-expiration
            )
            logger.debug(f" Persisted template {template.id} to Redis as 'user:global:{cache_key}' (TTL: {TEMPLATE_TTL_SECONDS}s)")
        except Exception as e:
            logger.error(f"Failed to persist template {template.id}: {e}")
    
    async def _load_template_from_cache(self, template_id: str) -> Optional[QueryTemplate]:
        """Load template from Redis cache"""
        cache_key = f"template:{template_id}"
        try:
            data = await cache_service.get("global", cache_key)
            if data:
                return QueryTemplate(**data)
        except Exception as e:
            logger.error(f"Failed to load template {template_id} from cache: {e}")
        return None
    
    async def _load_user_templates_from_redis(self, user_id: str):
        """
        Load user's templates from Redis (handles multi-worker scenarios)
        
        This ensures templates created in Worker A are visible in Worker B
        """
        try:
            redis_client = cache_service.redis_client
            if not redis_client:
                logger.debug("Redis not available for template sync")
                return
            
            # Scan for all template keys
            pattern = "user:global:template:*"
            cursor = 0
            user_template_ids = []
            
            while True:
                cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
                
                for key in keys:
                    # Extract template ID from key: user:global:template:XXX
                    template_id = key.split(":")[-1]
                    
                    # Load template to check ownership
                    template = await self._load_template_from_cache(template_id)
                    if template:
                        # Store in memory for this worker
                        self._templates[template_id] = template
                        
                        # Track if belongs to this user
                        if template.user_id == user_id:
                            user_template_ids.append(template_id)
                        
                        # Track if public (for marketplace)
                        if template.is_public and template_id not in self._public_template_ids:
                            self._public_template_ids.append(template_id)
                
                if cursor == 0:
                    break
            
            # Update user index
            if user_template_ids:
                self._user_templates[user_id] = user_template_ids
                logger.info(f" Synced {len(user_template_ids)} templates for user {user_id} from Redis")
                
        except Exception as e:
            logger.error(f"Failed to load user templates from Redis: {e}")
    
    async def load_all_from_redis(self):
        """
        Load all templates from Redis on startup
        This ensures templates persist across server restarts
        """
        if self._redis_loaded:
            logger.debug("Templates already loaded from Redis")
            return
        
        load_start = datetime.now()
        
        try:
            # Get Redis client
            redis_client = cache_service.redis_client
            if not redis_client:
                logger.warning("Redis not available, skipping template load")
                self._redis_loaded = True  # Mark as loaded even if Redis unavailable
                return
            
            # Scan for all template keys
            # Note: cache_service.set("global", "template:X") creates key "user:global:template:X"
            pattern = "user:global:template:*"
            cursor = 0
            loaded_count = 0
            keys_found = []
            
            logger.info(f" Scanning Redis for templates with pattern: {pattern}")
            
            # First, collect all keys using async scan
            scan_start = datetime.now()
            while True:
                cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
                keys_found.extend(keys)
                
                if cursor == 0:
                    break
            
            scan_duration = (datetime.now() - scan_start).total_seconds()
            logger.info(f" Found {len(keys_found)} template keys in Redis (scan took {scan_duration:.2f}s)")
            
            # Now load templates in batches for better performance
            load_templates_start = datetime.now()
            for key in keys_found:
                try:
                    # Extract template_id from key (user:global:template:{id})
                    template_id = key.split(':')[-1] if isinstance(key, str) else key.decode('utf-8').split(':')[-1]
                    
                    # Skip if already in memory (system templates)
                    if template_id in self._templates:
                        continue
                    
                    # Load from cache
                    template = await self._load_template_from_cache(template_id)
                    if template:
                        # Add to memory
                        self._templates[template.id] = template
                        
                        # Update user index
                        if template.user_id not in self._user_templates:
                            self._user_templates[template.user_id] = []
                        if template.id not in self._user_templates[template.user_id]:
                            self._user_templates[template.user_id].append(template.id)
                        
                        # Update public index if public
                        if template.is_public and not template.is_system:
                            if template.id not in self._public_template_ids:
                                self._public_template_ids.append(template.id)
                                logger.debug(f" Loaded PUBLIC template from Redis: {template.id} '{template.name}'")
                        else:
                            logger.debug(f" Loaded private template from Redis: {template.id} '{template.name}'")
                        
                        loaded_count += 1
                except Exception as e:
                    logger.error(f"Failed to load template from key {key}: {e}")
            
            load_templates_duration = (datetime.now() - load_templates_start).total_seconds()
            total_duration = (datetime.now() - load_start).total_seconds()
            
            self._redis_loaded = True
            logger.info(
                f"Loaded {loaded_count} user templates from Redis in {total_duration:.2f}s "
                f"(scan: {scan_duration:.2f}s, load: {load_templates_duration:.2f}s) | "
                f"Public templates: {len(self._public_template_ids)}"
            )
            
        except Exception as e:
            logger.error(f"Failed to load templates from Redis: {e}")
            self._redis_loaded = True  # Mark as complete to prevent infinite retries
    
    async def _delete_template_from_cache(self, template_id: str):
        """Delete template from Redis cache"""
        cache_key = f"template:{template_id}"
        try:
            await cache_service.delete("global", cache_key)
        except Exception as e:
            logger.error(f"Failed to delete template {template_id} from cache: {e}")
    
    # ============================================================================
    # Redis Pub/Sub for Multi-Worker Sync
    # ============================================================================
    
    async def _publish_template_change(self, action: str, template_id: str, user_id: str):
        """Publish template change event to Redis pub/sub for cross-worker sync"""
        try:
            redis_client = cache_service.redis_client
            if not redis_client:
                return
            
            message = json.dumps({
                "action": action,  # created, updated, deleted
                "template_id": template_id,
                "user_id": user_id,
                "timestamp": now_cet().isoformat()
            })
            
            await redis_client.publish(self._pubsub_channel, message)
            logger.debug(f" Published template change: {action} {template_id}")
            
        except Exception as e:
            logger.warning(f"Failed to publish template change: {e}")
    
    async def _handle_pubsub_message(self, message: dict):
        """Handle incoming pub/sub message about template changes"""
        try:
            if message.get("type") != "message":
                return
            
            data = json.loads(message.get("data", "{}"))
            action = data.get("action")
            template_id = data.get("template_id")
            user_id = data.get("user_id")
            
            if not action or not template_id:
                return
            
            logger.debug(f" Received template change: {action} {template_id}")
            
            if action == "created" or action == "updated":
                # Reload template from Redis
                template = await self._load_template_from_cache(template_id)
                if template:
                    # Update memory
                    self._templates[template_id] = template
                    
                    # Update user index
                    if user_id not in self._user_templates:
                        self._user_templates[user_id] = []
                    if template_id not in self._user_templates[user_id]:
                        self._user_templates[user_id].append(template_id)
                    
                    # Update public index
                    if template.is_public and template_id not in self._public_template_ids:
                        self._public_template_ids.append(template_id)
                    elif not template.is_public and template_id in self._public_template_ids:
                        self._public_template_ids.remove(template_id)
                    
                    logger.info(f" Synced template {action}: {template_id} ('{template.name}')")
            
            elif action == "deleted":
                # Remove from memory
                if template_id in self._templates:
                    del self._templates[template_id]
                
                # Remove from user index
                if user_id in self._user_templates:
                    self._user_templates[user_id] = [
                        tid for tid in self._user_templates[user_id] if tid != template_id
                    ]
                
                # Remove from public index
                if template_id in self._public_template_ids:
                    self._public_template_ids.remove(template_id)
                
                logger.info(f" Removed deleted template from cache: {template_id}")
                
        except Exception as e:
            logger.error(f"Error handling pub/sub message: {e}")
    
    async def _pubsub_listener(self):
        """Background task that listens for pub/sub messages"""
        try:
            redis_client = cache_service.redis_client
            if not redis_client:
                logger.warning("Redis not available, pub/sub disabled")
                return
            
            # Create pub/sub connection
            self._pubsub = redis_client.pubsub()
            await self._pubsub.subscribe(self._pubsub_channel)
            logger.info(f" Subscribed to template changes on '{self._pubsub_channel}'")
            
            # Listen for messages
            async for message in self._pubsub.listen():
                await self._handle_pubsub_message(message)
                
        except asyncio.CancelledError:
            logger.info("Pub/sub listener cancelled")
            if self._pubsub:
                await self._pubsub.unsubscribe(self._pubsub_channel)
                await self._pubsub.close()
        except Exception as e:
            logger.error(f"Pub/sub listener error: {e}")
    
    async def start_pubsub(self):
        """Start the pub/sub listener in background"""
        if self._pubsub_task is None or self._pubsub_task.done():
            self._pubsub_task = asyncio.create_task(self._pubsub_listener())
            logger.info(" Template pub/sub listener started")
    
    async def stop_pubsub(self):
        """Stop the pub/sub listener"""
        if self._pubsub_task and not self._pubsub_task.done():
            self._pubsub_task.cancel()
            try:
                await self._pubsub_task
            except asyncio.CancelledError:
                pass
            logger.info(" Template pub/sub listener stopped")
    
    # ============================================================================
    # AI-Powered Features
    # ============================================================================
    
    async def suggest_templates(self, user_query: str, user_id: str, limit: int = 3) -> List[QueryTemplate]:
        """
        Suggest relevant templates based on user's query
        Returns: AI-generated suggestions + matching existing templates
        
        Args:
            user_query: The user's natural language query
            user_id: User ID for personalization (shows their templates first)
            limit: Maximum number of suggestions to return
            
        Returns:
            List of suggested templates (AI-generated + existing), ordered by relevance
        """
        query_lower = user_query.lower().strip()
        
        if not query_lower:
            # Return most used templates if no query
            all_templates = list(self._templates.values())
            all_templates.sort(key=lambda t: t.use_count, reverse=True)
            return all_templates[:limit]
        
        # Step 1: Get AI-generated template suggestion
        ai_generated = await self._generate_template_suggestion_with_ai(query_lower, user_id)
        
        # Step 2: Find matching existing templates
        ai_variations = await self._extract_intent_with_ai_cached(query_lower)
        logger.debug(f" AI generated variations for '{query_lower}': {ai_variations}")
        
        # Score existing templates
        scored_templates = []
        
        for template in self._templates.values():
            score = 0
            
            # Semantic scoring using AI-generated variations (full phrases only)
            semantic_score = self._calculate_semantic_score(
                template, query_lower, ai_variations
            )
            score += semantic_score
            
            # Direct phrase matching from AI variations
            name_lower = template.name.lower()
            desc_lower = template.description.lower()
            query_content_lower = template.query.lower()
            
            # Check each AI-generated variation against template
            for variation in ai_variations:
                if len(variation) < 3:
                    continue
                
                # Exact phrase matches
                if variation in name_lower:
                    score += 15
                elif variation in desc_lower:
                    score += 10
                elif variation in query_content_lower:
                    score += 8
                
                # Tag matching
                for tag in template.tags:
                    if variation in tag.lower() or tag.lower() in variation:
                        score += 5
            
            # Category matching
            if template.category.lower() in query_lower:
                score += 6
            
            # Personalization boosts
            if template.user_id == user_id:
                score += 3  # User's own templates
            
            # Usage-based boost
            score += min(template.use_count * 0.2, 5)
            
            # System template slight boost (they're curated)
            if template.is_system:
                score += 2
            
            if score > 0:
                scored_templates.append((score, template))
        
        # Sort by score descending
        scored_templates.sort(key=lambda x: x[0], reverse=True)
        
        # Combine AI-generated + top existing templates
        results = []
        
        # Add AI-generated suggestion first if available
        if ai_generated:
            results.append(ai_generated)
            logger.debug(f" AI-generated template: {ai_generated.name}")
        
        # Add top existing templates (limit - 1 if AI generated, else limit)
        remaining_slots = limit - len(results)
        existing_templates = [template for _, template in scored_templates[:remaining_slots]]
        results.extend(existing_templates)
        
        # Log results
        if results:
            logger.debug(f" Total suggestions for '{user_query}': " + 
                       ", ".join([f"{t.name} ({'AI' if t.id.startswith('ai-') else 'Existing'})" 
                                 for t in results]))
        
        return results[:limit]
    
    async def _generate_template_suggestion_with_ai(self, query: str, user_id: str) -> QueryTemplate:
        """
        Use AI to generate a brand new template suggestion based on the query
        Returns a QueryTemplate with AI-generated name, description, and query
        """
        try:
            from services.kql_generation_service import AIService
            ai_service = AIService()
            
            if not ai_service.is_available():
                logger.warning("AI service not available for template generation")
                return None
            
            system_prompt = """You are an Azure cloud monitoring expert. Generate a query template based on the user's request.
Return ONLY valid JSON with this exact structure (no markdown, no code blocks):
{
  "name": "Template Name (3-6 words)",
  "description": "What this template does (1 sentence)",
  "category": "cost|security|performance|resources|logs|custom",
  "tags": ["tag1", "tag2", "tag3"],
  "query_type": "natural_language",
  "query": "Natural language query that CIRRUS Portal will execute"
}

Use "resources" for inventory/config/networking, "logs" for Log Analytics/diagnostic/activity logs, "performance" for metrics/CPU/memory.

Examples:
User: "postgresql access logs"
{"name": "PostgreSQL Access Logs", "description": "View PostgreSQL database access and authentication logs", "category": "logs", "tags": ["postgresql", "database", "access", "audit"], "query_type": "natural_language", "query": "Show me PostgreSQL access logs including authentication attempts and connections"}

User: "high cpu usage"
{"name": "High CPU Resources", "description": "Find resources with CPU usage above 80%", "category": "performance", "tags": ["cpu", "performance", "threshold"], "query_type": "natural_language", "query": "Show all resources with CPU usage above 80% in the last 24 hours"}"""

            response = await ai_service.client.chat.completions.create(
                model=ai_service.deployment_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Generate a template for: {query}"}
                ],
                max_completion_tokens=300,
                response_format={"type": "json_object"}
            )
            
            template_json = response.choices[0].message.content.strip()
            logger.debug(f" AI template response: {template_json}")
            
            if not template_json:
                logger.warning("AI template generation returned empty content")
                return None

            # Parse JSON
            import json
            template_data = json.loads(template_json)
            
            # Create QueryTemplate from AI response
            ai_template = QueryTemplate(
                id=f"ai-{hash(query) % 1000000}",  # Temporary AI-generated ID
                name=template_data["name"],
                description=template_data["description"],
                query=template_data["query"],
                query_type=template_data.get("query_type", "natural_language"),
                category=template_data["category"],
                tags=template_data["tags"],
                user_id=user_id,
                is_public=False,
                is_system=False,  # AI-generated, not system
                use_count=0,
                last_used=None
            )
            
            return ai_template
            
        except Exception as e:
            logger.error(f"Failed to generate AI template: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    async def _extract_intent_with_ai_cached(self, query: str) -> set:
        """
        Extract intent using AI with caching for speed
        First query uses AI (500ms), subsequent identical queries are instant (<1ms)
        """
        # Check cache first
        if query in self._intent_cache:
            logger.debug(f" Cache hit for query: {query}")
            return self._intent_cache[query]
        
        # Call AI for new queries
        intent_keywords = await self._extract_intent_with_ai(query)
        
        # Cache the result (keep last 50 queries)
        self._intent_cache[query] = intent_keywords
        if len(self._intent_cache) > 50:
            # Remove oldest entry
            oldest_key = next(iter(self._intent_cache))
            del self._intent_cache[oldest_key]
        
        return intent_keywords
    
    async def _extract_intent_with_ai(self, query: str) -> set:
        """
        Use AI to understand query and generate variations/synonyms
        Returns natural language variations and related terms for better matching
        """
        try:
            from services.kql_generation_service import AIService
            ai_service = AIService()
            
            if not ai_service.is_available():
                logger.warning("AI service not available, using fallback")
                return self._extract_intent_fallback(query)
            
            # Prompt to generate query variations and related terms
            system_prompt = """Generate natural language query variations and related terms for Azure cloud queries.
Return comma-separated variations that mean the same thing or are closely related.

Examples:
Input: "waf blocks"
Output: waf blocks, firewall blocks, web application firewall blocks, denied traffic, blocked requests, security blocks

Input: "postgresql access log"
Output: postgresql access log, postgres access logs, database access logs, postgresql audit logs, postgres connection logs, postgresql authentication logs

Input: "aks pod failures"  
Output: aks pod failures, kubernetes pod failures, k8s pod failures, failed pods, pod errors, container failures, pod crashes

Input: "cost breakdown"
Output: cost breakdown, cost analysis, spending breakdown, cost summary, billing breakdown, expense analysis"""

            response = await ai_service.client.chat.completions.create(
                model=ai_service.deployment_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                max_completion_tokens=150
            )
            
            variations_text = response.choices[0].message.content.strip()
            
            # Parse variations - keep as full phrases, don't split into words
            variations = set()
            for variation in variations_text.split(','):
                cleaned = variation.strip().lower()
                if cleaned and len(cleaned) >= 3:
                    variations.add(cleaned)
            
            # Always include original query as a phrase
            variations.add(query.lower())
            
            logger.debug(f" AI generated variations: {variations_text}")
            logger.debug(f" Using phrases: {variations}")
            return variations
            
        except Exception as e:
            logger.error(f"AI extraction failed: {e}, using fallback")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return self._extract_intent_fallback(query)
    
    def _extract_intent_fallback(self, query: str) -> set:
        """
        Fallback when AI is unavailable - returns empty set
        Will rely on direct keyword matching in scoring
        """
        logger.warning("AI unavailable and fallback called - suggestions may be less accurate")
        return set()
    
    def _calculate_semantic_score(self, template: QueryTemplate, 
                                  query: str, ai_variations: set) -> float:
        """
        Calculate semantic similarity score using AI-generated phrase variations
        No keyword splitting - uses full natural language phrases only
        """
        score = 0.0
        
        # Combine template text for matching
        template_text = (
            f"{template.name} {template.description} "
            f"{template.query} {' '.join(template.tags)}"
        ).lower()
        
        # Score based on AI-generated phrase matches
        for variation in ai_variations:
            if len(variation) < 3:
                continue
            if variation in template_text:
                score += 3  # AI semantic phrase match
        
        return score
    
    def get_popular_templates(self, limit: int = 10) -> List[QueryTemplate]:
        """Get most used user-created public templates (for marketplace)"""
        all_templates = list(self._templates.values())
        # Filter: public templates created by users (NOT system templates)
        public_user_templates = [t for t in all_templates if t.is_public and not t.is_system]
        public_user_templates.sort(key=lambda t: t.use_count, reverse=True)
        return public_user_templates[:limit]
    
    def get_recent_templates(self, user_id: str, limit: int = 5) -> List[QueryTemplate]:
        """Get user's recently used templates"""
        user_template_ids = self._user_templates.get(user_id, [])
        user_templates = [self._templates[tid] for tid in user_template_ids if tid in self._templates]
        
        # Filter templates that have been used
        used_templates = [t for t in user_templates if t.last_used_at is not None]
        used_templates.sort(key=lambda t: t.last_used_at or datetime.min, reverse=True)
        
        return used_templates[:limit]
    
    def get_trending_templates(self, days: int = 7, limit: int = 10) -> List[QueryTemplate]:
        """
        Get trending templates (most used in recent period)
        Note: This is a simplified version. In production, you'd track usage timestamps.
        """
        # For now, return popular public templates
        # In production, you'd track usage per day and calculate trends
        return self.get_popular_templates(limit)
    
    def rate_template(self, template_id: str, user_id: str, rating: int) -> bool:
        """
        Rate a template (1-5 stars)
        Note: Ratings are not persisted in current implementation
        
        Args:
            template_id: Template to rate
            user_id: User providing rating
            rating: Rating value (1-5)
            
        Returns:
            True if rating was recorded
        """
        if template_id not in self._templates:
            return False
        
        if rating < 1 or rating > 5:
            return False
        
        # In a production system, you'd store ratings in a separate structure
        # For now, we'll just log it
        logger.info(f"User {user_id} rated template {template_id}: {rating} stars")
        
        # Could add rating metadata to template
        template = self._templates[template_id]
        if not hasattr(template, 'ratings'):
            template.ratings = []
        
        # Store rating (in memory only for now)
        # In production: store in database with user_id, rating, timestamp
        
        return True
    
    # ============================================================================
    # Storage & Maintenance
    # ============================================================================
    
    def get_storage_info(self) -> dict:
        """Get information about template storage"""
        all_templates = list(self._templates.values())
        user_templates = [t for t in all_templates if not t.is_system]
        public_templates = [t for t in user_templates if t.is_public]
        
        return {
            "total_templates": len(all_templates),
            "system_templates": len([t for t in all_templates if t.is_system]),
            "user_templates": len(user_templates),
            "public_templates": len(public_templates),
            "private_templates": len(user_templates) - len(public_templates),
            "redis_ttl_days": TEMPLATE_TTL_SECONDS // 86400,
            "storage_note": "User templates expire after 30 days in Redis. System templates never expire.",
            "future_migration": "Long-term storage will be migrated to Azure Cosmos DB"
        }
    
    async def cleanup_expired_templates(self):
        """
        Manual cleanup of expired templates from memory
        
        Redis auto-expires after TTL, but memory cache needs manual cleanup.
        This can be called periodically or on startup.
        
        Note: This is a placeholder for future DB migration.
        With Cosmos DB, we'll use TTL at the database level.
        """
        expired_count = 0
        current_time = now_cet()
        
        for template_id, template in list(self._templates.items()):
            # Skip system templates
            if template.is_system:
                continue
            
            # Check if template is older than 30 days
            age_days = (current_time - template.created_at).days
            if age_days > 30:
                # Remove from memory
                del self._templates[template_id]
                
                # Remove from user index
                if template.user_id in self._user_templates:
                    if template_id in self._user_templates[template.user_id]:
                        self._user_templates[template.user_id].remove(template_id)
                
                expired_count += 1
                logger.info(f"Cleaned up expired template {template_id} (age: {age_days} days)")
        
        if expired_count > 0:
            logger.info(f"Cleanup completed: removed {expired_count} expired templates")
        
        return expired_count
    
    async def clear_all_redis_templates(self) -> int:
        """
        Clear all user templates from Redis (admin function)
        System templates are never cleared as they're loaded from JSON
        
        Returns:
            Number of templates cleared from Redis
        """
        if not cache_service.redis_client:
            logger.warning("Redis not available, cannot clear template cache")
            return 0
        
        cleared_count = 0
        
        try:
            # Scan for all template keys in Redis using async scan_iter
            async for key in cache_service.redis_client.scan_iter("template:*"):
                try:
                    await cache_service.redis_client.delete(key)
                    cleared_count += 1
                except Exception as e:
                    logger.error(f"Failed to delete template key {key}: {e}")
            
            # Clear user templates from memory (keep system templates)
            templates_to_remove = [
                tid for tid, t in self._templates.items() 
                if not t.is_system
            ]
            
            for template_id in templates_to_remove:
                template = self._templates[template_id]
                
                # Remove from memory
                del self._templates[template_id]
                
                # Remove from user index
                if template.user_id in self._user_templates:
                    if template_id in self._user_templates[template.user_id]:
                        self._user_templates[template.user_id].remove(template_id)
                
                # Remove from public index
                if template_id in self._public_template_ids:
                    self._public_template_ids.remove(template_id)
            
            logger.info(f" Cleared {cleared_count} templates from Redis and {len(templates_to_remove)} from memory")
            return cleared_count
            
        except Exception as e:
            logger.error(f"Failed to clear template cache: {e}")
            return 0

# Global instance
query_template_service = QueryTemplateService()
