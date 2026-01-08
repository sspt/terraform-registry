from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.services.github_service import github_service
from app.config import settings
import logging
import httpx

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

def is_authenticated(request: Request) -> bool:
    if not settings.github_client_id:
        return True # Auth not configured, allow
    return request.session.get("user") is not None

@router.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/")
    return templates.TemplateResponse("login.html", {"request": request})

@router.get("/login/github")
async def login_github(request: Request):
    if not settings.github_client_id:
        return RedirectResponse("/")
    
    # Simple redirect - assuming standard port or proxied
    # Best to use request.base_url to determine callback if app_host not set perfectly
    base_url = str(request.base_url).rstrip("/")
    
    # Force HTTPS if detected as http (common behind proxies like ngrok)
    if base_url.startswith("http://"):
        base_url = base_url.replace("http://", "https://", 1)
        
    redirect_uri = f"{base_url}/auth/callback"
    
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=read:user"
    )
    return RedirectResponse(url)

@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

@router.get("/auth/callback")
async def auth_callback(request: Request, code: str):
    if not code:
        return RedirectResponse("/")
        
    async with httpx.AsyncClient() as client:
        # Exchange code
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code
            }
        )
        if resp.status_code != 200:
             return HTMLResponse("Auth Failed during token exchange", status_code=400)
             
        token_data = resp.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            return HTMLResponse(f"Auth Failed: {token_data.get('error_description')}", status_code=400)
            
        # Get User
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {access_token}"}
        )
        if user_resp.status_code != 200:
             return HTMLResponse("Failed to fetch user profile", status_code=400)
             
        user = user_resp.json()
        # Optionally filter by Org membership if 'target_org' is set?
        # For now just login.
        
        request.session["user"] = {"login": user["login"], "avatar_url": user["avatar_url"], "name": user.get("name")}
        
    return RedirectResponse("/")

def get_common_context(request: Request):
    # Use Host header for 'terraform login' command to match user's URL (e.g. ngrok)
    display_host = request.headers.get("host")
    if not display_host:
         # Fallback to configured app_host
         from urllib.parse import urlparse
         parsed = urlparse(settings.app_host)
         display_host = parsed.netloc if parsed.netloc else settings.app_host

    return {
        "request": request,
        "target_org": settings.target_org,
        "app_host": settings.app_host,
        "display_host": display_host,
        "api_token": settings.auth_api_key if is_authenticated(request) else None
    }

@router.get("/api/search")
async def api_search(request: Request, q: str):
    if not is_authenticated(request):
        return JSONResponse([], status_code=401)

    if not q or len(q) < 2:
        return JSONResponse([])
    
    modules = await github_service.search_modules(q)
    results = []
    
    # Limit results for autocomplete
    for m in modules[:10]:
         results.append({
             "name": m["name"],
             "provider": m["provider"], 
             "namespace": m["namespace"],
             "url": f"/browse/{m['namespace']}/{m['name']}/{m['provider']}"
         })
         
    return JSONResponse(results)

@router.post("/cache/clear")
async def clear_cache(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    logger.info("Clearing application cache")
    github_service.clear_cache()
    # Redirect back to where they came from or home
    return RedirectResponse(url="/", status_code=303)

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login")
    logger.info("Accessing index page. Fetching providers.")
    
    # In monorepo/discovery mode, show list of providers first
    if github_service.is_monorepo():
        providers = await github_service.get_providers()
        
        # Enrich with module count
        for provider in providers:
            modules = await github_service.search_modules("", provider_filter=provider["name"])
            provider["module_count"] = len(modules)
            
        return templates.TemplateResponse("index.html", {**get_common_context(request), "providers": providers, "query": ""})

    # Fallback for standard mode (which is not main use case here) -> just redirect to a default or show all?
    # For now, let's keep old behavior for non-monorepo or just show empty providers
    modules = await github_service.search_modules("")
    # ... processing logic ...
    # This path might need update if we want to support non-monorepo fully, but request is for monorepo mainly.
    # We will assume monorepo logic for now.
    return templates.TemplateResponse("index.html", {**get_common_context(request), "modules": modules, "query": ""})

@router.get("/provider/{provider}", response_class=HTMLResponse)
async def provider_modules(request: Request, provider: str, page: int = 1, page_size: int = 20):
    if not is_authenticated(request):
        return RedirectResponse("/login")
    logger.info(f"Listing modules for provider: {provider}")
    
    # Fetch modules for this provider
    # Note: search_modules (via get_modules_for_provider) now handles enrichment (versions, desc) directly.
    modules = await github_service.search_modules("", provider_filter=provider)
    
    # Extract unique Groups
    groups = {}
    for m in modules:
        g = m.get("group", "General")
        slug = m.get("group_slug", "General")
        groups[slug] = g
    
    sorted_groups = sorted(groups.items(), key=lambda x: x[1]) # Sort by Display Name

    return templates.TemplateResponse("provider_modules.html", {
        **get_common_context(request),
        "view_mode": "groups",
        "groups": sorted_groups,
        "provider": provider,
        "query": ""
    })

@router.get("/provider/{provider}/{group_slug}", response_class=HTMLResponse)
async def provider_subfolders(request: Request, provider: str, group_slug: str):
    if not is_authenticated(request):
        return RedirectResponse("/login")
    modules = await github_service.search_modules("", provider_filter=provider)
    
    # Filter by Group
    parents = {}
    current_group_name = group_slug
    
    for m in modules:
        if m.get("group_slug") == group_slug:
            current_group_name = m.get("group", group_slug)
            p = m.get("parent_dir", "Root")
            slug = m.get("parent_slug", "Root")
            parents[slug] = p
            
    sorted_parents = sorted(parents.items(), key=lambda x: x[1])

    # Optimization: If there is only one parent folder (e.g. "General" or "Root"),
    # bypass the intermediate screen and go directly to module list.
    logger.info(f"Group '{group_slug}' parents: {sorted_parents}")
    
    if len(sorted_parents) == 1:
        only_slug = sorted_parents[0][0]
        target_url = f"/provider/{provider}/{group_slug}/{only_slug}"
        logger.info(f"Single parent found. Redirecting to {target_url}")
        return RedirectResponse(url=target_url, status_code=302)

    return templates.TemplateResponse("provider_modules.html", {
        **get_common_context(request),
        "view_mode": "parents",
        "parents": sorted_parents,
        "current_group": current_group_name,
        "current_group_slug": group_slug,
        "provider": provider,
        "query": ""
    })

@router.get("/provider/{provider}/{group_slug}/{parent_slug:path}", response_class=HTMLResponse)
async def provider_modules_list(request: Request, provider: str, group_slug: str, parent_slug: str):
    if not is_authenticated(request):
        return RedirectResponse("/login")
    modules = await github_service.search_modules("", provider_filter=provider)
    
    filtered_modules = []
    current_group_name = group_slug
    current_parent_name = parent_slug
    
    for m in modules:
        if m.get("group_slug") == group_slug and m.get("parent_slug") == parent_slug:
            filtered_modules.append(m)
            current_group_name = m.get("group", group_slug)
            current_parent_name = m.get("parent_dir", parent_slug)
            
    # Sort modules
    filtered_modules.sort(key=lambda x: x["short_name"])

    return templates.TemplateResponse("provider_modules.html", {
        **get_common_context(request),
        "view_mode": "modules",
        "modules": filtered_modules,
        "current_group": current_group_name,
        "current_group_slug": group_slug,
        "current_parent": current_parent_name,
        "current_parent_slug": parent_slug,
        "provider": provider,
        "query": ""
    })

@router.post("/search", response_class=HTMLResponse)
async def search(request: Request, query: str = Form(...)):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    logger.info(f"Searching via UI with query: {query}")
    modules = await github_service.search_modules(query)
    # Modules are already parsed and enriched by service
    return templates.TemplateResponse("index.html", {**get_common_context(request), "modules": modules, "query": query})

@router.get("/browse/{namespace}/{name:path}/{provider}", response_class=HTMLResponse)
async def module_detail(request: Request, namespace: str, name: str, provider: str, version: str = None):
    if not is_authenticated(request):
        return RedirectResponse("/login")
    logger.info(f"Viewing module: {namespace}/{name}/{provider} version={version}")
    readme = await github_service.get_readme(namespace, name, provider, version)
    details = await github_service.get_repo_details(namespace, name, provider)
    versions_data = await github_service.get_versions(namespace, name, provider)
    examples = await github_service.get_examples(namespace, name, provider, version)
    
    # Breadcrumbs Calculation
    module_path = await github_service.get_module_path(namespace, name, provider)
    
    # Fetch description from README
    repo_name = details.get("name") if details else None
    if repo_name:
         # Use raw module_path for resolution
         desc = await github_service.get_readme_snippet(repo_name, module_path)
         if desc and details:
             details["description"] = desc

    # Breadcrumbs Hierarchy
    group_slug = "General"
    parent_slug = "Root"
    group = "General" 
    parent = "Root"

    if module_path:
        parts = module_path.strip("/").split("/")
        
        if len(parts) >= 3:
            group_slug = parts[0]
            parent_slug = "/".join(parts[1:-1])
        elif len(parts) == 2:
            group_slug = parts[0]
            parent_slug = "General"
            
        group = group_slug.replace("_", " ").title()
        parent = parent_slug.replace("_", " ").title()

    versions = []
    if versions_data and "modules" in versions_data and versions_data["modules"]:
        versions = versions_data["modules"][0]["versions"]

    return templates.TemplateResponse("module.html", {
        **get_common_context(request),
        "namespace": namespace,
        "name": name, 
        "provider": provider,
        "readme": readme,
        "details": details,
        "versions": versions,
        "current_version": version,
        "examples": examples,
        "group": group,
        "group_slug": group_slug,
        "parent": parent,
        "parent_slug": parent_slug
    })
