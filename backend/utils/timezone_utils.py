"""
Timezone utilities for consistent CET (Central European Time) usage across the application
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

# CET is UTC+1, CEST (summer time) is UTC+2
# For simplicity, we'll use a fixed UTC+1 offset (CET)
CET = timezone(timedelta(hours=1))

def now_cet() -> datetime:
    """Get current datetime in CET timezone"""
    return datetime.now(CET)

def utc_to_cet(dt: datetime) -> datetime:
    """Convert UTC datetime to CET"""
    if dt.tzinfo is None:
        # Assume UTC if no timezone info
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CET)

def cet_to_utc(dt: datetime) -> datetime:
    """Convert CET datetime to UTC"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CET)
    return dt.astimezone(timezone.utc)

def format_cet(dt: Optional[datetime]) -> str:
    """Format datetime as CET ISO string"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CET)
    return dt.astimezone(CET).isoformat()
