# Terraform Registry Proxy for GitHub

This is a Dockerized Terraform Module Registry that proxies modules from GitHub.
It allows you to browse GitHub repositories as Terraform modules and use them in your Terraform configuration with a private registry syntax.

## Features

- **Terraform Registry API**: Implements the Service Discovery and Module Registry protocols.
- **GitHub Backend**: Automatically fetches versions and download URLs from GitHub Releases/Tags.
- **UI**: Browsing interface to search modules and view README documentation.
- **Direct Mapping**: Maps `namespace/name/provider` to GitHub repositories.

## Prerequisites

- Docker
- GitHub Personal Access Token (for higher rate limits and private repo access)

## Setup

1. **Build and Run the Docker image**:

   ```bash
   ./build-and-run
   ```

## Configuration

| Environment Variable | Description |
|----------------------|-------------|
| `GITHUB_TOKEN`       | GitHub PAT (required for private repos, recommended for public) |
| `TARGET_ORG`         | Where the app lookup for modules (repos must match the regex: (.*)terraform-(.*)-modules ) |
| `GITHUB_CLIENT_ID`     | (Optional) GitHub app Client ID (For authorization) |
| `GITHUB_CLIENT_SECRET`      | (Optional) GitHub app Client Secret (For authorization) |
| `AUTH_API_KEY`| API Key to use for terraform login |


## Usage

### Web UI
Open [http://localhost:8000](http://localhost:8000) to browse and search for modules.

### Terraform

To use a module hosted in this registry:

1. Find the module in the UI.
2. Copy the usage snippet.

Example `main.tf`:

```hcl
module "my_vpc" {
  source  = "localhost:8000/my-org/vpc/aws"
  version = "1.0.0"
}
```

Note: Terraform requires HTTPS for non-localhost registries. For production, put this behind an Nginx/LoadBalancer with SSL.


