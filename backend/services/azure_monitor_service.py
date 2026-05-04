import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_not_exception_type
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

logger = logging.getLogger(__name__)

# Custom exception for schema errors that should NOT be retried
class SchemaError(Exception):
    """Exception for KQL schema errors (column doesn't exist, etc.) that should not be retried"""
    pass

# Global workspace GUID cache (survives across service instances)
_workspace_guid_cache = {}

class AzureMonitorService:
    """Service for executing KQL queries against Azure Monitor Log Analytics using Azure SDK"""
    
    def __init__(self):
        pass
    
    async def _resolve_workspace_to_guid(self, workspace_identifier: str, management_token: str) -> str:
        """
        Resolve workspace resource ID to workspace GUID (customerId)
        Uses GLOBAL cache shared across all service instances
        
        Args:
            workspace_identifier: Either workspace GUID or full resource ID
            management_token: Token with https://management.azure.com audience
            
        Returns:
            Workspace GUID (customerId) required by LogsQueryClient
        """
        global _workspace_guid_cache
        
        # Check global cache first
        if workspace_identifier in _workspace_guid_cache:
            logger.info(f" Using cached workspace GUID")
            return _workspace_guid_cache[workspace_identifier]
        
        # If it's already a GUID (36 chars with 4 hyphens), cache and return
        if len(workspace_identifier) == 36 and workspace_identifier.count('-') == 4:
            logger.info(f"Workspace is already a GUID: {workspace_identifier}")
            _workspace_guid_cache[workspace_identifier] = workspace_identifier
            return workspace_identifier
        
        # If it's a resource ID, resolve it to workspace GUID using Management API
        if workspace_identifier.startswith('/subscriptions/'):
            try:
                import requests
                import asyncio
                
                logger.info(f"Resolving workspace resource ID to GUID: {workspace_identifier}")
                
                api_url = f"https://management.azure.com{workspace_identifier}?api-version=2021-06-01"
                headers = {
                    "Authorization": f"Bearer {management_token}",
                    "Content-Type": "application/json"
                }
                
                def get_workspace():
                    response = requests.get(api_url, headers=headers, timeout=30)
                    response.raise_for_status()
                    return response.json()
                
                workspace_data = await asyncio.get_event_loop().run_in_executor(None, get_workspace)
                workspace_guid = workspace_data.get('properties', {}).get('customerId')
                
                if not workspace_guid:
                    raise Exception(f"No customerId found in workspace response")
                
                logger.info(f" Resolved workspace to GUID: {workspace_guid}")
                _workspace_guid_cache[workspace_identifier] = workspace_guid
                return workspace_guid
                
            except Exception as e:
                logger.error("Failed to resolve workspace resource ID")
                raise Exception("Could not resolve workspace to GUID")
        
        # If it's neither, assume it's already a workspace identifier
        logger.info(f"Using workspace identifier as-is: {workspace_identifier}")
        _workspace_guid_cache[workspace_identifier] = workspace_identifier
        return workspace_identifier

    @retry(
        stop=stop_after_attempt(3), 
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_not_exception_type((SchemaError, ResourceNotFoundError))  # Don't retry schema or path errors
    )
    async def execute_kql_query(self, query: str, workspace_id: str, user_info: Dict[str, Any], 
                               timespan: Optional[timedelta] = timedelta(days=30)) -> Dict[str, Any]:
        """
        Execute a KQL query against a Log Analytics workspace using Azure Monitor Query SDK
        
        Args:
            query: KQL query string
            workspace_id: Log Analytics workspace ID (can be GUID or full resource ID)
                         If resource ID, will be resolved to GUID using management token
            user_info: User authentication information - must contain:
                      - 'token': Log Analytics token (audience: https://api.loganalytics.io)
                      - Original management token still in user_data from parent call
            timespan: Query time range (defaults to last 30 days)
            
        Returns:
            Dictionary containing query results and metadata
        """
        try:
            from azure.monitor.query import LogsQueryClient, LogsQueryStatus
            from datetime import datetime
            import asyncio
            
            logger.info(f"Executing KQL query against workspace: {workspace_id}")
            logger.info(f"Query: {query}")
            
            # Get LA token for KQL execution
            la_token = user_info.get('la_token') or user_info.get('token')
            if not la_token:
                raise Exception("No Log Analytics token provided")
            
            # Verify token has correct audience for Log Analytics
            import jwt
            try:
                decoded = jwt.decode(la_token, options={"verify_signature": False})
                token_audience = decoded.get('aud', '')
                
                # Check if this is a Log Analytics token
                if 'loganalytics.io' not in token_audience and token_audience != 'https://api.loganalytics.io':
                    logger.error(f"Token has wrong audience: {token_audience} (expected: https://api.loganalytics.io)")
                    raise Exception("Authentication token expired. Please logout and login again to refresh your session.")
            except jwt.DecodeError:
                logger.error("Failed to decode token")
                raise Exception("Invalid authentication token. Please logout and login again.")
            
            # Resolve workspace resource ID to GUID if needed
            # The SDK URL-encodes resource paths causing 404, so we need the GUID
            # Use management token for workspace resolution via Management API
            workspace_guid = workspace_id
            if workspace_id.startswith('/subscriptions/'):
                management_token = user_info.get('management_token') or user_info.get('token')
                if not management_token:
                    raise Exception("No management token available for workspace resolution")
                
                logger.info(f"Resolving workspace resource ID to GUID using management token")
                workspace_guid = await self._resolve_workspace_to_guid(workspace_id, management_token)
                logger.info(f"Using resolved workspace GUID: {workspace_guid}")
            
            # Import UserTokenCredential from auth
            from auth import UserTokenCredential
            user_credential = UserTokenCredential(la_token)
            
            # Create the logs query client with LA token
            client = LogsQueryClient(user_credential)
            
            # Calculate time range (None means let KQL's own time filter control the window)
            if timespan is not None:
                end_time = datetime.utcnow()
                start_time = end_time - timespan
                logger.info(f"Query timespan: {timespan}, from {start_time} to {end_time}")
                query_timespan = (start_time, end_time)
            else:
                logger.info("Query timespan: unset — KQL time filter controls the window")
                query_timespan = None

            # Run synchronous query in thread pool
            # Use the resolved workspace GUID (not the original resource ID)
            def run_query():
                return client.query_workspace(
                    workspace_id=workspace_guid,
                    query=query,
                    timespan=query_timespan
                )
            
            response = await asyncio.get_event_loop().run_in_executor(None, run_query)
            
            logger.info(f"Query response status: {response.status}")
            
            if response.status == LogsQueryStatus.SUCCESS:
                # Convert results to list of dictionaries
                results = []
                tables = []
                
                if response.tables:
                    for table in response.tables:
                        # Debug: Check column structure
                        logger.info(f"Table has {len(table.columns)} columns")
                        if len(table.columns) > 0:
                            first_col = table.columns[0]
                            logger.info(f"First column type: {type(first_col)}, value: {first_col}")
                        
                        # Convert table rows to list of dictionaries (keep ALL columns for AI analysis)
                        table_results = []
                        for row in table.rows:
                            row_dict = {}
                            for i, column in enumerate(table.columns):
                                # Handle both column object and string formats
                                col_name = column.name if hasattr(column, 'name') else str(column)
                                row_dict[col_name] = row[i]
                            table_results.append(row_dict)
                        
                        # Store full results for AI analysis (log_analysis_service will filter empty columns)
                        results.extend(table_results)
                        
                        # Also store table metadata
                        # Handle columns that might be strings or objects
                        column_metadata = []
                        for col in table.columns:
                            if hasattr(col, 'name'):
                                column_metadata.append({'name': col.name, 'type': col.type if hasattr(col, 'type') else 'string'})
                            else:
                                column_metadata.append({'name': str(col), 'type': 'string'})
                        
                        # Filter empty columns before adding to tables
                        raw_rows = [[row[i] for i in range(len(table.columns))] for row in table.rows]
                        filtered_columns, filtered_rows = self._filter_empty_columns(column_metadata, raw_rows)
                        
                        tables.append({
                            'name': table.name if hasattr(table, 'name') else 'PrimaryResult',
                            'columns': filtered_columns,
                            'rows': filtered_rows
                        })
                
                logger.info(f"Query returned {len(results)} rows across {len(tables)} table(s)")
                
                return {
                    'status': 'success',
                    'tables': tables,
                    'results': results,  # Flat list for backward compatibility
                    'row_count': len(results)
                }
            elif response.status == LogsQueryStatus.PARTIAL:
                logger.warning("Query completed with partial success")
                # LogsQueryPartialResult has different structure than LogsQueryResult
                results = []
                tables = []
                
                try:
                    # Try to access partial_data (Azure SDK structure)
                    partial_tables = []
                    if hasattr(response, 'partial_data') and response.partial_data:
                        partial_tables = response.partial_data
                    elif hasattr(response, 'tables') and response.tables:
                        partial_tables = response.tables
                    
                    logger.info(f"Processing {len(partial_tables)} partial tables")
                    
                    for table in partial_tables:
                        logger.info(f"Table type: {type(table)}, has columns: {hasattr(table, 'columns')}")
                        
                        # Build column metadata - handle both object and dict structures
                        column_metadata = []
                        for column in table.columns:
                            if hasattr(column, 'name'):
                                # Column is an object
                                column_metadata.append({
                                    'name': column.name,
                                    'type': column.type if hasattr(column, 'type') else 'string'
                                })
                            elif isinstance(column, dict):
                                # Column is a dict
                                column_metadata.append({
                                    'name': column.get('name', 'unknown'),
                                    'type': column.get('type', 'string')
                                })
                            else:
                                # Column is something else (usually just a string name)
                                # This is normal for partial results, no need to warn
                                column_metadata.append({
                                    'name': str(column),
                                    'type': 'string'
                                })
                        
                        # Build result rows
                        for row in table.rows:
                            row_dict = {}
                            for i, column in enumerate(table.columns):
                                # Get column name safely
                                if hasattr(column, 'name'):
                                    col_name = column.name
                                elif isinstance(column, dict):
                                    col_name = column.get('name', f'column_{i}')
                                else:
                                    col_name = str(column)
                                
                                row_dict[col_name] = row[i]
                            results.append(row_dict)
                        
                        # Filter empty columns before adding to tables
                        raw_rows = [[row[i] for i in range(len(table.columns))] for row in table.rows]
                        filtered_columns, filtered_rows = self._filter_empty_columns(column_metadata, raw_rows)
                        
                        tables.append({
                            'name': table.name if hasattr(table, 'name') else 'PrimaryResult',
                            'columns': filtered_columns,
                            'rows': filtered_rows
                        })
                    
                    error_msg = str(response.partial_error) if hasattr(response, 'partial_error') else 'Partial results returned'
                    logger.warning(f"Partial result error: {error_msg}")
                    logger.info(f"Successfully processed {len(results)} rows from partial result")
                    
                    return {
                        'status': 'success',  # Treat as success since we have data
                        'tables': tables,
                        'results': results,
                        'row_count': len(results),
                        'partial_warning': error_msg
                    }
                except Exception as partial_error:
                    # Log full error details (including stack trace) on the server for diagnostics
                    logger.error(f"Failed to process partial result: {partial_error}", exc_info=True)
                    # Return a generic error message to the caller without exposing internal details
                    return {
                        'status': 'error',
                        'message': "Query returned partial results but an internal error occurred while processing them. "
                                   "Please try again later or refine your query.",
                        'results': [],
                        'row_count': 0
                    }
            else:
                error_msg = f"Query failed with status: {response.status}"
                logger.error(error_msg)
                raise Exception(error_msg)
                
        except Exception as e:
            error_str = type(e).__name__
            logger.error("Error executing KQL query")
            
            # Check for schema errors (column doesn't exist, type mismatch, etc.)
            # These should NOT be retried - they need the query to be regenerated
            if "SEM0100" in str(e) or "Failed to resolve" in str(e) or "SemanticError" in str(e):
                logger.warning("Schema error detected - will NOT retry (needs query regeneration)")
                raise SchemaError("Query schema error")
            
            # Check for specific error types and provide helpful messages
            if "E_QUERY_RESULT_SET_TOO_LARGE" in str(e) or "64000000 bytes" in str(e):
                raise Exception(
                    "Query result exceeds Azure's 64MB limit. "
                    "Please refine your query by:\n"
                    "1. Adding a narrower time range (e.g., last 5 minutes instead of hours)\n"
                    "2. Adding more specific filters\n"
                    "3. Using 'summarize' to aggregate data\n"
                    "4. Adding '| take 10000' to limit results"
                )
            
            raise Exception("Query execution failed")
    
    def _filter_empty_columns(self, columns: List[Dict[str, Any]], rows: List[List[Any]]) -> tuple:
        """
        Filter out columns that are entirely empty (null, empty string, or None in all rows)
        Returns (filtered_columns, filtered_rows)
        """
        if not columns or not rows:
            return columns, rows
        
        # Track which column indices have at least one non-empty value
        non_empty_indices = []
        
        for col_idx in range(len(columns)):
            has_value = False
            for row in rows:
                if col_idx < len(row):
                    value = row[col_idx]
                    # Consider non-empty if value is not None, not empty string, and not just whitespace
                    if value is not None and str(value).strip() != '':
                        has_value = True
                        break
            
            if has_value:
                non_empty_indices.append(col_idx)
        
        # If all columns are non-empty, return as-is
        if len(non_empty_indices) == len(columns):
            return columns, rows
        
        # Filter columns
        filtered_columns = [columns[i] for i in non_empty_indices]
        
        # Filter rows to only include non-empty columns
        filtered_rows = []
        for row in rows:
            filtered_row = [row[i] for i in non_empty_indices if i < len(row)]
            filtered_rows.append(filtered_row)
        
        logger.info(f"Filtered out {len(columns) - len(filtered_columns)} empty columns")
        return filtered_columns, filtered_rows

    async def get_table_schema(self, table_name: str, workspace_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get schema information (columns and their types) for a specific table
        
        Args:
            table_name: Name of the table (e.g., 'AzureDiagnostics', 'AzureActivity')
            workspace_id: Log Analytics workspace ID
            user_info: User authentication information
            
        Returns:
            Dictionary containing column names and types
        """
        try:
            # Use getschema operator to get table schema
            schema_query = f"{table_name} | getschema | project ColumnName, DataType"
            
            logger.info(f"Discovering schema for table: {table_name}")
            result = await self.execute_kql_query(schema_query, workspace_id, user_info, timedelta(hours=1))
            
            # Extract column information
            columns = []
            if result.get('results'):
                for row in result['results']:
                    columns.append({
                        'name': row.get('ColumnName'),
                        'type': row.get('DataType')
                    })
            
            logger.info(f"Found {len(columns)} columns in {table_name}")
            return {
                'table': table_name,
                'columns': columns,
                'column_count': len(columns)
            }
            
        except Exception as e:
            logger.error(f"Error getting table schema for {table_name}: {e}")
            # Return empty schema rather than failing - table might not exist
            return {
                'table': table_name,
                'columns': [],
                'column_count': 0,
                'has_error': True
            }