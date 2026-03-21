#!/usr/bin/env python3
"""Entry point for the self-healing pipeline runner.

Usage:
    python run_heal.py           # run all healing strategies
    python run_heal.py --dry-run # report what would be healed without changing anything (TODO)
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
os.chdir(Path(__file__).parent)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

from src.dynamo_store import DynamoTaskStore
from src.healer import run_healer

store = DynamoTaskStore()

stale, pr, cancelled, worktrees = run_healer(store)
print("Healer: %d stale reset, %d PRs fixed, %d cancelled recovered, %d worktrees cleaned" % (stale, pr, cancelled, worktrees))
