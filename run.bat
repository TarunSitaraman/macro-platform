@echo off
call .venv\Scripts\activate.bat
set PYTHONPATH=%CD%
echo Starting Hexaware Macro Platform...
streamlit run src\ui\app.py
