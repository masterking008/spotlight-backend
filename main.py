import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from database import get_db, create_tables
from auth import make_internal_user_dep
import storage as storage_svc

from routers import auth, casting_calls, applications, pitch_decks, notifications, overview, ai

# Internal user dep — used by routers that need it
make_internal_user_dep(get_db)

# ─── Startup / Lifespan ───────────────────────────────────────────────────────

def _check_env():
    required = ["SUPABASE_URL", "SUPABASE_ANON_KEY", "DATABASE_URL"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"⚠️  Missing env vars: {missing}. Some features may not work.")


def _ensure_storage_bucket():
    """Create the spotlight-media bucket if it doesn't exist yet."""
    try:
        client = storage_svc._admin_client()
        buckets = [b.name for b in client.storage.list_buckets()]
        if storage_svc.BUCKET not in buckets:
            client.storage.create_bucket(
                storage_svc.BUCKET,
                options={"public": False},
            )
            print(f"✅ Created storage bucket '{storage_svc.BUCKET}'")
        else:
            print(f"✅ Storage bucket '{storage_svc.BUCKET}' ready")
    except Exception as e:
        print(f"⚠️  Storage bucket init error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_env()
    try:
        create_tables()
        print("✅ Database tables ready")
    except Exception as e:
        print(f"⚠️  DB init error: {e}")
    _ensure_storage_bucket()
    yield


app = FastAPI(
    title="Spotlight Casting Platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:4173",
        os.getenv("FRONTEND_URL", "https://spotlight.ruskmedia.com"),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# ─── Routers ──────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(casting_calls.router)
app.include_router(applications.router)
app.include_router(pitch_decks.router)
app.include_router(notifications.router)
app.include_router(overview.router)
app.include_router(ai.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
