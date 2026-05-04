"""
Saved View Service - Redis Implementation

Stores user filter configurations as reusable views in Redis.
Design allows easy migration to Cosmos DB later.

Redis Structure:
- saved_views:{user_id}:list -> List of view IDs for user
- saved_views:{user_id}:{view_id} -> View data (JSON)
- saved_views:{user_id}:default -> Default view ID
"""

import logging
import json
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from redis import Redis
from models.saved_view import (
    SavedView, 
    SavedViewFilters,
    CreateSavedViewRequest,
    UpdateSavedViewRequest,
    SavedViewListResponse
)

logger = logging.getLogger(__name__)


class SavedViewService:
    """Manages saved views in Redis"""
    
    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        # No TTL - views should persist indefinitely like user preferences
        # Redis persistence (RDB/AOF) will handle durability
        # When migrating to Cosmos DB, data will be permanent
        self.TTL = None
    
    def _get_view_key(self, user_id: str, view_id: str) -> str:
        """Generate Redis key for a specific view"""
        return f"saved_views:{user_id}:{view_id}"
    
    def _get_list_key(self, user_id: str) -> str:
        """Generate Redis key for user's view list"""
        return f"saved_views:{user_id}:list"
    
    def _get_default_key(self, user_id: str) -> str:
        """Generate Redis key for user's default view"""
        return f"saved_views:{user_id}:default"
    
    async def create_view(self, user_id: str, request: CreateSavedViewRequest) -> SavedView:
        """Create a new saved view"""
        try:
            view_id = str(uuid.uuid4())
            now = datetime.utcnow()
            
            view = SavedView(
                id=view_id,
                name=request.name,
                description=request.description,
                owner_id=user_id,
                view_type=request.view_type,
                filters=request.filters,
                is_default=request.is_default,
                is_shared=request.is_shared,
                created_at=now,
                updated_at=now
            )
            
            # Store view data (no expiration - persist indefinitely)
            view_key = self._get_view_key(user_id, view_id)
            await self.redis.set(
                view_key,
                json.dumps(view.model_dump(), default=str)
            )
            
            # Add to user's view list (no expiration)
            list_key = self._get_list_key(user_id)
            await self.redis.sadd(list_key, view_id)
            
            # Set as default if requested
            if request.is_default:
                await self._set_default_view(user_id, view_id)
            
            logger.info(f" Created saved view '{request.name}' for user {user_id}")
            return view
            
        except Exception as e:
            logger.error(f" Error creating saved view: {e}")
            raise
    
    async def get_view(self, user_id: str, view_id: str) -> Optional[SavedView]:
        """Get a specific saved view"""
        try:
            view_key = self._get_view_key(user_id, view_id)
            data = await self.redis.get(view_key)
            
            if not data:
                return None
            
            view_dict = json.loads(data)
            return SavedView(**view_dict)
            
        except Exception as e:
            logger.error(f" Error getting saved view {view_id}: {e}")
            return None
    
    async def list_views(self, user_id: str) -> SavedViewListResponse:
        """List all saved views for a user"""
        try:
            list_key = self._get_list_key(user_id)
            view_ids = await self.redis.smembers(list_key)
            
            views = []
            for view_id in view_ids:
                view_id_str = view_id.decode('utf-8') if isinstance(view_id, bytes) else view_id
                view = await self.get_view(user_id, view_id_str)
                if view:
                    views.append(view)
            
            # Sort by updated_at descending
            views.sort(key=lambda v: v.updated_at, reverse=True)
            
            return SavedViewListResponse(
                success=True,
                views=views,
                total_count=len(views)
            )
            
        except Exception as e:
            logger.error(f" Error listing saved views: {e}")
            return SavedViewListResponse(success=False, views=[], total_count=0)
    
    async def update_view(
        self, 
        user_id: str, 
        view_id: str, 
        request: UpdateSavedViewRequest
    ) -> Optional[SavedView]:
        """Update an existing saved view"""
        try:
            # Get existing view
            view = await self.get_view(user_id, view_id)
            if not view:
                return None
            
            # Update fields
            if request.name is not None:
                view.name = request.name
            if request.description is not None:
                view.description = request.description
            if request.filters is not None:
                view.filters = request.filters
            if request.is_default is not None:
                view.is_default = request.is_default
            if request.is_shared is not None:
                view.is_shared = request.is_shared
            
            view.updated_at = datetime.utcnow()
            
            # Save updated view (no expiration)
            view_key = self._get_view_key(user_id, view_id)
            await self.redis.set(
                view_key,
                json.dumps(view.model_dump(), default=str)
            )
            
            # Update default if needed
            if request.is_default:
                await self._set_default_view(user_id, view_id)
            
            logger.info(f" Updated saved view {view_id} for user {user_id}")
            return view
            
        except Exception as e:
            logger.error(f" Error updating saved view {view_id}: {e}")
            return None
    
    async def delete_view(self, user_id: str, view_id: str) -> bool:
        """Delete a saved view"""
        try:
            # Remove from user's list
            list_key = self._get_list_key(user_id)
            await self.redis.srem(list_key, view_id)
            
            # Delete view data
            view_key = self._get_view_key(user_id, view_id)
            await self.redis.delete(view_key)
            
            # Clear default if this was the default view
            default_key = self._get_default_key(user_id)
            default_view_id = await self.redis.get(default_key)
            if default_view_id:
                # Handle both bytes and string responses from Redis
                default_id = default_view_id.decode('utf-8') if isinstance(default_view_id, bytes) else default_view_id
                if default_id == view_id:
                    await self.redis.delete(default_key)
            
            logger.info(f" Deleted saved view {view_id} for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f" Error deleting saved view {view_id}: {e}")
            return False
    
    async def get_default_view(self, user_id: str) -> Optional[SavedView]:
        """Get user's default view"""
        try:
            default_key = self._get_default_key(user_id)
            view_id = await self.redis.get(default_key)
            
            if not view_id:
                return None
            
            view_id_str = view_id.decode('utf-8') if isinstance(view_id, bytes) else view_id
            return await self.get_view(user_id, view_id_str)
            
        except Exception as e:
            logger.error(f" Error getting default view: {e}")
            return None
    
    async def _set_default_view(self, user_id: str, view_id: str):
        """Set a view as the default (internal)"""
        # Clear is_default flag from all other views
        list_key = self._get_list_key(user_id)
        view_ids = await self.redis.smembers(list_key)
        
        for vid in view_ids:
            vid_str = vid.decode('utf-8') if isinstance(vid, bytes) else vid
            if vid_str != view_id:
                view = await self.get_view(user_id, vid_str)
                if view and view.is_default:
                    view.is_default = False
                    view_key = self._get_view_key(user_id, vid_str)
                    await self.redis.set(
                        view_key,
                        json.dumps(view.model_dump(), default=str)
                    )
        
        # Set new default (no expiration)
        default_key = self._get_default_key(user_id)
        await self.redis.set(default_key, view_id)


# Migration helper for future Cosmos DB migration
class CosmosDBMigrationHelper:
    """
    Helper functions for migrating saved views from Redis to Cosmos DB
    
    Usage when ready to migrate:
    1. Create Cosmos DB container with partition key: /owner_id
    2. Run migration script to copy all views from Redis to Cosmos DB
    3. Switch service implementation to Cosmos DB client
    4. Keep Redis as backup for rollback
    """
    
    @staticmethod
    def redis_to_cosmos_format(view: SavedView) -> Dict[str, Any]:
        """Convert view to Cosmos DB format"""
        cosmos_doc = view.model_dump()
        # Cosmos DB uses 'id' as document ID and partition key path
        cosmos_doc['_partition_key'] = view.owner_id
        return cosmos_doc
    
    @staticmethod
    def cosmos_to_redis_format(cosmos_doc: Dict[str, Any]) -> SavedView:
        """Convert Cosmos DB document back to SavedView"""
        # Remove Cosmos DB metadata
        cosmos_doc.pop('_rid', None)
        cosmos_doc.pop('_self', None)
        cosmos_doc.pop('_etag', None)
        cosmos_doc.pop('_attachments', None)
        cosmos_doc.pop('_ts', None)
        cosmos_doc.pop('_partition_key', None)
        return SavedView(**cosmos_doc)
