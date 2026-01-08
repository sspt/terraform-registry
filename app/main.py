from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from app.api import registry
from app.web import ui
from app.config import settings
from app.services.github_service import github_service

app = FastAPI(title="Terraform GitHub Registry Proxy")

# Add Session Middleware for UI Auth
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Mount templates
templates = Jinja2Templates(directory="app/templates")

@app.on_event("startup")
async def startup_event():
    await github_service.warmup_cache()

# Service Discovery
@app.get("/.well-known/terraform.json")
def service_discovery():
    return {
        "modules.v1": "/v1/modules/"
    }

# Include Routers
app.include_router(registry.router, prefix="/v1/modules", tags=["registry"])
app.include_router(ui.router, tags=["ui"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
