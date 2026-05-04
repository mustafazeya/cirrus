"""
Conversation management service with Redis persistence

Redis Structure:
- conversations:{user_id}:list -> Set of conversation IDs for user
- conversations:{user_id}:{conversation_id} -> Conversation data (JSON)

Keeps only 5 most recent conversations per user.
Conversations auto-expire after 30 days.
"""
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from config import settings
import logging
import json
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.timezone_utils import now_cet
from models.conversation import (
    Conversation,
    ConversationMessage,
    MessageRole,
    MessageType,
    ConversationContext
)
from services.cache_service import cache_service

logger = logging.getLogger(__name__)

# Conversation TTL: 30 days (1 month)
CONVERSATION_TTL_SECONDS = settings.CONVERSATION_TTL_SECONDS

# Max conversations to keep per user
MAX_CONVERSATIONS_PER_USER = settings.MAX_CONVERSATIONS_PER_USER


class ConversationService:
    """Manage conversation state and history with Redis persistence"""
    
    def __init__(self):
        # In-memory cache for performance (per-worker)
        self._conversations: Dict[str, Conversation] = {}
        # User index cache
        self._user_conversations: Dict[str, List[str]] = {}
        
    def _get_conversation_key(self, user_id: str, conversation_id: str) -> str:
        """Generate Redis key for a specific conversation"""
        return f"conversations:{user_id}:{conversation_id}"
    
    def _get_list_key(self, user_id: str) -> str:
        """Generate Redis key for user's conversation list"""
        return f"conversations:{user_id}:list"
    
    async def _persist_conversation(self, conversation: Conversation):
        """Persist conversation to Redis with TTL"""
        try:
            redis_client = cache_service.redis_client
            if not redis_client:
                logger.debug("Redis not available, conversation only in memory")
                return
            
            key = self._get_conversation_key(conversation.user_id, conversation.id)
            
            # Serialize conversation to JSON
            data = conversation.model_dump(mode='json')
            await redis_client.setex(
                key,
                CONVERSATION_TTL_SECONDS,
                json.dumps(data, default=str)
            )
            
            # Add to user's conversation list (also with TTL)
            list_key = self._get_list_key(conversation.user_id)
            await redis_client.sadd(list_key, conversation.id)
            await redis_client.expire(list_key, CONVERSATION_TTL_SECONDS)
            
            # Clean up old conversations if exceeding limit
            await self._cleanup_old_user_conversations(conversation.user_id)
            
            logger.debug(f" Persisted conversation {conversation.id} to Redis (TTL: 30 days)")
        except Exception as e:
            logger.error(f"Failed to persist conversation: {e}")
    
    async def _cleanup_old_user_conversations(self, user_id: str):
        """Keep only the 5 most recent conversations per user in Redis"""
        try:
            redis_client = cache_service.redis_client
            if not redis_client:
                return
            
            list_key = self._get_list_key(user_id)
            conv_ids = await redis_client.smembers(list_key)
            
            if len(conv_ids) <= MAX_CONVERSATIONS_PER_USER:
                return  # Nothing to clean up
            
            # Load all conversations to sort by date
            conversations = []
            for cid in conv_ids:
                cid_str = cid.decode() if isinstance(cid, bytes) else cid
                conv = await self._load_conversation_from_redis(user_id, cid_str)
                if conv:
                    conversations.append(conv)
            
            # Sort by updated_at (most recent first)
            conversations.sort(key=lambda c: c.updated_at, reverse=True)
            
            # Keep only the newest 5, delete the rest
            to_delete = conversations[MAX_CONVERSATIONS_PER_USER:]
            
            for conv in to_delete:
                # Remove from Redis
                conv_key = self._get_conversation_key(user_id, conv.id)
                await redis_client.delete(conv_key)
                await redis_client.srem(list_key, conv.id)
                
                # Remove from memory
                if conv.id in self._conversations:
                    del self._conversations[conv.id]
                
                logger.debug(f" Cleaned up old conversation {conv.id} for user {user_id}")
            
            if to_delete:
                logger.info(f" Cleaned up {len(to_delete)} old conversations for user {user_id}, kept {MAX_CONVERSATIONS_PER_USER} most recent")
                
        except Exception as e:
            logger.error(f"Failed to cleanup old conversations: {e}")
    
    async def _load_conversation_from_redis(self, user_id: str, conversation_id: str) -> Optional[Conversation]:
        """Load conversation from Redis"""
        try:
            redis_client = cache_service.redis_client
            if not redis_client:
                return None
            
            key = self._get_conversation_key(user_id, conversation_id)
            data = await redis_client.get(key)
            
            if data:
                conv_dict = json.loads(data)
                conversation = Conversation(**conv_dict)
                # Cache in memory
                self._conversations[conversation.id] = conversation
                return conversation
        except Exception as e:
            logger.error(f"Failed to load conversation from Redis: {e}")
        return None
    
    async def _load_user_conversations_from_redis(self, user_id: str):
        """Load user conversation IDs from Redis"""
        try:
            redis_client = cache_service.redis_client
            if not redis_client:
                return
            
            list_key = self._get_list_key(user_id)
            conv_ids = await redis_client.smembers(list_key)
            
            if conv_ids:
                # Convert bytes to strings if needed
                conv_ids = [cid.decode() if isinstance(cid, bytes) else cid for cid in conv_ids]
                self._user_conversations[user_id] = list(conv_ids)
                logger.info(f" Loaded {len(conv_ids)} conversation IDs for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to load user conversations from Redis: {e}")
    
    async def create_conversation(self, user_id: str) -> Conversation:
        """Create a new conversation"""
        conversation = Conversation(user_id=user_id)
        
        # Store in memory
        self._conversations[conversation.id] = conversation
        
        # Update user index
        if user_id not in self._user_conversations:
            self._user_conversations[user_id] = []
        self._user_conversations[user_id].append(conversation.id)
        
        # Persist to Redis
        await self._persist_conversation(conversation)
        
        logger.info(f" Created conversation {conversation.id} for user {user_id}")
        return conversation
    
    async def get_conversation(self, conversation_id: str, user_id: Optional[str] = None) -> Optional[Conversation]:
        """Get an existing conversation"""
        # Try memory first
        conversation = self._conversations.get(conversation_id)
        
        # If not in memory and user_id provided, try Redis
        if not conversation and user_id:
            conversation = await self._load_conversation_from_redis(user_id, conversation_id)
        
        return conversation
    
    async def get_user_conversations(
        self, 
        user_id: str, 
        active_only: bool = True
    ) -> List[Conversation]:
        """Get all conversations for a user (limited to 5 most recent)"""
        
        # Load from Redis if not in memory
        if user_id not in self._user_conversations or not self._user_conversations[user_id]:
            await self._load_user_conversations_from_redis(user_id)
        
        conv_ids = self._user_conversations.get(user_id, [])
        conversations = []
        
        for cid in conv_ids:
            # Try memory first
            conv = self._conversations.get(cid)
            
            # If not in memory, try Redis
            if not conv:
                conv = await self._load_conversation_from_redis(user_id, cid)
            
            if conv:
                conversations.append(conv)
        
        if active_only:
            conversations = [c for c in conversations if c.is_active]
        
        # Sort by most recent first
        conversations.sort(key=lambda c: c.updated_at, reverse=True)
        
        # Return only the 5 most recent conversations
        return conversations[:MAX_CONVERSATIONS_PER_USER]
    
    async def add_message(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        message_type: MessageType = "user",
        user_id: Optional[str] = None,
        **kwargs
    ) -> Optional[ConversationMessage]:
        """Add a message to a conversation"""
        conversation = await self.get_conversation(conversation_id, user_id)
        if not conversation:
            logger.error(f"Conversation {conversation_id} not found")
            return None
        
        message = conversation.add_message(role, content, message_type, **kwargs)
        
        # Persist updated conversation
        await self._persist_conversation(conversation)
        
        logger.info(
            f"Added {message_type} message to conversation {conversation_id}: "
            f"{content[:50]}..."
        )
        return message
    
    async def update_context(
        self,
        conversation_id: str,
        user_id: Optional[str] = None,
        **context_updates
    ) -> bool:
        """Update conversation context"""
        conversation = await self.get_conversation(conversation_id, user_id)
        if not conversation:
            return False
        
        conversation.update_context(**context_updates)
        
        # Persist updated conversation
        await self._persist_conversation(conversation)
        
        # Log only the keys, not the full values (which can include large result sets)
        update_keys = list(context_updates.keys())
        logger.debug(f"Updated context for conversation {conversation_id}: {update_keys}")
        return True
    
    async def get_context(self, conversation_id: str, user_id: Optional[str] = None) -> Optional[ConversationContext]:
        """Get conversation context"""
        conversation = await self.get_conversation(conversation_id, user_id)
        return conversation.context if conversation else None
    
    async def get_recent_messages(
        self,
        conversation_id: str,
        count: int = 10,
        user_id: Optional[str] = None
    ) -> List[ConversationMessage]:
        """Get recent messages for context"""
        conversation = await self.get_conversation(conversation_id, user_id)
        if not conversation:
            return []
        return conversation.get_recent_messages(count)
    
    async def end_conversation(self, conversation_id: str, user_id: Optional[str] = None) -> bool:
        """Mark conversation as inactive"""
        conversation = await self.get_conversation(conversation_id, user_id)
        if not conversation:
            return False
        
        conversation.is_active = False
        
        # Persist updated state
        await self._persist_conversation(conversation)
        
        logger.info(f"Ended conversation {conversation_id}")
        return True
    
    async def cleanup_old_conversations(self, max_age_hours: int = 720):
        """
        Clean up old conversations from memory cache
        
        Note: Redis conversations auto-expire after 30 days via TTL.
        This method only cleans up the in-memory cache.
        Default max_age: 720 hours (30 days)
        """
        cutoff = now_cet() - timedelta(hours=max_age_hours)
        removed = []
        
        for conv_id, conversation in list(self._conversations.items()):
            if conversation.updated_at < cutoff and not conversation.is_active:
                # Remove from memory
                del self._conversations[conv_id]
                
                # Remove from memory user mapping
                user_id = conversation.user_id
                if user_id in self._user_conversations:
                    self._user_conversations[user_id] = [
                        cid for cid in self._user_conversations[user_id] 
                        if cid != conv_id
                    ]
                
                removed.append(conv_id)
        
        if removed:
            logger.info(f" Cleaned up {len(removed)} old conversations from memory")
        
        return len(removed)
    
    async def get_or_create_conversation(
        self,
        user_id: str,
        conversation_id: Optional[str] = None
    ) -> Conversation:
        """Get existing conversation or create new one"""
        if conversation_id:
            conversation = await self.get_conversation(conversation_id, user_id)
            if conversation and conversation.user_id == user_id:
                return conversation
            elif conversation:
                logger.warning(
                    f"Conversation {conversation_id} belongs to different user, "
                    f"creating new one"
                )
        
        # Create new conversation
        return await self.create_conversation(user_id)

# Global instance
conversation_service = ConversationService()
