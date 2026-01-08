from fastapi import APIRouter, HTTPException, Response, Request, Depends
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

@router.get("/{namespace}/{name}/{provider}/{version}/source.zip")
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
    source_url = str(request.url_for("download_source", namespace=namespace, name=name, provider=provider, version=version))
    return Response(status_code=204, headers={"X-Terraform-Get": source_url})
