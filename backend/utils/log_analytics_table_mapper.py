"""
Log Analytics Table Schema Mapper
Fetches and caches table schemas from Microsoft Learn documentation in Redis.
Similar to arg_table_mapper but for Log Analytics tables.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import httpx
from config import settings

logger = logging.getLogger(__name__)

# Microsoft Learn documentation URLs
MS_LEARN_BASE = "https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables"
GITHUB_API_BASE = "https://api.github.com/repos/MicrosoftDocs/azure-monitor-docs/contents/articles/azure-monitor/reference/tables"

# Redis cache settings
CACHE_KEY_PREFIX = "la_table_schema"
CACHE_METADATA_KEY = "la_table_schema:metadata"
CACHE_TTL_DAYS = settings.LA_SCHEMA_CACHE_TTL_DAYS
CACHE_TTL_SECONDS = CACHE_TTL_DAYS * 24 * 60 * 60


class LogAnalyticsTableMapper:
    """Maps Log Analytics table names to their schemas using Redis cache"""
    
    def __init__(self):
        self._last_refresh: Optional[datetime] = None
    
    async def _get_redis_client(self):
        """Get Redis client from cache service"""
        from services.cache_service import cache_service
        if cache_service and cache_service.use_redis and cache_service.redis_client:
            return cache_service.redis_client
        return None
    
    async def _load_metadata(self) -> Dict:
        """Load cache metadata from Redis"""
        # Return cached metadata if already loaded recently (within 5 minutes)
        if self._last_refresh and (datetime.now() - self._last_refresh) < timedelta(minutes=5):
            return {
                'last_refresh': self._last_refresh.isoformat(),
                'table_count': 0  # Not needed for this check
            }
        
        try:
            redis_client = await self._get_redis_client()
            if not redis_client:
                return {}
            
            metadata_str = await redis_client.get(CACHE_METADATA_KEY)
            if metadata_str:
                metadata = json.loads(metadata_str)
                if metadata.get('last_refresh'):
                    self._last_refresh = datetime.fromisoformat(metadata['last_refresh'])
                logger.debug(f" Loaded metadata: {metadata.get('table_count', 0)} tables (last refresh: {self._last_refresh})")
                return metadata
        except Exception as e:
            logger.error(f" Failed to load metadata: {e}")
        
        return {}
    
    async def _save_metadata(self, table_count: int) -> bool:
        """Save cache metadata to Redis"""
        try:
            redis_client = await self._get_redis_client()
            if not redis_client:
                return False
            
            self._last_refresh = datetime.now()
            metadata = {
                'last_refresh': self._last_refresh.isoformat(),
                'table_count': table_count,
                'ttl_days': CACHE_TTL_DAYS
            }
            
            await redis_client.set(
                CACHE_METADATA_KEY,
                json.dumps(metadata),
                ex=CACHE_TTL_SECONDS
            )
            
            logger.info(f" Saved metadata: {table_count} tables")
            return True
        except Exception as e:
            logger.error(f" Failed to save metadata: {e}")
            return False
    
    def _needs_refresh(self) -> bool:
        """Check if cache needs refresh"""
        if not self._last_refresh:
            return True
        
        age = datetime.now() - self._last_refresh
        needs_refresh = age > timedelta(days=CACHE_TTL_DAYS)
        
        if needs_refresh:
            logger.info(f" Cache is {age.days} days old, needs refresh")
        
        return needs_refresh
    
    async def _fetch_table_list(self) -> List[str]:
        """Fetch list of table markdown files from GitHub"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(GITHUB_API_BASE)
                
                if response.status_code != 200:
                    logger.error(f" GitHub API returned {response.status_code}")
                    return []
                
                files = response.json()
                table_files = [
                    f['name'] 
                    for f in files 
                    if f['type'] == 'file' and f['name'].endswith('.md')
                ]
                
                logger.info(f" Found {len(table_files)} table markdown files")
                return table_files
        except Exception as e:
            logger.error(f" Failed to fetch table list: {e}")
            return []
    
    def _parse_azurediagnostics_table(self, content: str) -> Optional[Dict]:
        """Special parser for AzureDiagnostics table (has different structure on MS Learn)"""
        try:
            columns = []
            
            # Find the section heading for AzureDiagnostics table columns
            # Pattern: <h2 id="azurediagnostics-table-columns">...</h2> followed by <table>
            heading_pattern = r'<h2[^>]*id=["\']azurediagnostics-table-columns["\'][^>]*>.*?</h2>'
            heading_match = re.search(heading_pattern, content, re.DOTALL | re.IGNORECASE)
            
            if not heading_match:
                logger.warning(" Could not find azurediagnostics-table-columns heading")
                return None
            
            # Find the first table AFTER this heading
            content_after_heading = content[heading_match.end():]
            table_match = re.search(r'<table[^>]*>(.*?)</table>', content_after_heading, re.DOTALL | re.IGNORECASE)
            
            if not table_match:
                logger.warning(" No table found after azurediagnostics-table-columns heading")
                return None
            
            logger.info(" Found AzureDiagnostics schema table after heading")
            table_content = table_match.group(1)
            
            # Extract tbody if it exists, otherwise use whole table
            tbody_match = re.search(r'<tbody>(.*?)</tbody>', table_content, re.DOTALL | re.IGNORECASE)
            if tbody_match:
                table_content = tbody_match.group(1)
            
            # Extract all rows (including potential header rows)
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_content, re.DOTALL)
            
            for row in rows:
                # Extract cell contents (both <td> and <th> tags)
                cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                
                if len(cells) >= 2:
                    # Clean HTML tags and entities
                    col_name = re.sub(r'<[^>]+>', '', cells[0]).strip()
                    col_type = re.sub(r'<[^>]+>', '', cells[1]).strip()
                    col_desc = re.sub(r'<[^>]+>', '', cells[2]).strip() if len(cells) > 2 else ''
                    
                    # Skip header rows
                    if col_name and col_name.lower() not in ['column', 'name', 'field', 'type']:
                        columns.append({
                            'name': col_name,
                            'type': col_type,
                            'description': col_desc
                        })
            
            if not columns:
                logger.warning(" No columns found for AzureDiagnostics")
                return None
            
            logger.info(f" Parsed AzureDiagnostics: {len(columns)} columns")
            
            return {
                'table_name': 'AzureDiagnostics',
                'table_name_lower': 'azurediagnostics',
                'description': 'Legacy diagnostic logs from Azure resources. Contains logs from multiple resource types.',
                'resource_types': ['*'],  # Supports all resource types
                'columns': columns,
                'column_count': len(columns),
                'is_legacy': True
            }
        except Exception as e:
            logger.error(f" Failed to parse AzureDiagnostics: {e}")
            return None
    
    def _parse_table_markdown(self, content: str, table_name: str) -> Optional[Dict]:
        """Parse HTML content from Microsoft Learn to extract schema and resource types"""
        try:
            # Special handling for AzureDiagnostics (different page structure)
            if table_name.lower() == 'azurediagnostics':
                logger.info(" Using special parser for AzureDiagnostics")
                return self._parse_azurediagnostics_table(content)
            
            columns = []
            resource_types = []
            table_description = None
            actual_table_name = table_name  # Default to filename
            
            # Extract actual table name from <h1> (case-sensitive)
            h1_match = re.search(r'<h1[^>]*[^>]*>([^<]+)</h1>', content, re.IGNORECASE)
            if h1_match:
                actual_table_name = h1_match.group(1).strip()
            
            # Extract table description (first paragraph after h1 title)
            # Look for pattern: <h1>TableName</h1> followed by <p>description</p>
            desc_match = re.search(
                r'<h1[^>]*>.*?</h1>\s*<p[^>]*>(.*?)</p>',
                content,
                re.DOTALL | re.IGNORECASE
            )
            
            if desc_match:
                # Clean HTML tags and decode entities
                table_description = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()
                # Remove extra whitespace
                table_description = re.sub(r'\s+', ' ', table_description)
            
            # Extract resource types from "Table attributes" section
            # Look for pattern: <td><strong>Resource types</strong></td> followed by <td>resource type</td>
            resource_types_match = re.search(
                r'<td><strong>Resource types</strong></td>\s*<td[^>]*>([^<]+)</td>',
                content,
                re.DOTALL | re.IGNORECASE
            )
            
            if resource_types_match:
                resource_type_text = resource_types_match.group(1).strip()
                # Multiple resource types may be comma-separated or on separate lines
                for rt in re.split(r'[,\n]+', resource_type_text):
                    rt = rt.strip()
                    if rt and '/' in rt:  # Must be a proper resource type format
                        resource_types.append(rt)
            
            # Look for the columns table in HTML
            # Pattern: <table> with headers Column, Type, Description
            # Extract rows between <tbody> tags
            
            # Find table with column headers
            table_match = re.search(r'<table[^>]*>.*?<thead>.*?<tr>.*?<th[^>]*>Column.*?</thead>.*?<tbody>(.*?)</tbody>.*?</table>', content, re.DOTALL | re.IGNORECASE)
            
            if table_match:
                tbody_content = table_match.group(1)
                
                # Extract all rows
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_content, re.DOTALL)
                
                for row in rows:
                    # Extract cell contents
                    cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                    
                    if len(cells) >= 2:
                        # Clean HTML tags
                        col_name = re.sub(r'<[^>]+>', '', cells[0]).strip()
                        col_type = re.sub(r'<[^>]+>', '', cells[1]).strip()
                        col_desc = re.sub(r'<[^>]+>', '', cells[2]).strip() if len(cells) > 2 else ''
                        
                        if col_name and col_name.lower() != 'column':
                            columns.append({
                                'name': col_name,
                                'type': col_type,
                                'description': col_desc
                            })
            
            if not columns:
                logger.debug(f" No columns found in {table_name}")
                return None
            
            schema = {
                'table_name': actual_table_name,  # Use case-sensitive name from <h1>
                'table_name_lower': table_name.lower(),  # Keep lowercase for lookups
                'columns': columns,
                'column_count': len(columns)
            }
            
            # Add table description if found
            if table_description:
                schema['description'] = table_description
            
            # Add resource types if found
            if resource_types:
                schema['resource_types'] = resource_types
                logger.debug(f" Found {len(resource_types)} resource type(s) for {table_name}")
            
            return schema
        except Exception as e:
            logger.error(f" Failed to parse {table_name}: {e}")
            return None
    
    async def _fetch_table_schema(self, filename: str) -> Optional[Dict]:
        """Fetch and parse a single table schema from Microsoft Learn"""
        table_name = filename.replace('.md', '')
        
        try:
            # Special URL for AzureDiagnostics (has anchor to specific column section)
            if table_name.lower() == 'azurediagnostics':
                url = f"{MS_LEARN_BASE}/{table_name}#azurediagnostics-table-columns"
                logger.info(f" Using special URL for AzureDiagnostics: {url}")
            else:
                # Fetch from published Microsoft Learn page (includes are resolved)
                url = f"{MS_LEARN_BASE}/{table_name}"
            
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                
                if response.status_code != 200:
                    logger.warning(f" Failed to fetch {table_name}: {response.status_code}")
                    return None
                
                html_content = response.text
                schema = self._parse_table_markdown(html_content, table_name)
                
                if schema:
                    logger.debug(f" Parsed {table_name}: {schema['column_count']} columns")
                else:
                    logger.debug(f" No columns found for {table_name}")
                
                return schema
        except Exception as e:
            logger.error(f" Failed to fetch {table_name}: {e}")
            return None
    
    async def refresh_cache(self, force: bool = False) -> bool:
        """Refresh table schemas from Microsoft Learn and store in Redis"""
        # Load metadata first
        await self._load_metadata()
        
        if not force and not self._needs_refresh():
            logger.info(" Cache is fresh, skipping refresh")
            return True
        
        logger.info(" Refreshing Log Analytics table schemas from Microsoft Learn...")
        
        # Fetch list of tables
        table_files = await self._fetch_table_list()
        if not table_files:
            logger.error(" No table files found")
            return False
        
        # Retry getting Redis client with small delay (handles startup race condition)
        redis_client = None
        for attempt in range(3):
            redis_client = await self._get_redis_client()
            if redis_client:
                break
            if attempt < 2:
                await asyncio.sleep(1)  # Wait 1 second and retry
        
        if not redis_client:
            logger.warning(" Redis not available after retries, skipping schema refresh")
            return False
        
        # Fetch and parse each table (in batches to avoid rate limits)
        saved_count = 0
        skipped_count = 0
        batch_size = settings.LA_SCHEMA_FETCH_BATCH_SIZE
        
        for i in range(0, len(table_files), batch_size):
            batch = table_files[i:i+batch_size]
            
            # Check which tables already exist in Redis
            tasks_to_fetch = []
            for filename in batch:
                table_name = filename.replace('.md', '').lower()
                redis_key = f"{CACHE_KEY_PREFIX}:{table_name}"
                exists = await redis_client.exists(redis_key)
                
                if exists:
                    skipped_count += 1
                else:
                    tasks_to_fetch.append(filename)
            
            # Only fetch tables that don't exist
            if tasks_to_fetch:
                tasks = [self._fetch_table_schema(f) for f in tasks_to_fetch]
                results = await asyncio.gather(*tasks)
                
                for schema in results:
                    if schema:
                        table_name = schema['table_name'].lower()
                        redis_key = f"{CACHE_KEY_PREFIX}:{table_name}"
                        
                        # Store in Redis with TTL
                        await redis_client.set(
                            redis_key,
                            json.dumps(schema),
                            ex=CACHE_TTL_SECONDS
                        )
                        saved_count += 1
            
            # Progress log
            logger.info(f" Processed {min(i+batch_size, len(table_files))}/{len(table_files)} tables (saved: {saved_count}, skipped: {skipped_count})")
        
        if not saved_count and not skipped_count:
            logger.error(" No tables saved successfully")
            return False
        
        # Save metadata with total count
        total_count = saved_count + skipped_count
        await self._save_metadata(total_count)
        
        logger.info(f" Refresh complete: {saved_count} fetched, {skipped_count} skipped, {total_count} total")
        
        return True
    
    async def get_table_schema(self, table_name: str) -> Optional[Dict]:
        """Get schema for a specific table from Redis"""
        try:
            redis_client = await self._get_redis_client()
            if not redis_client:
                return None
            
            # Load metadata if not loaded
            if not self._last_refresh:
                await self._load_metadata()
            
            # Check if cache needs refresh
            if self._needs_refresh():
                logger.info(" Cache expired, triggering refresh")
                await self.refresh_cache()
            
            # Lookup table (case-insensitive)
            table_name_lower = table_name.lower()
            redis_key = f"{CACHE_KEY_PREFIX}:{table_name_lower}"
            
            schema_str = await redis_client.get(redis_key)
            if schema_str:
                return json.loads(schema_str)
            
            return None
        except Exception as e:
            logger.error(f" Failed to get table schema for {table_name}: {e}")
            return None
    
    async def get_tables_for_resource_type(self, resource_type: str) -> List[str]:
        """Get all table names that support a specific resource type (returns case-sensitive names)"""
        try:
            redis_client = await self._get_redis_client()
            if not redis_client:
                return []
            
            matching_tables = []
            
            # Scan through all cached tables
            pattern = f"{CACHE_KEY_PREFIX}:*"
            async for key in redis_client.scan_iter(pattern):
                try:
                    schema_str = await redis_client.get(key)
                    if schema_str:
                        schema = json.loads(schema_str)
                        resource_types = schema.get('resource_types', [])
                        
                        # Check if resource type matches (case-insensitive)
                        # '*' means table supports all resource types (e.g., AzureDiagnostics)
                        if '*' in resource_types or any(rt.lower() == resource_type.lower() for rt in resource_types):
                            # Return the case-sensitive table name
                            matching_tables.append(schema['table_name'])
                except Exception as e:
                    logger.debug(f"Error checking key {key}: {e}")
                    continue
            
            if matching_tables:
                logger.info(f" Found {len(matching_tables)} table(s) for resource type: {resource_type}")
            else:
                logger.debug(f"No tables found for resource type: {resource_type}")
            
            return matching_tables
        except Exception as e:
            logger.error(f" Failed to get tables for resource type: {e}")
            return []
    
    async def get_all_tables(self) -> List[str]:
        """Get list of all cached table names from Redis"""
        try:
            redis_client = await self._get_redis_client()
            if not redis_client:
                return []
            
            # Scan for all table keys
            table_names = []
            pattern = f"{CACHE_KEY_PREFIX}:*"
            
            async for key in redis_client.scan_iter(pattern):
                # Extract table name from key
                key_str = key if isinstance(key, str) else key.decode('utf-8')
                if ':' in key_str:
                    table_name = key_str.split(':', 1)[1]
                    if table_name and table_name != 'metadata':
                        table_names.append(table_name)
            
            return table_names
        except Exception as e:
            logger.error(f" Failed to get all tables: {e}")
            return []


# Global instance
log_analytics_table_mapper = LogAnalyticsTableMapper()
