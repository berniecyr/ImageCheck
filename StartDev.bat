@echo off
setlocal enabledelayedexpansion

echo Starting AI Monitor...

:: 1. Get the current script directory and strip the trailing backslash
set "CURRENT_DIR=%~dp0"
if "%CURRENT_DIR:~-1%"=="\" set "CURRENT_DIR=%CURRENT_DIR:~0,-1%"

set "VENV_DIR=%CURRENT_DIR%\venv"
set "LOCATION_FILE=%VENV_DIR%\.venv_location"

:: 2. Verify existing Venv location
if exist "%VENV_DIR%\Scripts\activate.bat" (
    if exist "%LOCATION_FILE%" (
        :: Read the saved location from the marker file
        set /p SAVED_LOCATION=<"%LOCATION_FILE%"
        
        if not "!SAVED_LOCATION!"=="%CURRENT_DIR%" (
            echo [Setup] Folder moved or renamed! 
            echo [Setup] Old path: !SAVED_LOCATION!
            echo [Setup] New path: %CURRENT_DIR%
            echo [Setup] Rebuilding virtual environment for new location...
            rmdir /s /q "%VENV_DIR%"
        )
    ) else (
        echo [Setup] Unverified venv detected. Rebuilding to guarantee portability...
        rmdir /s /q "%VENV_DIR%"
    )
)

:: 3. Create and configure the Venv if it is missing
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [Setup] Creating new virtual environment...
    python -m venv venv
    
    :: Write the current path into the marker file (no trailing spaces)
    >"%LOCATION_FILE%" echo %CURRENT_DIR%
    
    echo [Setup] Activating...
    call venv\Scripts\activate.bat
    
    echo [Setup] Upgrading core build tools...
    python -m pip install --upgrade pip setuptools wheel
    
    echo [Setup] Installing dependencies from requirements.txt...
    pip install -r requirements.txt
    
    echo [Setup] Complete!
) else (
    :: Venv exists and location is verified
    call venv\Scripts\activate.bat
)

:: 4. Run the master script
python ImageCheckDev.py
rem  --scanonly "G:\Pictures\Rosemary Misc"
rem --scanonly "S:\ImageCheckDev\TestingOKTODELETE2026-06-15-Export_Files.TXT"
rem --scanonly "S:\ImageCheckDev\Inbox\Test"
rem --scanonly "G:\Pictures\Rosemary Misc"
rem "S:\ImageCheckDev\venv\Scripts\python.exe" "S:\ImageCheckDev\ImageCheckDev.py" --scanonly "S:\Sighthound\2017"
rem "S:\ImageCheckDev\venv\Scripts\python.exe" "S:\ImageCheckDev\ImageCheckDev.py" --scanonly "S:\Sighthound\2018"
rem "S:\ImageCheckDev\venv\Scripts\python.exe" "S:\ImageCheckDev\ImageCheckDev.py" --scanonly "S:\Sighthound\2019"
rem "S:\ImageCheckDev\venv\Scripts\python.exe" "S:\ImageCheckDev\ImageCheckDev.py" --scanonly "S:\Sighthound\2020"
rem "S:\ImageCheckDev\venv\Scripts\python.exe" "S:\ImageCheckDev\ImageCheckDev.py" --scanonly "S:\Sighthound\2021"
rem "S:\ImageCheckDev\venv\Scripts\python.exe" "S:\ImageCheckDev\ImageCheckDev.py" --scanonly "S:\Sighthound\2022"
rem --mode faceonly --scanonly "G:\Pictures\Sunshine and Raindrops 2012"
rem  --mode faceonly --scanonly "S:\Sighthound\2018"
rem --scanonly "S:\ImageCheckDev\Testing" 
rem --mode faceonly 
rem --hide-boxes
rem --mode faceonly --scanonly "S:\saved" 

pause