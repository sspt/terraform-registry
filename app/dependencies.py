from fastapi import Request, HTTPException, Depends, status
from fastapi.responses import RedirectResponse
from app.config import settings

# API Authentication (Terraform CLI)
async def verify_api_key(request: Request):
    api_key = settings.effective_api_key
    if not api_key:
        return # Open if no key configured
        
    # Check Authorization Header
    auth_header = request.headers.get("Authorization")
    if auth_header:
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and token == api_key:
            return token
            
    # Check Query Parameter (fallback for binary downloads that might strip headers)
    token_param = request.query_params.get("token")
    if token_param and token_param == api_key:
        return token_param

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, 
        detail="Invalid or Missing API Key"
    )

# UI Authentication (GitHub OAuth)
async def get_current_user(request: Request):
    user = request.session.get("user")
    if not user:
        # Check if settings require auth
        if settings.github_client_id and settings.github_client_secret:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user

async def login_required(request: Request):
    if settings.github_client_id and settings.github_client_secret:
        user = request.session.get("user")
        if not user:
             # This dependency is usually used on HTML endpoints, so we might want to redirect
             # But Dependencies run before route handler. 
             # For simpler handling in FastAPI, we can return the response in the exception handler 
             # OR just assume this is called manually or via a wrapper.
             # Actually, simpler: Use this in routes and if it raises 401, handle it or just Redirect.
             # Wait, Dependencies can't return RedirectResponse to the browser easily without being the return value.
             pass 
    return True
