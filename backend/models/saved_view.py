"""
Saved View Model for Cost Analysis & Dashboard

Allows users to save filter configurations as reusable views
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class SavedViewFilters(BaseModel):
    """Filter configuration stored in a view"""
    # Cost page filters
    subscriptions: List[str] = Field(default_factory=list, description="Selected subscription IDs")
    resource_groups: Optional[Dict[str, List[str]]] = Field(default=None, description="Resource groups by subscription")
    date_range: str = Field(default="this_month", description="Date range: this_month, last_month, last_7_days, last_30_days, custom")
    custom_start_date: Optional[str] = Field(default=None, description="Custom start date (ISO format)")
    custom_end_date: Optional[str] = Field(default=None, description="Custom end date (ISO format)")
    group_by: str = Field(default="ServiceName", description="Group by dimension")
    
    # Query page filters
    subscription_id: Optional[str] = Field(default=None, description="Single subscription ID for query page")
    resource_group: Optional[str] = Field(default=None, description="Resource group filter")
    resource_type: Optional[str] = Field(default=None, description="Resource type filter")
    resource: Optional[str] = Field(default=None, description="Specific resource filter")
    global_scope: Optional[bool] = Field(default=False, description="Global scope enabled")
    
    # Dashboard page filters
    locations: Optional[List[str]] = Field(default=None, description="Selected location filters")
    search_query: Optional[str] = Field(default=None, description="Search query text")
    
    # Home page watchlist card
    card_metric: Optional[str] = Field(default=None, description="Metric for home card: cost, health, security, compliance, changes")
    card_size: Optional[str] = Field(default="md", description="Card display size: sm, md, lg")


class SavedView(BaseModel):
    """Saved view model"""
    id: str = Field(..., description="Unique view ID (UUID)")
    name: str = Field(..., min_length=1, max_length=100, description="View name")
    description: Optional[str] = Field(default=None, max_length=500, description="View description")
    owner_id: str = Field(..., description="User ID who created the view")
    view_type: str = Field(default="cost", description="View type: cost, dashboard, query")
    filters: SavedViewFilters = Field(..., description="Filter configuration")
    is_default: bool = Field(default=False, description="Whether this is the user's default view")
    is_shared: bool = Field(default=False, description="Whether this view is shared with team")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Last update timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "Finance - Monthly Review",
                "description": "Finance team view with all finance-related subscriptions",
                "owner_id": "user123",
                "view_type": "cost",
                "filters": {
                    "subscriptions": ["sub1", "sub2"],
                    "resource_groups": {"sub1": ["rg1", "rg2"]},
                    "date_range": "this_month",
                    "group_by": "ServiceName"
                },
                "is_default": True,
                "is_shared": False
            }
        }


class CreateSavedViewRequest(BaseModel):
    """Request to create a new saved view"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    view_type: str = Field(default="cost")
    filters: SavedViewFilters
    is_default: bool = Field(default=False)
    is_shared: bool = Field(default=False)


class UpdateSavedViewRequest(BaseModel):
    """Request to update an existing saved view"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    filters: Optional[SavedViewFilters] = Field(default=None)
    is_default: Optional[bool] = Field(default=None)
    is_shared: Optional[bool] = Field(default=None)


class SavedViewListResponse(BaseModel):
    """Response for listing saved views"""
    success: bool
    views: List[SavedView]
    total_count: int
