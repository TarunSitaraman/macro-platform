@echo off
echo ============================================
echo  Hexaware Macro Platform - Local Setup
echo ============================================

REM 1. Create virtual environment
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM 2. Activate
call .venv\Scripts\activate.bat

REM 3. Upgrade pip silently
python -m pip install --upgrade pip --quiet

REM 4. Install dependencies
echo Installing dependencies...
pip install -r requirements.txt --quiet
pip install plotly --quiet

REM 5. Install package in editable mode (fixes src.* imports)
echo Installing package in editable mode...
pip install -e . --quiet

REM 6. Check for .env
if not exist ".env" (
    echo.
    echo IMPORTANT: .env file not found.
    echo Copying .env.example to .env - fill in your credentials before running.
    copy .env.example .env
)

echo.
echo ============================================
echo  Setup complete. Next steps:
echo.
echo  1. Edit .env with your credentials:
echo       DATABASE_URL   = your Neon connection string
echo       OPENROUTER_API_KEY = your OpenRouter key
echo       OPENAI_API_KEY = your OpenAI key (for embeddings)
echo       FRED_API_KEY   = your FRED key (optional)
echo.
echo  2. Initialize the database:
echo       python -c "from src.database import init_db; init_db()"
echo.
echo  3. Run the app:
echo       streamlit run src\ui\app.py
echo.
echo  4. (Optional) Run the API in a second terminal:
echo       uvicorn src.api.main:app --reload --port 8000
echo ============================================
pause
