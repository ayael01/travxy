# Travxy

FastAPI backend that builds day plans from Geoapify / OpenTripMap.

## Quickstart

```bash
cd apps/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt  # or `pip install -e .` if you package it
cp .env.example .env  # add your API keys
uvicorn app.main:app --reload
