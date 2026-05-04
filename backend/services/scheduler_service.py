"""
Background Scheduler Service
Manages periodic tasks using APScheduler
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from services.background_prefetch_service import background_prefetch_service
from config import settings
import asyncio

logger = logging.getLogger(__name__)

class BackgroundScheduler:
    """
    Manages background scheduled tasks for the application
    """
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
    
    def start_scheduler(self):
        """Start the background scheduler"""
        try:
            if self.is_running:
                logger.warning("Scheduler is already running")
                return
            
            # Add prefetch job - configurable interval
            self.scheduler.add_job(
                background_prefetch_service.run_full_prefetch,
                trigger=IntervalTrigger(minutes=settings.PREFETCH_INTERVAL_MINUTES),
                id='prefetch_azure_data',
                name='Prefetch Azure Data',
                replace_existing=True,
                max_instances=1  # Prevent overlapping executions
            )
            
            # Add smart initial prefetch - checks cache age before running
            from datetime import datetime, timedelta
            initial_check_time = datetime.now() + timedelta(seconds=10)
            self.scheduler.add_job(
                self._smart_initial_prefetch,
                trigger='date',
                run_date=initial_check_time,  # Check after 10 seconds
                id='initial_prefetch_check',
                name='Initial Prefetch Check',
                replace_existing=True
            )
            
            # Start the scheduler
            self.scheduler.start()
            self.is_running = True
                
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
    
    def stop_scheduler(self):
        """Stop the background scheduler"""
        try:
            if not self.is_running:
                logger.warning("Scheduler is not running")
                return
            
            self.scheduler.shutdown(wait=True)
            self.is_running = False
            logger.info("Background scheduler stopped")
            
        except Exception as e:
            logger.error(f"Failed to stop scheduler: {e}")
    
    def get_job_status(self):
        """Get status of all scheduled jobs"""
        if not self.is_running:
            return {"scheduler_running": False, "jobs": [], "next_run_time": None}
        
        jobs = []
        next_run_time = None
        for job in self.scheduler.get_jobs():
            job_next_run = job.next_run_time.isoformat() if job.next_run_time else None
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": job_next_run,
                "last_run_time": getattr(job, 'last_run_time', None),
                "trigger": str(job.trigger)
            })
            # Set next_run_time to the earliest scheduled job
            if job.next_run_time and (next_run_time is None or job_next_run < next_run_time):
                next_run_time = job_next_run
        
        return {
            "scheduler_running": True,
            "jobs": jobs,
            "next_run_time": next_run_time
        }
    
    async def trigger_immediate_prefetch(self):
        """Trigger an immediate prefetch manually (invalidates cache first)"""
        try:
            logger.info("Manual prefetch triggered")
            await background_prefetch_service.run_full_prefetch(force_refresh=True)
            logger.info("Manual prefetch completed")
            return {"success": True, "message": "Cache invalidated and prefetch completed successfully"}
        except Exception:
            logger.error("Failed to trigger immediate prefetch")
            return {"success": False, "message": "Prefetch failed"}
    
    async def _smart_initial_prefetch(self):
        """Smart prefetch: only run if cache is expired or missing"""
        try:
            from services.cache_service import cache_service
            from datetime import datetime
            
            # Check if we have cached subscriptions
            cached_subs = await cache_service.get("global", "all_subscriptions")
            last_prefetch = await cache_service.get("global", "last_prefetch_timestamp")
            
            if cached_subs and last_prefetch:
                # Calculate cache age
                cache_age_seconds = (datetime.now() - datetime.fromisoformat(last_prefetch)).total_seconds()
                cache_age_hours = cache_age_seconds / 3600
                ttl_hours = settings.PREFETCH_CACHE_TTL_SECONDS / 3600
                
                logger.info(f"Cache found: age {cache_age_hours:.1f}h / TTL {ttl_hours:.1f}h ({len(cached_subs)} subs)")
                
                # Only prefetch if cache is expired
                if cache_age_seconds < settings.PREFETCH_CACHE_TTL_SECONDS:
                    logger.info(f" Cache is fresh - skipping initial prefetch")
                    return
                else:
                    logger.info(f" Cache expired - running prefetch")
            else:
                logger.info("No cache found - running initial prefetch")
            
            await background_prefetch_service.run_full_prefetch()
            
        except Exception as e:
            logger.error(f"Smart prefetch check failed: {e}")

# Global scheduler instance
scheduler_service = BackgroundScheduler()