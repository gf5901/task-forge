#!/usr/bin/env python3
"""Entry point for the Task Forge Discord bot."""

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

from src.bot import TaskBot


def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("Error: DISCORD_BOT_TOKEN not set. Copy .env.example to .env and fill it in.")
        sys.exit(1)
    bot = TaskBot()
    bot.run(token)


if __name__ == "__main__":
    main()
