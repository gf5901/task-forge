#!/usr/bin/env python3
"""Entry point for the web UI."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

os.chdir(Path(__file__).parent)

import uvicorn

from src.web import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", "8080")),
    )
