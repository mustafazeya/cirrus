"""
Query Template Models
Allows users to save and reuse common queries
"""
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field, validator
from datetime import datetime
import uuid
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.timezone_utils import now_cet

# Query types that templates can be created for
QueryType = Literal["natural_language", "kql", "resource_graph", "cost_analysis", "metrics"]

# Template categories for organization
TemplateCategory = Literal["cost", "security", "performance", "resources", "logs", "governance", "custom"]

class QueryTemplate(BaseModel):
    """
    A saved query template that can be reused
    
    Templates can be:
    - Private: Only visible to the user who created it
    - Public: Visible to all users (requires admin approval)
    
    Templates support parameterization for dynamic queries
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = Field(..., description="User who created the template")
    name: str = Field(..., min_length=1, max_length=100, description="Template name")
    description: str = Field(..., min_length=1, max_length=500, description="What this template does")
    
    # Query details
    query_type: QueryType = Field(..., description="Type of query (natural_language, kql, etc.)")
    query: str = Field(..., min_length=1, max_length=10000, description="The actual query text")
    
    # Parameterization support
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters that can be substituted in the query. E.g., {'time_range': '7d', 'subscription': null}"
    )
    
    # Categorization and discovery
    category: TemplateCategory = Field(default="custom", description="Template category")
    tags: List[str] = Field(default_factory=list, description="Tags for search/filtering")
    
    # Visibility and sharing
    is_public: bool = Field(default=False, description="Whether template is visible to all users")
    is_system: bool = Field(default=False, description="Whether this is a built-in system template")
    
    # Usage tracking
    use_count: int = Field(default=0, description="Number of times this template has been used")
    last_used_at: Optional[datetime] = None
    
    # Metadata
    created_at: datetime = Field(default_factory=now_cet)
    updated_at: datetime = Field(default_factory=now_cet)
    created_by_name: Optional[str] = Field(None, description="Display name of creator (for public templates)")
    
    @validator('tags')
    def validate_tags(cls, v):
        """Ensure tags are lowercase and unique"""
        return list(set(tag.lower().strip() for tag in v if tag.strip()))
    
    @validator('name')
    def validate_name(cls, v):
        """Ensure name is not just whitespace"""
        if not v.strip():
            raise ValueError("Name cannot be empty or just whitespace")
        return v.strip()
    
    def increment_usage(self):
        """Track template usage"""
        self.use_count += 1
        self.last_used_at = now_cet()
        self.updated_at = now_cet()
    
    def to_dict_for_list(self) -> Dict[str, Any]:
        """Return dict for list views (includes query for template usage)"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "query_type": self.query_type,
            "query": self.query,  # Include query so templates can be used directly
            "category": self.category,
            "tags": self.tags,
            "is_public": self.is_public,
            "is_system": self.is_system,
            "use_count": self.use_count,
            "created_at": self.created_at.isoformat(),
            "created_by_name": self.created_by_name,
            "user_id": self.user_id if self.is_public else None  # Only show user_id for public templates
        }

class QueryTemplateCreate(BaseModel):
    """Request model for creating a new template"""
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=500)
    query_type: QueryType
    query: str = Field(..., min_length=1, max_length=10000)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    category: TemplateCategory = "custom"
    tags: List[str] = Field(default_factory=list)
    is_public: bool = False  # Allow users to share templates with team via marketplace

class QueryTemplateUpdate(BaseModel):
    """Request model for updating a template"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, min_length=1, max_length=500)
    query: Optional[str] = Field(None, min_length=1, max_length=10000)
    parameters: Optional[Dict[str, Any]] = None
    category: Optional[TemplateCategory] = None
    tags: Optional[List[str]] = None
    is_public: Optional[bool] = None  # Allow changing public/private status

class QueryTemplateExecute(BaseModel):
    """Request model for executing a template with parameters"""
    template_id: str
    parameter_values: Dict[str, Any] = Field(
        default_factory=dict,
        description="Values to substitute for template parameters"
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context (subscription, resource_group, etc.)"
    )

class TemplateSearchFilters(BaseModel):
    """Filters for searching templates"""
    query: Optional[str] = Field(None, description="Search in name/description")
    query_type: Optional[QueryType] = None
    category: Optional[TemplateCategory] = None
    tags: Optional[List[str]] = None
    is_public: Optional[bool] = None
    user_id: Optional[str] = Field(None, description="Filter by creator (admin only)")
