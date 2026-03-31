# Backend (starts both uvicorn + celery worker)
.\start-backend.ps1

# Or manually in separate terminals:
uvicorn backend.api.main:app --reload
celery -A backend.celery_app worker --loglevel=info --pool=threads --concurrency=4

npm run dev

./compare-flight.sh 2057 BOM 2026-03-20 18:49
