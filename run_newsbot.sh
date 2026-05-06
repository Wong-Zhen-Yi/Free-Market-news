#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 app/newsbot.py --portfolio app/portfolio.json --db data/news.db --min-score 2 --limit 40
