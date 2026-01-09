from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from app.api import registry, auth
from app.web import ui
from app.config import settings
from app.services.github_service import github_service
import asyncio

app = FastAPI(title="Terraform GitHub Registry Proxy")

# Add Session Middleware for UI Auth
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Mount templates
templates = Jinja2Templates(directory="app/templates")

@app.on_event("startup")
async def startup_event():
    # Run warmup in the background so it doesn't block server startup
    asyncio.create_task(github_service.warmup_cache())

# Service Discovery
@app.api_route("/.well-known/terraform.json", methods=["GET", "HEAD"], response_class=JSONResponse)
def service_discovery(request: Request):
    print(f"Service Discovery Hit. Headers: {request.headers}")
    return JSONResponse(content={
        "modules.v1": "/v1/modules/",
        "login.v1": {
            "client": "terraform-cli",
            "grant_types": ["authz_code"],
            "authz": "/v1/login/authorize",
            "token": "/v1/login/token",
            "ports": [10009, 10010]
        }
    })

# Include Routers
app.include_router(registry.router, prefix="/v1/modules", tags=["registry"])
app.include_router(auth.router, tags=["auth"])
app.include_router(ui.router, tags=["ui"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
