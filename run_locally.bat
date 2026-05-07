@echo off
cd /d "%~dp0"
echo Starting As-Built Stamper...
echo.
echo The app will open in your browser automatically.
echo To stop it, press Ctrl+C in this window.
echo.
streamlit run asbuilt_stamper.py
pause
