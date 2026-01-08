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

1. **Build the Docker image**:

   ```bash
   docker build -t terraform-registry .
   ```

2. **Run the container**:

   ```bash
   # Create a .env file or pass variables directly
   docker run -d -p 8000:8000 \
     -e GITHUB_TOKEN=your_github_pat \
     --name registry \
     terraform-registry
   ```

## Configuration

| Environment Variable | Description |
|----------------------|-------------|
| `GITHUB_TOKEN`       | GitHub PAT (required for private repos, recommended for public) |
| `TARGET_ORG`         | (Optional) Limit search to a specific GitHub user/org |
| `MONOREPO_OWNER`     | (Optional) Owner of the monorepo (e.g. `CompanyName`) |
| `MONOREPO_NAME`      | (Optional) Name of the monorepo (e.g. `terraform-modules`) |
| `MONOREPO_PATH_PREFIX`| (Optional) Path to modules inside monorepo (e.g. `modules`). Default is root. |

### Monorepo Mode

If you store all modules in a single repository (e.g. `CompanyName/terraform-modules`), configure the `MONOREPO_*` variables.
- Modules will be served from subdirectories.
- Tags on the repo (e.g. `v1.0.0`) will be available as versions for all modules.
- The UI will list directories in the repo instead of searching for multiple repos.

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

## How it works

1. **Discovery**: `/.well-known/terraform.json` points Terraform to the API.
2. **Resolution**: `source = "host/ns/name/provider"` translates to a lookup.
   - The service looks for a GitHub repo named `{name}` or `terraform-{provider}-{name}` in key `{ns}`.
3. **Download**: Returns a `X-Terraform-Get` header pointing to the GitHub archive zip for the specific tag/version.

