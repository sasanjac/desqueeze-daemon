#! /usr/bin/python
from __future__ import annotations

import sys
import time
from pathlib import Path

import schedule
from loguru import logger

from desqueeze_daemon.daemon import Daemon

logger.remove()
logger.add(sys.stderr, colorize=True, format="<level>{message}</level>")

import_path = Path("/data/import")
export_path = Path("/data/export")

logger.info("Starting daemon...")
d = Daemon(import_path=import_path, export_path=export_path)

d.desqueeze()
schedule.every(5).minutes.do(d.desqueeze)


while True:
    schedule.run_pending()
    time.sleep(10)
