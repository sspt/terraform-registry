from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    github_token: str = ""
    github_api_base: str = "https://api.github.com"
    app_host: str = "http://localhost"
    
    # Optional: restricts registry to a specific GitHub user/org if set
    # If empty, tries to find any public repo matching namespace/name
    target_org: str = "" 
    
    # Monorepo settings
    # If set, treats the registry as a proxy for a single repository containing multiple modules
    monorepo_owner: str = ""
    monorepo_name: str = ""
    # monorepo_path_prefix removed (hardcoded to 'modules')


    # Authentication
    auth_api_key: str = "" # For Terraform CLI (Bearer token)
    
    # GitHub OAuth (UI)
    github_client_id: str = ""
    github_client_secret: str = ""
    secret_key: str = "change-me-in-production" # For session signing

    class Config:
        env_file = ".env"

settings = Settings()
