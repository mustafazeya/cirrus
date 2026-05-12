"""
Event Hub Firewall Log Streaming Service
Provides real-time firewall log streaming from Azure Event Hub (1-3 second latency).
"""

import asyncio
import ipaddress
import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from azure.eventhub.aio import EventHubConsumerClient
from config import settings

logger = logging.getLogger(__name__)


class EventHubFirewallService:
    """
    Consumes firewall logs from Azure Event Hub in real-time.
    Filters and broadcasts to connected WebSocket clients.
    """

    def __init__(self):
        self.connection_string = settings.EVENTHUB_CONNECTION_STRING
        self.eventhub_name = settings.EVENTHUB_NAME
        self.consumer_group = settings.EVENTHUB_CONSUMER_GROUP
        
        self.client: Optional[EventHubConsumerClient] = None
        self.active_consumers: Dict[str, Dict[str, Any]] = {}  # session_id -> {filters, callback}
        self.consumer_task: Optional[asyncio.Task] = None
        self.flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        
        # Time-windowed merge buffer for cross-partition ordering
        self.log_buffer: List[Dict[str, Any]] = []
        self.buffer_lock = asyncio.Lock()
        self.flush_interval = 0.25  # Flush every 250ms for real-time feel
        self.max_buffer_size = 10000  # Prevent memory overflow during traffic spikes

    async def start(self):
        """Initialize Event Hub consumer client and start consuming."""
        if not self.connection_string:
            logger.warning("EVENTHUB_CONNECTION_STRING not configured - Event Hub streaming disabled")
            return

        try:
            self.client = EventHubConsumerClient.from_connection_string(
                conn_str=self.connection_string,
                consumer_group=self.consumer_group,
                eventhub_name=self.eventhub_name,
            )
            
            self.consumer_task = asyncio.create_task(self._consume_events())
            self.flush_task = asyncio.create_task(self._flush_buffer_periodically())
            logger.info(f"Event Hub consumer started for {self.eventhub_name}")
        except Exception as e:
            logger.error(f"Failed to start Event Hub consumer: {e}")

    async def stop(self):
        """Stop flush_task:
            self.flush_task.cancel()
            try:
                await self.flush_task
            except asyncio.CancelledError:
                pass
        
        if self.consuming and close client."""
        if self.consumer_task:
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass

        if self.client:
            await self.client.close()
            logger.info("Event Hub consumer stopped")

    async def register_consumer(
        self,
        session_id: str,
        callback: Callable[[Dict[str, Any]], None],
        filters: Optional[Dict[str, Any]] = None,
    ):
        """
        Register a WebSocket client to receive filtered firewall logs.
        Starts Event Hub consumer on first connection (lazy start).
        
        Args:
            session_id: Unique session identifier
            callback: Async function to call with each matching log entry
            filters: Filter criteria (same format as log search)
        """
        async with self._lock:
            # Check if this is the first consumer
            is_first_consumer = len(self.active_consumers) == 0
            
            self.active_consumers[session_id] = {
                "callback": callback,
                "filters": filters or {},
                "registered_at": datetime.utcnow(),
            }
            logger.info(f"Registered consumer {session_id} with filters: {filters}")
            
            # Start Event Hub consumer if this is the first connection
            if is_first_consumer:
                await self.start()
                logger.info("Event Hub consumer started (first user connected)")

    async def unregister_consumer(self, session_id: str):
        """
        Unregister a WebSocket client.
        Stops Event Hub consumer when last user disconnects (lazy stop).
        """
        async with self._lock:
            if session_id in self.active_consumers:
                del self.active_consumers[session_id]
                logger.info(f"Unregistered consumer {session_id}")
                
                # Stop Event Hub consumer if this was the last connection
                if len(self.active_consumers) == 0:
                    logger.info("Last consumer disconnected - stopping Event Hub consumer")
                    # Schedule stop outside the lock to avoid deadlock
                    asyncio.create_task(self._stop_if_no_consumers())
    
    async def _stop_if_no_consumers(self):
        """Stop Event Hub consumer if no consumers remain (with delay to handle quick reconnects)."""
        # Wait a bit to handle quick reconnects (user refreshes page)
        await asyncio.sleep(5)
        
        async with self._lock:
            if len(self.active_consumers) == 0:
                await self.stop()
                logger.info("Event Hub consumer stopped (no active users)")

    async def _consume_events(self):
        """Background task to consume events from Event Hub."""
        if not self.client:
            return

        try:
            from datetime import datetime, timezone
            
            # Start from current time to skip backlog
            start_time = datetime.now(timezone.utc)
            logger.info(f"Starting Event Hub consumer from {start_time.isoformat()} (skipping backlog)")
            
            async with self.client:
                await self.client.receive_batch(
                    on_event_batch=self._on_event_batch,
                    starting_position=start_time,  # Start from now, not from beginning
                )
        except asyncio.CancelledError:
            logger.info("Event Hub consumer cancelled")
            raise
        except Exception as e:
            logger.error(f"Event Hub consumer error: {e}")

    async def _on_event_batch(self, partition_context, events):
        """Process a batch of events from Event Hub."""
        for event in events:
            try:
                await self._process_event(event)
            except Exception as e:
                logger.error(f"Error processing event: {e}")
        
        await partition_context.update_checkpoint()
    
    async def _flush_buffer_periodically(self):
        """
        Background task that flushes buffer every 250ms.
        Provides optimal balance: real-time feel + cross-partition time ordering.
        """
        try:
            while True:
                await asyncio.sleep(self.flush_interval)
                await self._flush_buffer()
        except asyncio.CancelledError:
            await self._flush_buffer()
            raise
        except Exception as e:
            logger.error(f"Buffer flush error: {e}")
    
    async def _flush_buffer(self):
        """
        Sort buffered logs by TimeGenerated and broadcast in chronological order.
        Oldest logs sent first, newest last - natural streaming like a terminal.
        """
        async with self.buffer_lock:
            if not self.log_buffer:
                return
            
            # Sort by TimeGenerated ASCENDING (oldest → newest, chronological order)
            sorted_logs = sorted(
                self.log_buffer,
                key=lambda x: x.get("TimeGenerated", "")
            )
            self.log_buffer = []
        
        # Broadcast all logs efficiently without artificial delays
        # Let WebSocket and network handle flow control naturally
        for log_entry in sorted_logs:
            await self._broadcast_to_consumers(log_entry)
            # Micro-yield to event loop for concurrency (no blocking)
            await asyncio.sleep(0)

    async def _process_event(self, event):
        """Parse firewall log event and add to buffer for time-ordered streaming."""
        try:
            body = event.body_as_json()
            
            # Azure diagnostic logs come as an array of "records"
            if isinstance(body, dict) and "records" in body:
                records = body["records"]
            elif isinstance(body, list):
                records = body
            else:
                records = [body]
            
            for record in records:
                log_entry = self._parse_firewall_log(record)
                if log_entry:
                    # Add to buffer with overflow protection
                    async with self.buffer_lock:
                        if len(self.log_buffer) < self.max_buffer_size:
                            self.log_buffer.append(log_entry)
                        else:
                            # Drop oldest logs if buffer full - keep most recent half + new entry
                            self.log_buffer = self.log_buffer[-(self.max_buffer_size//2):] + [log_entry]

        except Exception as e:
            logger.error(f"Failed to parse event: {e}")

    def _parse_firewall_log(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse Azure Firewall diagnostic log record into standardized format.
        Handles both structured logs and legacy msg_s format.
        """
        try:
            # Structured logs (AZFWNetworkRule, AZFWApplicationRule, AZFWNatRule)
            if "properties" in record:
                props = record["properties"]
                category = record.get("category", "")
                
                entry = {
                    "TimeGenerated": record.get("time", ""),
                    "RuleType": self._map_category_to_type(category),
                    "Action": props.get("action", props.get("Action", "")),
                    "SourceIp": props.get("sourceIp", props.get("SourceIp", "")),
                    "DestinationIp": props.get("destinationIp", props.get("DestinationIp", "")),
                    "DestinationPort": str(props.get("destinationPort", props.get("DestinationPort", ""))),
                    "Protocol": props.get("protocol", props.get("Protocol", "")),
                    "Rule": props.get("rule", props.get("Rule", "Default Deny")),
                    "RuleCollection": props.get("ruleCollection", props.get("RuleCollection", "")),
                    "RuleCollectionGroup": props.get("ruleCollectionGroup", props.get("RuleCollectionGroup", "")),
                    "TargetUrl": props.get("fqdn", props.get("Fqdn", props.get("targetUrl", props.get("TargetUrl", "")))),
                    "ThreatDescription": props.get("threatDescription", ""),
                }
                
                # For application logs without DestinationIp, use TargetUrl
                if not entry["DestinationIp"] and entry["TargetUrl"]:
                    entry["DestinationIp"] = entry["TargetUrl"]
                
                # Normalize empty values
                if not entry["Rule"] or entry["Rule"] == "-":
                    entry["Rule"] = "Default Deny"
                
                # Only process logs from the last 1 minute (ignore old backlog)
                try:
                    from datetime import datetime, timezone
                    log_time = datetime.fromisoformat(entry["TimeGenerated"].replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    age = (now - log_time).total_seconds()
                    
                    # Skip logs older than 1 minute (60 seconds)
                    if age > 60:
                        return None
                except Exception:
                    # If time parsing fails, allow the log through
                    pass
                
                return entry
            
            return None

        except Exception as e:
            logger.error(f"Failed to parse log record: {e}")
            return None

    def _map_category_to_type(self, category: str) -> str:
        """Map diagnostic log category to rule type."""
        mapping = {
            "AZFWNetworkRule": "Network",
            "AZFWApplicationRule": "Application",
            "AZFWNatRule": "NAT",
            "AZFWThreatIntel": "ThreatIntel",
        }
        return mapping.get(category, "Unknown")

    async def _broadcast_to_consumers(self, log_entry: Dict[str, Any]):
        """
        Send log entry to all consumers whose filters match.
        Uses concurrent tasks so slow clients don't block fast clients.
        """
        async with self._lock:
            consumers = list(self.active_consumers.items())

        # Send to all matching consumers concurrently (don't block on slow clients)
        tasks = []
        for session_id, consumer in consumers:
            if self._matches_filters(log_entry, consumer["filters"]):
                callback = consumer["callback"]
                if asyncio.iscoroutinefunction(callback):
                    # Create task but don't await - fires immediately without blocking
                    task = asyncio.create_task(self._safe_callback(callback, log_entry, session_id))
                    tasks.append(task)
        
        # Don't wait for tasks to complete - fire-and-forget for maximum throughput
        # Error handling is in _safe_callback
    
    async def _safe_callback(self, callback, log_entry: Dict[str, Any], session_id: str):
        """Wrapper for callback with error handling."""
        try:
            await callback(log_entry)
        except Exception as e:
            logger.debug(f"Callback error for {session_id}: {e}")

    def _matches_filters(self, log_entry: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        """Check if log entry matches filter criteria with proper group logic."""
        if not filters:
            return True

        groups = filters.get("groups", [])
        if not groups:
            return True

        # Evaluate each group
        group_results = []
        for group in groups:
            conditions = group.get("conditions", [])
            if not conditions:
                continue

            intra_connector = group.get("intra_connector", "AND").upper()
            
            # Evaluate conditions within the group
            cond_results = []
            for cond in conditions:
                field = cond.get("field", "")
                operator = cond.get("operator", "equals")
                value = cond.get("value", "")
                
                # Get field value from log entry
                field_value = str(log_entry.get(self._map_field_name(field), ""))
                
                # For case-insensitive string operations, lowercase both
                # BUT preserve original for IP/CIDR matching
                if operator in ["matches", "matches_in", "is_exactly"]:
                    # IP operators - preserve case for CIDR parsing
                    result = self._evaluate_condition(field_value, operator, value)
                else:
                    # String operators - case-insensitive
                    result = self._evaluate_condition(field_value.lower(), operator, value.lower())
                
                cond_results.append(result)

            # Combine condition results with intra_connector
            if intra_connector == "AND":
                group_result = all(cond_results) if cond_results else False
            else:  # OR
                group_result = any(cond_results) if cond_results else False
            
            group_results.append((group_result, group.get("inter_connector", "AND").upper()))
            
        if not group_results:
            return True
        
        # Combine group results with their inter_connectors
        final_result = group_results[0][0]
        for i in range(1, len(group_results)):
            # Use the inter_connector from the PREVIOUS group
            connector = group_results[i-1][1]
            if connector == "AND":
                final_result = final_result and group_results[i][0]
            else:  # OR
                final_result = final_result or group_results[i][0]
        
        return final_result

    def _map_field_name(self, field: str) -> str:
        """Map filter field names to log entry field names."""
        mapping = {
            "action": "Action",
            "source_ip": "SourceIp",
            "dest_ip": "DestinationIp",
            "dest_port": "DestinationPort",
            "protocol": "Protocol",
            "rule_name": "Rule",
            "rule_collection": "RuleCollection",
            "rule_group": "RuleCollectionGroup",
            "dest_fqdn": "TargetUrl",
            "type": "RuleType",
        }
        return mapping.get(field, field)

    def _evaluate_condition(self, field_value: str, operator: str, filter_value: str) -> bool:
        """Evaluate a single filter condition with full operator support."""
        # Basic string operators
        if operator == "equals":
            return field_value == filter_value
        elif operator == "not_equals":
            return field_value != filter_value
        elif operator == "contains":
            return filter_value in field_value
        elif operator == "not_contains":
            return filter_value not in field_value
        elif operator == "starts_with":
            return field_value.startswith(filter_value)
        elif operator == "ends_with":
            return field_value.endswith(filter_value)
        
        # IP/CIDR matching operators
        elif operator == "matches":
            # IP/CIDR overlap matching
            return self._ip_matches(field_value, filter_value)
        elif operator == "is_exactly":
            # Exact IP match (case-insensitive string comparison)
            return field_value == filter_value
        
        # List-based operators (comma-separated values)
        elif operator == "matches_in":
            # Match against any IP/CIDR in the list
            values = [v.strip() for v in filter_value.split(',') if v.strip()]
            return any(self._ip_matches(field_value, v) for v in values)
        elif operator == "exactly_in":
            # Exact match against any value in the list
            values = [v.strip().lower() for v in filter_value.split(',') if v.strip()]
            return field_value in values
        
        # Fallback to equals
        else:
            return field_value == filter_value
    
    def _ip_matches(self, field_value: str, filter_value: str) -> bool:
        """Check if field_value IP/CIDR matches or overlaps with filter_value.
        
        Supports:
        - Single IP: 10.0.0.1 matches 10.0.0.0/24
        - CIDR range: 10.0.0.0/24 overlaps with 10.0.0.0/16
        - Exact match: 10.0.0.1 matches 10.0.0.1
        """
        try:
            # Parse both as IP networks
            field_net = ipaddress.ip_network(field_value.strip(), strict=False)
            filter_net = ipaddress.ip_network(filter_value.strip(), strict=False)
            
            # Check for overlap or exact match
            # overlaps() returns True if there's any overlap
            return field_net.overlaps(filter_net) or field_net == filter_net
        except (ValueError, AttributeError):
            # If not valid IP/CIDR, fallback to string comparison
            return field_value == filter_value


# Global singleton instance
eventhub_firewall_service = EventHubFirewallService()
