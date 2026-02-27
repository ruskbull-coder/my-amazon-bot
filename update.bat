@echo off
echo =======================================
echo   Discord Bot Update Start
echo =======================================

echo 1. Adding changes...
git add .

echo 2. Committing...
set /p msg="Enter commit message (e.g. Fixed image): "
git commit -m "%msg%"

echo 3. Pushing to GitHub...
git push origin main

echo =======================================
echo   Update Complete! Check Render Logs.
echo =======================================
pause