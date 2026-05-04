"""
Log Analysis Service
Prepares log data for AI analysis with simple sampling
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

class LogAnalysisService:
    """Service for preparing log data for AI analysis"""
    
    def prepare_for_ai_analysis(self, results: List[Dict], query: str, max_sample_rows: int = 50) -> Dict[str, Any]:
        """
        Prepare log results for AI analysis by passing raw rows directly.
        The AI decides what's anomalous — no Python-side classification.

        Args:
            results: Raw KQL results (list of dicts)
            query: Original KQL query for context
            max_sample_rows: Maximum rows to include in the sample

        Returns:
            Dict with total row count and evenly sampled raw rows
        """
        if not results:
            return {"total_rows": 0, "message": "No results"}

        total = len(results)
        if total <= max_sample_rows:
            sample = results
        else:
            # Evenly spaced indices that always include the first and last row
            indices = [int(i * (total - 1) / (max_sample_rows - 1)) for i in range(max_sample_rows)]
            sample = [results[i] for i in indices]

        sample = self._remove_empty_columns(sample)

        return {
            "total_rows": total,
            "sampled_rows": len(sample),
            "is_sampled": len(sample) < total,
            "time_range": self._get_time_range(results),
            "sample_entries": sample,
        }

    def _remove_empty_columns(self, rows: List[Dict]) -> List[Dict]:
        """Remove columns that are empty/null in all sampled rows"""
        if not rows:
            return rows

        non_empty_cols = set()
        for row in rows:
            for key, val in row.items():
                if val is not None and val != '' and str(val).lower() != 'null':
                    non_empty_cols.add(key)

        return [{k: v for k, v in row.items() if k in non_empty_cols} for row in rows]

    def _get_time_range(self, results: List[Dict]) -> Dict[str, str]:
        """Extract time range from results"""
        time_fields = ["TimeGenerated", "timestamp", "Timestamp", "time"]

        for field in time_fields:
            times = [r.get(field) for r in results if r.get(field)]
            if times:
                try:
                    parsed_times = []
                    for t in times:
                        if isinstance(t, str):
                            parsed_times.append(datetime.fromisoformat(t.replace('Z', '+00:00')))
                        elif isinstance(t, datetime):
                            parsed_times.append(t)

                    if parsed_times:
                        return {
                            "start": min(parsed_times).isoformat(),
                            "end": max(parsed_times).isoformat()
                        }
                except Exception as e:
                    logger.warning(f"Failed to parse time range: {e}")

        return {"start": "unknown", "end": "unknown"}

# Global instance
log_analysis_service = LogAnalysisService()
