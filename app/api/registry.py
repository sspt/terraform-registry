from fastapi import APIRouter, HTTPException, Response, Request, Depends
from app.config import settings
from fastapi.responses import StreamingResponse
from app.services.github_service import github_service
from app.dependencies import verify_api_key
import io

router = APIRouter(dependencies=[Depends(verify_api_key)])

@router.get("/{namespace}/{name}/{provider}/versions")
async def list_versions(namespace: str, name: str, provider: str):
    versions = await github_service.get_versions(namespace, name, provider)
    if not versions:
        raise HTTPException(status_code=404, detail="Module not found")
    return versions

@router.api_route("/{namespace}/{name}/{provider}/{version}/source.zip", methods=["GET", "HEAD"])
async def download_source(namespace: str, name: str, provider: str, version: str):
    zip_bytes = await github_service.get_module_source_zip(namespace, name, provider, version)
    if not zip_bytes:
        raise HTTPException(status_code=404, detail="Module source not found")
        
    return StreamingResponse(
        io.BytesIO(zip_bytes), 
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={name}-{provider}-{version}.zip"}
    )

@router.get("/{namespace}/{name}/{provider}/{version}/download")
async def download_version(request: Request, namespace: str, name: str, provider: str, version: str):
    # Verify existence first (optional but good practice)
    # calls get_versions or similar? 
    # For performance, we might skip, but let's check basic repo existence via get_download_url
    check_url = await github_service.get_download_url(namespace, name, provider, version)
    if not check_url:
        raise HTTPException(status_code=404, detail="Module version not found")
    
    # Terraform Registry Protocol expects 204 No Content with X-Terraform-Get header
    # We point to our local source endpoint which serves the filtered zip
    # IMPORTANT: We need to preserve the API Key authentication for the download link.
    # Since Terraform does NOT automatically forward the Bearer token to the X-Terraform-Get URL if it's on a different path/host or if it treats it as a simple redirect.
    # However, for same-host refs, it usually works. 
    # The error 401 on the GET indicates the token might be missing or lost.
    
    # To fix this robustness, we can embed the token in the query param if it's safe (internal proxy), or rely on header.
    # We will append the token as a query parameter to ensure the download link works even if the client drops the header.
    source_url = str(request.url_for("download_source", namespace=namespace, name=name, provider=provider, version=version))
    
    # Append auth token if configured
    if settings.effective_api_key:
        sep = "&" if "?" in source_url else "?"
        source_url += f"{sep}token={settings.effective_api_key}"

    return Response(status_code=204, headers={"X-Terraform-Get": source_url})
