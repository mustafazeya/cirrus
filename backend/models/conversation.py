"""
Conversation state management for multi-turn AI interactions
"""
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
import uuid
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.timezone_utils import now_cet

# Message types
MessageType = Literal["user", "ai", "error", "loading"]
MessageRole = Literal["user", "assistant", "system"]

class ConversationMessage(BaseModel):
    """Single message in a conversation"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: MessageRole
    content: str
    message_type: MessageType = "user"
    timestamp: datetime = Field(default_factory=now_cet)

    @field_validator("message_type", mode="before")
    @classmethod
    def _coerce_legacy_message_type(cls, v: Any) -> Any:
        """Map legacy 'suggestion' type (stored in Redis) to 'ai' for backward compat."""
        if v == "suggestion":
            return "ai"
        return v
    
    # Optional metadata
    query_result: Optional[Dict[str, Any]] = None  # Store the actual query result
    suggestions: Optional[List[str]] = None  # Stored for history only — not rendered by frontend
    error_details: Optional[Dict[str, Any]] = None  # Error information
    actions: Optional[List[Dict[str, str]]] = None  # Quick action buttons
    metadata: Optional[Dict[str, Any]] = None  # Additional metadata (query_type, resource_type, etc.)

    # User feedback — set via POST /api/ai/feedback after a response is shown
    feedback: Optional[str] = None          # "positive" | "negative"
    correction: Optional[str] = None        # Free-text correction provided by the user

class ConversationContext(BaseModel):
    """Context maintained across conversation turns"""
    last_query_type: Optional[str] = None  # resource_graph, log_analytics, metrics, cost_analysis
    last_subscription: Optional[str] = None
    last_resource_group: Optional[str] = None
    last_time_range: Optional[str] = None
    last_result: Optional[Dict[str, Any]] = None
    referenced_resources: List[str] = Field(default_factory=list)  # Resources mentioned in conversation
    
class Conversation(BaseModel):
    """Complete conversation state"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    created_at: datetime = Field(default_factory=now_cet)
    updated_at: datetime = Field(default_factory=now_cet)
    messages: List[ConversationMessage] = Field(default_factory=list)
    context: ConversationContext = Field(default_factory=ConversationContext)
    is_active: bool = True
    
    def add_message(
        self, 
        role: MessageRole, 
        content: str, 
        message_type: MessageType = "user",
        **kwargs
    ) -> ConversationMessage:
        """Add a message to the conversation"""
        message = ConversationMessage(
            role=role,
            content=content,
            message_type=message_type,
            **kwargs
        )
        self.messages.append(message)
        self.updated_at = now_cet()
        return message
    
    def get_recent_messages(self, count: int = 10) -> List[ConversationMessage]:
        """Get recent messages for context"""
        return self.messages[-count:] if self.messages else []
    
    def update_context(self, **kwargs):
        """Update conversation context"""
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
        self.updated_at = now_cet()

class AIResponse(BaseModel):
    """Structured AI response for frontend"""
    message_id: str
    conversation_id: str
    content: str
    message_type: MessageType
    query_result: Optional[Dict[str, Any]] = None
    actions: Optional[List[Dict[str, str]]] = None
    context: Optional[Dict[str, Any]] = None
    kql_query: Optional[str] = None  # The generated KQL query for debugging
    workspace_id: Optional[str] = None  # The workspace used for the query
    rca_trigger: Optional[bool] = None  # True when query implies incident investigation
    rca_hours: Optional[int] = None  # Relative time window (hours back from now)
    rca_start: Optional[str] = None  # Absolute window start (ISO UTC)
    rca_end: Optional[str] = None    # Absolute window end (ISO UTC)
    timestamp: datetime = Field(default_factory=now_cet)
