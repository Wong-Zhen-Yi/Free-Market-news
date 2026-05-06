@echo off
cd /d "%~dp0\.."
python app\newsbot.py --portfolio app\portfolio.json --db data\news.db --min-score 2 --limit 40
echo.
echo Done. Press any key to close this window.
pause >nul
