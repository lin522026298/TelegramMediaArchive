@echo off
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" set "LOGDIR=%~dp0..\logs"
start "" "%LOGDIR%"
