import httpx
import base64
import logging
import re
import time
import io
import zipfile
import markdown
from app.config import settings

logger = logging.getLogger(__name__)

class GitHubService:
    def __init__(self):
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        if settings.github_token:
            self.headers["Authorization"] = f"token {settings.github_token}"
        
        # Simple In-Memory Cache for small items (files, snippets)
        self._cache = {}
        self._cache_ttl = 3600 # 1 hour default used for small lookups
        
        # Structured Registry Cache
        # Structure: Provider -> Groups -> Modules Folders (Parents) -> Modules -> Details
        self.structured_cache = {}

    def _get_from_cache(self, key):
        if key in self._cache:
            data, timestamp = self._cache[key]
            # Simple TTL check
            if time.time() - timestamp < self._cache_ttl:
                return data
            else:
                del self._cache[key]
        return None

    def _set_to_cache(self, key, data):
        self._cache[key] = (data, time.time())

    def clear_cache(self):
        logger.info("Clearing local cache")
        self._cache = {}
        self.structured_cache = {}

    def _get_owner(self):
        return settings.monorepo_owner or settings.target_org

    def is_monorepo(self):
        return bool(self._get_owner())

    # ... existing internal resolution methods ...

    async def _resolve_module_location(self, client: httpx.AsyncClient, namespace: str, name: str, provider: str):
        """
        Locates the repository and path for a given module.
        First checks the structured cache for an exact match.
        """
        # Optimized lookup using structured_cache if available
        if provider in self.structured_cache:
            # We need to flatten or search the tree. 
            # Since 'name' is unique per provider usually, we can iterate.
            # But navigating the tree is safer.
            p_node = self.structured_cache[provider]
            for g_slug, g_node in p_node["groups"].items():
                for pa_slug, pa_node in g_node["parents"].items():
                    if name in pa_node["modules"]:
                        mod = pa_node["modules"][name]
                        return mod["repo_name"], mod["path"]

        # Fallback to dynamic resolution if cache missed (or cold)
        cache_key = f"location:{namespace}:{name}:{provider}"
        cached = self._get_from_cache(cache_key)
        if cached: return cached

        if not self.is_monorepo():
             # Standard multi-repo: path is root
             repo = await self._get_repo_name(client, namespace, name, provider)
             self._set_to_cache(cache_key, (repo, ""))
             return repo, ""
             
        owner = self._get_owner()
        candidate_repos = []
        
        if settings.monorepo_name:
            candidate_repos.append(settings.monorepo_name)
        else:
             list_url = f"{settings.github_api_base}/orgs/{owner}/repos?per_page=100&type=all"
             resp = await client.get(list_url, headers=self.headers)
             if resp.status_code != 200:
                list_url = f"{settings.github_api_base}/users/{owner}/repos?per_page=100&type=owner"
                resp = await client.get(list_url, headers=self.headers)
             
             if resp.status_code == 200:
                pattern = re.compile(rf".*terraform-{provider}-modules$", re.IGNORECASE)
                for r in resp.json():
                    if pattern.match(r["name"]):
                        candidate_repos.append(r["name"])
        
        if not candidate_repos:
            candidate_repos.append(f"terraform-{provider}-modules")

        for repo_name in candidate_repos:
            repo_url = f"{settings.github_api_base}/repos/{owner}/{repo_name}"
            resp = await client.get(repo_url, headers=self.headers)
            if resp.status_code != 200: continue
            
            default_branch = resp.json().get("default_branch", "main")
            
            tree_url = f"{settings.github_api_base}/repos/{owner}/{repo_name}/git/trees/{default_branch}?recursive=1"
            resp = await client.get(tree_url, headers=self.headers)
            if resp.status_code != 200: continue
            
            tree = resp.json().get("tree", [])
            # Enforce root modules directory
            prefix = "modules"
            
            # Match Logic (File based)
            for item in tree:
                if item["type"] != "blob" or not item["path"].endswith(".tf"):
                    continue
                
                dirname = "/".join(item["path"].split("/")[:-1])
                if not dirname: continue
                if prefix and not dirname.startswith(prefix): continue
                
                if prefix:
                    rel_path = dirname[len(prefix):].lstrip("/")
                else:
                    rel_path = dirname
                
                if not rel_path: continue
                # Allow any subfolder structure inside 'modules/'
                
                candidate_name = rel_path.replace("/", "_").replace("-", "_")
                if candidate_name.startswith("modules_"): candidate_name = candidate_name[8:]
                elif candidate_name.startswith("module_"): candidate_name = candidate_name[7:]
                
                if candidate_name == name:
                    self._set_to_cache(cache_key, (repo_name, rel_path))
                    return repo_name, rel_path

            # Match Logic (Directory fallback)
            dirs = [i["path"] for i in tree if i["type"] == "tree"]
            for d in dirs:
                check_path = d
                if prefix and not check_path.startswith(prefix): continue

                if prefix: rel = check_path[len(prefix):].lstrip("/")
                else: rel = check_path
                
                if not rel: continue

                cand = rel.replace("/", "_").replace("-", "_")
                if cand.startswith("modules_"): cand = cand[8:]
                elif cand.startswith("module_"): cand = cand[7:]
                
                if cand == name:
                    self._set_to_cache(cache_key, (repo_name, rel))
                    return repo_name, rel
                    
        self._set_to_cache(cache_key, (None, None))
        return None, None

    async def _get_repo_name(self, client: httpx.AsyncClient, namespace: str, name: str, provider: str) -> str:
        # Legacy/Internal wrapper for single repo resolution
        # Updated to use the smarter locator logic if monorepo
        if self.is_monorepo():
            repo, _ = await self._resolve_module_location(client, namespace, name, provider)
            return repo # might be None
            
        # Standard Multi-repo mode (Unchanged)
        # Try direct match
        repo_name = name
        url = f"{settings.github_api_base}/repos/{namespace}/{repo_name}"
        resp = await client.get(url, headers=self.headers)
        if resp.status_code == 200:
            return repo_name
        
        # Try standard terraform module naming convention
        repo_name = f"terraform-{provider}-{name}"
        url = f"{settings.github_api_base}/repos/{namespace}/{repo_name}"
        resp = await client.get(url, headers=self.headers)
        if resp.status_code == 200:
            return repo_name
            
        return None
    
    async def _resolve_monorepo_path(self, client: httpx.AsyncClient, module_name: str, provider: str = None) -> str:
        """
        Legacy wrapper: Use _resolve_module_location instead.
        """
        if not self.is_monorepo():
            return module_name

        _, path = await self._resolve_module_location(client, self._get_owner(), module_name, provider)
        return path if path else module_name
    
    async def get_providers(self):
        """
        Returns a list of available providers by scanning repositories.
        """
        cache_key = "providers"
        cached = self._get_from_cache(cache_key)
        if cached: return cached

        async with httpx.AsyncClient() as client:
            if self.is_monorepo():
                # Efficiently discover providers from repo names
                owner = self._get_owner()
                target_repos = []

                if settings.monorepo_name:
                    p = "aws"
                    parts = settings.monorepo_name.split("-")
                    if len(parts) >= 3 and parts[0] == "terraform" and parts[-1] == "modules":
                        p = parts[1]
                    result = [{"name": p, "repos": [settings.monorepo_name]}]
                    self._set_to_cache(cache_key, result)
                    return result
                
                # Discovery Mode
                all_repos = []
                page = 1
                while True:
                    # Attempt to fetch Org repos first
                    url = f"{settings.github_api_base}/orgs/{owner}/repos?per_page=100&type=all&page={page}"
                    resp = await client.get(url, headers=self.headers)
                    
                    if resp.status_code != 200:
                        # Fallback to User repos if Org fails (likely 404 if owner is a user)
                        url = f"{settings.github_api_base}/users/{owner}/repos?per_page=100&type=owner&page={page}"
                        resp = await client.get(url, headers=self.headers)
                    
                    if resp.status_code != 200:
                        logger.error(f"Failed to fetch repositories for {owner}: {resp.status_code}")
                        break
                        
                    data = resp.json()
                    if not data:
                        break
                        
                    all_repos.extend(data)
                    page += 1
                    
                    # Safety break to prevent infinite loops in weird scenarios (e.g. 100 pages = 10k repos)
                    if page > 100: 
                        break

                providers = {}
                if all_repos:
                    pattern = re.compile(r".*terraform-(.+)-modules$", re.IGNORECASE)
                    
                    for r in all_repos:
                        rname = r["name"]
                        if "terraform" not in rname.lower() or "modules" not in rname.lower():
                            continue

                        match = pattern.match(rname)
                        if match:
                            p = match.group(1).lower()
                            if p not in providers:
                                providers[p] = []
                            providers[p].append(rname)
                
                result = [{"name": p, "repos": repos} for p, repos in providers.items()]
                self._set_to_cache(cache_key, result)
                return result
            else:
                 # Standard mode - hard to guess without full search, return empty or common
                 return []

    async def get_modules_for_provider(self, provider: str, enrich: bool = False):
        """
        Scans repositories for a specific provider and returns modules from the structured cache.
        If cache is empty/cold, triggers a scan.
        """
        if provider in self.structured_cache:
            # Flatten the structured cache into a list for compatibility
            modules_list = []
            p_node = self.structured_cache[provider]
            for g_slug, g_node in p_node["groups"].items():
                for pa_slug, pa_node in g_node["parents"].items():
                     for m_name, m_data in pa_node["modules"].items():
                         modules_list.append(m_data)
            return sorted(modules_list, key=lambda x: x["name"])

        # Fallback to cold scan (similar to warmup but just for this provider)
        logger.info(f"Structured Cache MISS for provider '{provider}'. Triggering scan...")
        
        # 1. Get Repos for Provider
        providers_list = await self.get_providers()
        target_repos = []
        for p in providers_list:
            if p["name"] == provider:
                target_repos = p["repos"]
                break
        
        if not target_repos:
            return []

        all_modules = []
        owner = self._get_owner()
        
        # Initialize Structure
        if provider not in self.structured_cache:
            self.structured_cache[provider] = {"groups": {}}

        async with httpx.AsyncClient() as client:
            repo_version_map = {} # Cache versions per repo

            for repo_name in target_repos:
                # 1. Get default branch
                repo_url = f"{settings.github_api_base}/repos/{owner}/{repo_name}"
                resp = await client.get(repo_url, headers=self.headers)
                if resp.status_code != 200: continue
                default_branch = resp.json().get("default_branch", "main")

                # 2. Get Tree recursively
                tree_url = f"{settings.github_api_base}/repos/{owner}/{repo_name}/git/trees/{default_branch}?recursive=1"
                resp = await client.get(tree_url, headers=self.headers)
                if resp.status_code != 200: continue
                tree = resp.json().get("tree", [])
                
                # Pre-fetch versions if enriching (always try to enrich for structure)
                versions = []
                if repo_name not in repo_version_map:
                    versions = await self.get_repo_tags(repo_name)
                    repo_version_map[repo_name] = versions
                else:
                    versions = repo_version_map[repo_name]

                # 3. Find modules
                module_paths = set()
                prefix = "modules"

                for item in tree:
                    path = item["path"]
                    # Ensure path is effectively under 'modules/'
                    if not path.startswith(prefix + "/"): 
                        continue
                        
                    if item["type"] == "blob" and path.endswith(".tf"):
                        dirname = "/".join(path.split("/")[:-1])
                        if dirname: module_paths.add(dirname)

                for mpath in module_paths:
                    # Strip 'modules/' prefix to get the relative structure
                    rel_name = mpath[len(prefix):].lstrip("/")
                    
                    if not rel_name: continue
                    
                    path_parts = mpath.split("/")
                    if "examples" in path_parts or "fixtures" in path_parts or "tests" in path_parts: continue

                    # Naming & Hierarchy
                    # logic: patterns/abc/def -> group: Patterns, parent: abc, module: def
                    
                    r_parts = rel_name.split("/")
                    
                    group_slug = r_parts[0] # modules or patterns
                    group_name = group_slug.replace("_", " ").title()
                    
                    # Module Name Logic
                    display_name = rel_name.replace("/", "_").replace("-", "_")
                    if display_name.startswith("modules_"): display_name = display_name[8:]
                    elif display_name.startswith("module_"): display_name = display_name[7:]
                    
                    short_name = r_parts[-1].replace("-", "_")
                    
                    # Parent Folder Logic (The "Module Folder")
                    # If path is modules/security/firewall -> Parent is 'security', module is firewall
                    # If path is modules/vpc -> Parent is 'General' or 'vpc'? Usually 'modules/vpc' is the module.
                    # Let's say:
                    # modules/a -> Parent: General, Module: a
                    # modules/a/b -> Parent: a, Module: b
                    
                    parent_slug = "general"
                    parent_name = "General"
                    
                    if len(r_parts) > 2:
                        parent_slug = r_parts[1]
                        parent_name = parent_slug.replace("_", " ").title()
                    
                    subfolder = r_parts[-2] if len(r_parts) >= 2 else "root"
                    
                    # Description & README (enriched)
                    description = f"Module {display_name} ({provider})"
                    readme_full = None
                    
                    if enrich:
                        # Optimization: Fetch full README now since we are making a request anyway for the snippet
                        # or we want to cache everything as requested.
                        # We can reuse get_readme logic but adapted for the loop to avoid redundant cache checks/keys
                        
                        # Determine readme path
                        readme_path = f"{mpath}/README.md" if mpath else "README.md"
                        if readme_path.startswith("/"): readme_path = readme_path[1:]
                        
                        try:
                            # We use the lower-level API to get content directly to avoid complex recursion in get_readme
                            # But we want the HTML or Raw? User said "READMEs", usually implies text/markdown for rendering.
                            # get_readme uses 'application/vnd.github.v3.html' which is heavy. 
                            # Let's stick to RAW for storage size and client-side rendering or just snippet extraction.
                            # Actually, get_readme returns HTML. If we want that, we fetch HTML.
                            
                            # Let's call get_readme() directly? No, it might be slow if we do it sequentially.
                            # But we are already here.
                            
                            # Let's just fetch RAW to extract description, and maybe store RAW?
                            # Or fetch HTML? The UI renders HTML.
                            
                            # Decision: Fetch RAW for snippet extraction (lightweight-ish). 
                            # If user wants full cached readmes for "fast UI", we might want HTML.
                            # However, 'get_readme_snippet' fetches RAW.
                            
                            # Let's do this: Fetch RAW. Store RAW.
                            # Use RAW to generate description.
                            # If UI needs HTML, we can render it or fetch HTML lazily? 
                            # The user said "Modules -> READMEs".
                            # I will store the RAW content.
                            
                            url_readme = f"{settings.github_api_base}/repos/{owner}/{repo_name}/contents/{readme_path}"
                            headers_readme = self.headers.copy()
                            headers_readme["Accept"] = "application/vnd.github.v3.raw"
                            
                            resp_readme = await client.get(url_readme, headers=headers_readme)
                            if resp_readme.status_code == 200:
                                readme_full = resp_readme.text
                                
                                # Extract snippet from full text
                                lines = readme_full.split("\n")
                                filtered_lines = []
                                count = 0
                                for line in lines:
                                    l = line.strip()
                                    l = re.sub(r'<!--.*?-->', '', l).strip()
                                    if not l: continue
                                    if l.startswith("[!"): continue
                                    if l.startswith("[!["): continue
                                    if l.startswith("#"): continue
                                    if l.startswith("="): continue
                                    filtered_lines.append(l)
                                    count += 1
                                    if count >= 1: break
                                if filtered_lines:
                                    description = filtered_lines[0]
                        except Exception as e:
                            logger.warning(f"Failed to fetch readme for {display_name}: {e}")

                    mod_data = {
                        "namespace": owner,
                        "name": display_name, 
                        "short_name": short_name,
                        "parsed_name": display_name,
                        "group": group_name,
                        "group_slug": group_slug,
                        "parent_dir": parent_name,
                        "parent_slug": parent_slug,
                        "subfolder": subfolder,
                        "provider": provider,
                        "parsed_provider": provider,
                        "repo_name": repo_name,
                        "path": rel_name, # Store relative path (stripped of 'modules/')
                        "description": description,
                        "versions": versions,
                        "stars": 0, 
                        "url": f"https://github.com/{owner}/{repo_name}/tree/{default_branch}/{mpath}",
                        "readme_content": readme_full # Caching the content
                    }
                    
                    all_modules.append(mod_data)

                    # Update Structured Cache
                    p_cache = self.structured_cache[provider]
                    if group_slug not in p_cache["groups"]:
                        p_cache["groups"][group_slug] = {"name": group_name, "parents": {}}
                    
                    g_cache = p_cache["groups"][group_slug]
                    if parent_slug not in g_cache["parents"]:
                        g_cache["parents"][parent_slug] = {"name": parent_name, "modules": {}}
                        
                    g_cache["parents"][parent_slug]["modules"][display_name] = mod_data

        return sorted(all_modules, key=lambda x: x["name"])

    async def search_modules(self, query: str, provider_filter: str = None):
        """
        Refactored module search:
        1. If Monorepo: Get ALL modules from structured cache (flattened).
        2. If Standard: Use GitHub Search API.
        """
        logger.info(f"Searching modules: query='{query}' provider='{provider_filter}'")

        if self.is_monorepo():
            all_results = []
            
            # Determine which providers to scan
            target_providers = []
            if provider_filter:
                target_providers = [provider_filter]
            else:
                # If cache is warm, use keys from structured cache
                if self.structured_cache:
                    target_providers = list(self.structured_cache.keys())
                else:
                    # Cold start scenario
                    p_list = await self.get_providers()
                    target_providers = [p["name"] for p in p_list]
            
            for p_name in target_providers:
                # This will use the structured cache or fill it if empty
                modules = await self.get_modules_for_provider(p_name, enrich=True)
                all_results.extend(modules)
            
            # Filter in memory
            final_list = []
            q_lower = query.lower() if query else ""
            
            for m in all_results:
                if q_lower and q_lower not in m["name"].lower():
                    continue
                final_list.append(m)
            
            return final_list

        else:
            # Standard Multi-repo Search (Legacy/Fallback)
            cache_key = f"search:{query}:{provider_filter}"
            cached = self._get_from_cache(cache_key)
            if cached: return cached
            
            # Multi-repo search
            logger.info("Performing standard multi-repo search")
            q = f"{query} topic:terraform" if query else "topic:terraform"
            if settings.target_org:
                q += f" user:{settings.target_org}"
                
            url = f"{settings.github_api_base}/search/repositories?q={q}&sort=stars&order=desc"
            # async with httpx.AsyncClient() as client: # Fixed missing client context
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code != 200:
                    return []
                
                items = resp.json().get("items", [])
                modules = []
                for item in items:
                    modules.append({
                        "namespace": item["owner"]["login"],
                        "name": item["name"],
                        "short_name": item["name"],
                        "group": "General",
                        "group_slug": "General",
                        "parent_dir": "Root",
                        "parent_slug": "Root",
                        "subfolder": "root",
                        "provider": "unknown", 
                        "repo_name": item["name"], 
                        "path": "",
                        "description": item["description"],
                        "stars": item["stargazers_count"],
                        "url": item["html_url"]
                    })
                self._set_to_cache(cache_key, modules)
                return modules

    async def get_versions_legacy(self, namespace: str, name: str, provider: str):
        # Renamed old method to legacy just in case
        pass 


    async def get_versions(self, namespace: str, name: str, provider: str):
        # 1. Check Structured Cache first
        if provider in self.structured_cache:
            p_node = self.structured_cache[provider]
            # Iterate to find module
            for g in p_node["groups"].values():
                for pa in g["parents"].values():
                    if name in pa["modules"]:
                        mod_data = pa["modules"][name]
                        if "versions" in mod_data and mod_data["versions"]:
                            return {"modules": [{"versions": [{"version": v} for v in mod_data["versions"]]}]}

        # 2. Fallback to Standard
        cache_key = f"versions:{namespace}:{name}:{provider}"
        cached = self._get_from_cache(cache_key)
        if cached: return cached

        async with httpx.AsyncClient() as client:
            repo_name = await self._get_repo_name(client, namespace, name, provider)
            if not repo_name:
                return None
            
            repo_owner = self._get_owner() if self.is_monorepo() else namespace

            # Get tags
            url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}/tags"
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                print(f"Error fetching tags: {resp.status_code}")
                # Fallback: if no tags, maybe return 0.0.0 or similar if it's main branch? 
                # Terraform requires versions. 
                return []
            
            tags = resp.json()
            versions = []
            for tag in tags:
                # Todo: Filtering tags for monorepo if they are scoped (e.g. module-v1.0.0)
                # For now assuming global tags
                v = tag["name"].lstrip("v")
                versions.append({"version": v})
            
            result = {"modules": [{"versions": versions}]}
            self._set_to_cache(cache_key, result)
            return result

    async def get_module_source_zip(self, namespace: str, name: str, provider: str, version: str) -> bytes:
        async with httpx.AsyncClient() as client:
            # 1. Resolve Location
            repo_name = None
            resolved_rel_path = ""
            if self.is_monorepo():
                 repo_name, resolved_rel_path = await self._resolve_module_location(client, namespace, name, provider)
            else:
                 repo_name = await self._get_repo_name(client, namespace, name, provider)
            
            if not repo_name:
                logger.warning(f"Could not resolve repo name for {namespace}/{name}/{provider}")
                return None
            
            repo_owner = self._get_owner() if self.is_monorepo() else namespace
            
            # 2. Download Zipball from GitHub
            # Try with 'v' prefix first
            url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}/zipball/v{version}"
            logger.info(f"Downloading source from {url}")
            
            response = await client.get(url, headers=self.headers, follow_redirects=True)
            
            if response.status_code != 200:
                # Fallback: Try without 'v' prefix
                logger.info(f"First attempt failed ({response.status_code}). Trying without 'v' prefix.")
                url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}/zipball/{version}"
                response = await client.get(url, headers=self.headers, follow_redirects=True)
                
                if response.status_code != 200:
                    logger.error(f"Failed to download zip from GitHub: {response.status_code}")
                    return None
            
            try:
                source_zip = zipfile.ZipFile(io.BytesIO(response.content))
            except zipfile.BadZipFile:
                logger.error("Received bad zip file from GitHub")
                return None
            
            # 3. Determine filtering path
            target_path = ""
            if self.is_monorepo():
                prefix = "modules"
                if resolved_rel_path:
                    target_path = f"{prefix}/{resolved_rel_path}"
                else:
                    target_path = prefix
                
                # Cleanup path
                target_path = target_path.strip("/")
            
            # 4. Create new Zip
            output_io = io.BytesIO()
            with zipfile.ZipFile(output_io, "w", zipfile.ZIP_DEFLATED) as out_zip:
                # GitHub zipball has a root folder: owner-repo-sha/
                if not source_zip.namelist():
                    logger.error("Downloaded zip is empty (no files).")
                    return None
                    
                root_dir = source_zip.namelist()[0].split("/")[0]
                
                search_prefix = f"{root_dir}/{target_path}" if target_path else root_dir
                if not search_prefix.endswith("/"):
                    search_prefix += "/"
                
                logger.info(f"Filtering zip content using prefix: {search_prefix}")
                
                found_files = False
                for file_info in source_zip.infolist():
                    if file_info.filename.startswith(search_prefix):
                        # Calculate new internal name
                        if file_info.filename == search_prefix: 
                            continue 
                            
                        # Remove the matched prefix to place files at root
                        rel_path = file_info.filename[len(search_prefix):]
                        
                        if not rel_path: continue
                        
                        final_data = source_zip.read(file_info)
                        out_zip.writestr(rel_path, final_data)
                        found_files = True
            
                if not found_files:
                    logger.warning(f"No files found matching prefix {search_prefix}. Available roots: {[n.split('/')[0] for n in source_zip.namelist()[:5]]}")
            
            output_io.seek(0)
            return output_io.read()

    async def get_download_url(self, namespace: str, name: str, provider: str, version: str):
        async with httpx.AsyncClient() as client:
            repo_name = None
            resolved_rel_path = ""
            
            if self.is_monorepo():
                 repo_name, resolved_rel_path = await self._resolve_module_location(client, namespace, name, provider)
            else:
                 repo_name = await self._get_repo_name(client, namespace, name, provider)
            
            if not repo_name:
                return None
            
            repo_owner = self._get_owner() if self.is_monorepo() else namespace
            
            download_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/zipball/v{version}"
            
            if self.is_monorepo() and resolved_rel_path:
                prefix = "modules"
                subdir = f"{prefix}/{resolved_rel_path}"
                download_url = f"{download_url}//{subdir}"

            return download_url

    async def get_module_path(self, namespace: str, name: str, provider: str) -> str:
        """
        Public method to resolve and return the physical path (subdir) of the module.
        Useful for UI breadcrumbs.
        """
        async with httpx.AsyncClient() as client:
            if self.is_monorepo():
                 _, path = await self._resolve_module_location(client, namespace, name, provider)
                 return path if path else ""
            return ""

    async def get_readme(self, namespace: str, name: str, provider: str, version: str = None):
        """
        Extended to cache READMEs inside the structured object if successful.
        """
        # 1. Try Memory (Structured)
        if provider in self.structured_cache:
            p_node = self.structured_cache[provider]
            # Reverse lookup 
            found_mod = None
            for g in p_node["groups"].values():
                for pa in g["parents"].values():
                    if name in pa["modules"]:
                        found_mod = pa["modules"][name]
                        break
                if found_mod: break
            
            if found_mod and "readme_content" in found_mod and found_mod["readme_content"]:
                 # We only cache the 'latest' readme in structured for now.
                 if not version:
                     # Convert stored markdown to HTML on the fly
                     raw_md = found_mod["readme_content"]
                     html = markdown.markdown(raw_md, extensions=['fenced_code', 'tables', 'nl2br'])
                     return html

        # 2. Try Standard Cache (Files/API responses)
        cache_key = f"readme:{namespace}:{name}:{provider}:{version}"
        cached = self._get_from_cache(cache_key)
        if cached: return cached
        
        async with httpx.AsyncClient() as client:
            repo_name = None
            resolved_rel_path = ""
            
            # Use improved resolver 
            if self.is_monorepo():
                 repo_name, resolved_rel_path = await self._resolve_module_location(client, namespace, name, provider)
            else:
                 repo_name = await self._get_repo_name(client, namespace, name, provider)
            
            if not repo_name:
                return None
            
            repo_owner = self._get_owner() if self.is_monorepo() else namespace
            
            path = ""
            if self.is_monorepo():
                # Resolve module name to path
                prefix = "modules"
                path = f"/{prefix}/{resolved_rel_path}"
                logger.info(f"Fetching README for {name} from path: {path}")

            # Try to get README HTML rendered by GitHub
            headers = self.headers.copy()
            headers["Accept"] = "application/vnd.github.v3.html"
            
            params = {}
            if version:
                params["ref"] = f"v{version}" if not version.startswith("v") else version
            
            content_text = None

            if path:
                # Monorepo logic ...
                # Try README.md 
                target_path = f"{path.strip('/')}/README.md"
                url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}/contents/{target_path}"
                
                resp = await client.get(url, headers=headers, params=params)
                
                if resp.status_code == 404:
                    # If not found, list directory
                    dir_url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}/contents/{path.strip('/')}"
                    # Use standard headers for directory listing (JSON)
                    dir_resp = await client.get(dir_url, headers=self.headers, params=params)
                    
                    if dir_resp.status_code == 200:
                        files = dir_resp.json()
                        if isinstance(files, list):
                            readme_candidates = [f for f in files if f["name"].lower().startswith("readme")]
                            readme_file = next((f for f in readme_candidates if f["name"].lower().endswith(".md")), None)
                            if not readme_file and readme_candidates:
                                readme_file = readme_candidates[0]
                                
                            if readme_file:
                                 url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}/contents/{readme_file['path']}"
                                 resp = await client.get(url, headers=headers, params=params)
                                 if resp.status_code == 200:
                                     content_text = resp.text
                elif resp.status_code == 200:
                     content_text = resp.text
            else:
                # Standard Root README API (auto-detects)
                url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}/readme"
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    content_text = resp.text
             
            if not content_text:
                 logger.warning(f"Failed to fetch README from {path}: {resp.status_code} if called. URL context: {path}")
                 if self.is_monorepo() and path:
                     self._set_to_cache(cache_key, None)
                     return None
                 
                 error_msg = f"Error fetching readme"
                 self._set_to_cache(cache_key, error_msg)
                 return error_msg
            
            # API returns HTML (because we asked for it via headers)
            self._set_to_cache(cache_key, content_text)

            # Store in structured cache? 
            # If we fetched HTML here, we can't easily store it in 'readme_content' which expects Raw for other logic?
            # Actually, we can store it, but next time we retrieve it we assume it is raw and try to convert it.
            # That would be bad (converting HTML to HTML via markdown parser).
            
            # Solution: We only populate structured cache during warmup (Raw). 
            # If we are here, it means we missed structured cache or version specific.
            # We don't update structured cache here to avoid inconsistent types.
            
            return content_text

    async def get_examples(self, namespace: str, name: str, provider: str, version: str = None):
        """
        Fetches the list of examples for a given module.
        Checks for an 'examples' directory in the module path.
        """
        cache_key = f"examples:{namespace}:{name}:{provider}:{version}"
        cached = self._get_from_cache(cache_key)
        if cached: return cached

        async with httpx.AsyncClient() as client:
            repo_name = None
            resolved_rel_path = ""
            
            if self.is_monorepo():
                 repo_name, resolved_rel_path = await self._resolve_module_location(client, namespace, name, provider)
            else:
                 repo_name = await self._get_repo_name(client, namespace, name, provider)
            
            if not repo_name:
                return []

            repo_owner = self._get_owner() if self.is_monorepo() else namespace
            
            # Determine path to module
            path = ""
            if self.is_monorepo():
                prefix = "modules"
                path = f"{prefix}/{resolved_rel_path}"
            
            # Look for examples folder
            examples_path = f"{path}/examples" if path else "examples"
            
            url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}/contents/{examples_path}"
            
            params = {}
            if version:
                params["ref"] = f"v{version}" if not version.startswith("v") else version

            resp = await client.get(url, headers=self.headers, params=params)
            if resp.status_code != 200:
                # No examples folder found
                self._set_to_cache(cache_key, [])
                return []
            
            items = resp.json()
            if not isinstance(items, list):
                self._set_to_cache(cache_key, [])
                return []
            
            examples = []
            for item in items:
                if item["type"] == "dir":
                    examples.append({
                        "name": item["name"],
                        "url": item["html_url"]
                    })
            
            self._set_to_cache(cache_key, examples)
            return examples

    async def get_repo_tags(self, repo_name: str):
        """
        Fetches tags for a specific repository.
        """
        cache_key = f"tags:{repo_name}"
        cached = self._get_from_cache(cache_key)
        if cached: return cached

        async with httpx.AsyncClient() as client:
            owner = self._get_owner()
            url = f"{settings.github_api_base}/repos/{owner}/{repo_name}/tags"
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch tags for {repo_name}: {resp.status_code}")
                return []
            
            tags = resp.json()
            versions = []
            for tag in tags:
                versions.append(tag["name"].lstrip("v"))
            
            self._set_to_cache(cache_key, versions)
            return versions

    async def get_readme_snippet(self, repo_name: str, path: str):
        """
        Fetches the first few lines of the README.md for a given module path.
        """
        cache_key = f"readme:{repo_name}:{path}"
        cached = self._get_from_cache(cache_key)
        if cached: return cached

        async with httpx.AsyncClient() as client:
            owner = self._get_owner()
            
            # Determine path to README
            readme_path = f"{path}/README.md" if path else "README.md"
            if path:
                # Remove leading slash if present
                if readme_path.startswith("/"): readme_path = readme_path[1:]

            url = f"{settings.github_api_base}/repos/{owner}/{repo_name}/contents/{readme_path}"
            headers = self.headers.copy()
            headers["Accept"] = "application/vnd.github.v3.raw"
            
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                # Try lowercase
                url = f"{settings.github_api_base}/repos/{owner}/{repo_name}/contents/{path}/readme.md" if path else f"{settings.github_api_base}/repos/{owner}/{repo_name}/contents/readme.md"
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return None
            
            content = resp.text
            lines = content.split("\n")
            filtered_lines = []
            count = 0
            for line in lines:
                l = line.strip()
                # Remove HTML comments
                l = re.sub(r'<!--.*?-->', '', l).strip()
                
                # Skip badges, empty lines, headers (maybe keep header text?)
                if not l: continue
                if l.startswith("[!"): continue # Badges often start with links/images
                if l.startswith("[!["): continue
                
                # If header, skip it entirely as we want non-header paragraph
                if l.startswith("#"):
                    continue
                
                if l.startswith("="):
                    continue

                filtered_lines.append(l)
                count += 1
                if count >= 1: # Just the first line/paragraph
                    break
            
            result = " ".join(filtered_lines)
            self._set_to_cache(cache_key, result)
            return result

    async def get_monorepo_tags(self):
        """
        Fetches tags for the configured monorepo.
        This allows us to populate version dropdowns efficiently primarily for monorepo setups.
        """
        if not self.is_monorepo() or not settings.monorepo_name:
            # If explicit monorepo_name is not set, we can't assume global tags apply.
            return []
            
        async with httpx.AsyncClient() as client:
            owner = self._get_owner()
            url = f"{settings.github_api_base}/repos/{owner}/{settings.monorepo_name}/tags"
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch monorepo tags: {resp.status_code}")
                return []
            
            tags = resp.json()
            versions = []
            for tag in tags:
                versions.append(tag["name"].lstrip("v"))
            return versions

    async def get_repo_details(self, namespace: str, name: str, provider: str):
        cache_key = f"repo_details:{namespace}:{name}:{provider}"
        cached = self._get_from_cache(cache_key)
        if cached: return cached

        async with httpx.AsyncClient() as client:
            repo_name = None
            if self.is_monorepo():
                 repo_name, _ = await self._resolve_module_location(client, namespace, name, provider)
            else:
                 repo_name = await self._get_repo_name(client, namespace, name, provider)

            if not repo_name:
                return None
            
            repo_owner = self._get_owner() if self.is_monorepo() else namespace

            url = f"{settings.github_api_base}/repos/{repo_owner}/{repo_name}"
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                self._set_to_cache(cache_key, None)
                return None
            
            self._set_to_cache(cache_key, resp.json())
            return resp.json()

    async def verify_org_access(self):
        """
        Verifies if the configured token has read access to the target organization.
        """
        owner = self._get_owner()
        if not owner:
            logger.warning("No TARGET_ORG or MONOREPO_OWNER configured. Skipping access check.")
            return

        async with httpx.AsyncClient() as client:
            # Check if it's an Organization
            url = f"{settings.github_api_base}/orgs/{owner}"
            resp = await client.get(url, headers=self.headers)
            
            if resp.status_code == 200:
                logger.info(f"Verified access to Organization '{owner}'.")
                
                # Check Rate Limit
                rate_url = f"{settings.github_api_base}/rate_limit"
                rate_resp = await client.get(rate_url, headers=self.headers)
                if rate_resp.status_code == 200:
                    limits = rate_resp.json().get("resources", {}).get("core", {})
                    logger.info(f"GitHub Rate Limit: {limits.get('remaining')}/{limits.get('limit')} remaining.")
                return

            # If not an Org, maybe it's a User?
            if resp.status_code == 404:
                 user_url = f"{settings.github_api_base}/users/{owner}"
                 user_resp = await client.get(user_url, headers=self.headers)
                 if user_resp.status_code == 200:
                     logger.info(f"Verified access to User '{owner}'.")
                     return
            
            # If we are here, access failed
            logger.critical(f"Failed to verify access to '{owner}'. GitHub returned {resp.status_code}.")
            if resp.status_code == 401:
                logger.critical("Authentication failed: Invalid GITHUB_TOKEN.")
            elif resp.status_code == 403:
                logger.critical("Access denied: Token may lack permissions (read:org) or SAML enforcement SSO.")
            elif resp.status_code == 404:
                logger.critical(f"Organization or User '{owner}' not found or token lacks visibility.")
            
            # Raise exception to ensure visibility
            raise Exception(f"GitHub Access Verification Failed for '{owner}'. Check GITHUB_TOKEN and TARGET_ORG.")

    async def warmup_cache(self):
        logger.info("Starting Cache Warmup...")
        try:
            # 0. Verify Access
            await self.verify_org_access()

            # 1. Fetch Providers
            providers = await self.get_providers()
            logger.info(f"Warmup: Found {len(providers)} providers.")
            
            # 2. Fetch Modules for each provider
            for p in providers:
                logger.info(f"Warmup: Fetching enriched modules for provider '{p['name']}'...")
                # Calling get_modules_for_provider explicitly with enrich=True ensures
                # that the cache entry created is the 'rich' one, which search_modules also uses.
                await self.get_modules_for_provider(p["name"], enrich=True)
                
            logger.info("Cache Warmup Completed Successfully.")
        except Exception as e:
            logger.error(f"Cache Warmup Failed: {e}")

github_service = GitHubService()
