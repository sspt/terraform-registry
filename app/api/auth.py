from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.config import settings
import uuid
import time
import logging

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

# Simple in-memory (not production ready for multi-worker)
AUTH_CODES = {}

@router.api_route("/v1/login", methods=["GET", "HEAD"], response_class=JSONResponse)
def login_discovery():
    """
    Terraform Login Protocol Service Discovery
    Values are relative to the service root if they start with /
    """
    return JSONResponse(content={
        "client": "terraform-cli",
        "grant_types": ["authz_code"],
        "authz": "/v1/login/authorize",
        "token": "/v1/login/token",
        "ports": [10009, 10010]
    })

@router.get("/v1/login/authorize", response_class=HTMLResponse)
async def authorize(
    request: Request, 
    redirect_uri: str = None, 
    state: str = None,
    code_challenge: str = None, 
    client_id: str = None # Accepted but ignored
):
    """
    OAuth2 Authorization Endpoint for Terraform CLI
    Automatically authorizes the request using the server's configured API_TOKEN.
    """
    if not settings.effective_api_key:
         return HTMLResponse(
             "<h3>Error: Server has no AUTH_API_KEY or API_TOKEN configured.</h3>", 
             status_code=500
         )

    # Automatic Authorization
    # We trust the flow because the user initiated it via CLI and confirmed "yes".
    # Since we are using a static token known to the server, we just grant access.

    # Generate a temporary authorization code
    api_code = str(uuid.uuid4())
    AUTH_CODES[api_code] = {
        "ts": time.time(),
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge
    }
    
    # Auto-redirect back to Terraform's local listener
    sep = "&" if "?" in redirect_uri else "?"
    target = f"{redirect_uri}{sep}code={api_code}&state={state}"
    
    return templates.TemplateResponse("auth_success.html", {
        "request": request,
        "target_url": target,
        "api_token": settings.effective_api_key
    })

@router.post("/v1/login/token")
async def token(request: Request):
    """
    OAuth2 Token Endpoint
    Exchanges the authorization code for the API Key
    """
    form = await request.form()
    grant_type = form.get("grant_type")
    code = form.get("code")
    redirect_uri = form.get("redirect_uri")
    
    # Basic validation
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    
    if code not in AUTH_CODES:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    
    data = AUTH_CODES[code]
    
    # Validate expiry (5 mins)
    if time.time() - data["ts"] > 300:
        del AUTH_CODES[code]
        return JSONResponse({"error": "expired_grant"}, status_code=400)
        
    # Remove used code
    del AUTH_CODES[code]
    
    # Return the static API Key
    return {
        "access_token": settings.effective_api_key,
        "token_type": "Bearer",
    }
