"""
Shared types for the task system.

Enums, dataclasses, and constants used by DynamoTaskStore and all pipeline modules.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Comment:
    author: str
    body: str
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


class ModelTier(str, Enum):
    FAST = "fast"
    DEFAULT = "default"
    FULL = "full"


@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""
    tags: List[str] = field(default_factory=list)
    target_repo: str = ""
    parent_id: str = ""
    model: str = ""
    plan_only: bool = False
    depends_on: List[str] = field(default_factory=list)
    session_id: str = ""
    reply_pending: bool = False
    role: str = ""
    spawned_by: str = ""
    project_id: str = ""
    directive_sk: str = ""
    directive_date: str = ""
    assignee: str = "agent"

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
