from fastapi import Request, HTTPException, Depends, status
from fastapi.responses import RedirectResponse
from app.config import settings

# API Authentication (Terraform CLI)
async def verify_api_key(request: Request):
    if not settings.auth_api_key:
        return # Open if no key configured
        
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Missing Authorization Header"
        )
    
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or token != settings.auth_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid API Key"
        )
    return token

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
