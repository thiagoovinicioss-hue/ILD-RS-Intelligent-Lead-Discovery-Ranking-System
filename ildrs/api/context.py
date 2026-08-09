"""Application context shared across routes via ``request.app.state``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ildrs.jobs.scheduler import Scheduler
from ildrs.notifications.notifier import Notifier
from ildrs.outreach.workflow import OutreachWorkflow
from ildrs.pipeline.orchestrator import Orchestrator
from ildrs.sources.base import BusinessSource
from ildrs.storage.database import Database


@dataclass
class AppContext:
    db: Database
    source: BusinessSource
    notifier: Notifier
    orchestrator: Orchestrator
    outreach: OutreachWorkflow
    scheduler: Scheduler = field(default_factory=Scheduler)
    background_tasks: set[asyncio.Task] = field(default_factory=set)
