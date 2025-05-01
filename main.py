import json
import os
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import requests
from pydantic import BaseModel
from urllib.parse import urlparse, urlunparse, unquote
import logging
import uuid

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# Config file path
CONFIG_FILE = "registrainer.json"

# Session storage for passwords (in-memory, per session)
session_passwords: Dict[str, str] = {}

# Models
class Registry(BaseModel):
    uuid: str
    url: str
    username: Optional[str] = None
    insecure: bool = False

class DeleteRequest(BaseModel):
    repository: str
    tag: str

# Normalize URL to ensure scheme is present
def normalize_url(url: str) -> str:
    decoded_url = unquote(url)
    parsed = urlparse(decoded_url)
    if not parsed.scheme:
        parsed = urlparse(f"http://{decoded_url}")
    normalized = urlunparse(parsed).rstrip('/')
    logger.debug(f"Normalized URL: {url} -> {normalized}")
    return normalized

# Load config
def load_config() -> List[Registry]:
    if not os.path.exists(CONFIG_FILE):
        logger.warning(f"Config file {CONFIG_FILE} not found, returning empty list")
        return []
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        registries = []
        for item in data:
            item['url'] = normalize_url(item['url'])
            # Assign UUID if missing (for migration)
            if 'uuid' not in item or not item['uuid']:
                item['uuid'] = str(uuid.uuid4())
            registries.append(Registry(**item))
        # Save updated config with UUIDs
        save_config(registries)
        logger.debug(f"Loaded registries: {[f'{r.uuid}: {r.url}' for r in registries]}")
        return registries
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file {CONFIG_FILE}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Error loading config: {e}", exc_info=True)
        return []

# Save config
def save_config(registries: List[Registry]):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump([r.dict() for r in registries], f, indent=2)
        logger.debug(f"Saved config with registries: {[f'{r.uuid}: {r.url}' for r in registries]}")
    except Exception as e:
        logger.error(f"Error saving config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save config")

# Get registry auth headers
def get_auth_headers(registry: Registry, password: Optional[str] = None) -> Dict:
    if registry.username and password:
        return {"Authorization": f"Basic {requests.auth._basic_auth_str(registry.username, password)}"}
    return {}

# API Endpoints
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open("static/index.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/registries", response_model=List[Registry])
async def get_registries():
    registries = load_config()
    logger.debug(f"Returning registries: {[f'{r.uuid}: {r.url}' for r in registries]}")
    return registries

@app.post("/registries")
async def add_registry(registry: Registry):
    registries = load_config()
    normalized_url = normalize_url(registry.url)
    registry.url = normalized_url
    # Generate UUID for new registry
    registry.uuid = str(uuid.uuid4())
    if any(r.url == normalized_url for r in registries):
        raise HTTPException(status_code=400, detail="Registry already exists")
    registries.append(registry)
    save_config(registries)
    logger.info(f"Added registry: {registry.uuid}: {normalized_url}")
    return {"message": "Registry added"}

@app.delete("/registries/{uuid}")
async def remove_registry(uuid: str):
    logger.debug(f"Attempting to remove registry with UUID: {uuid}")
    registries = load_config()
    if not any(r.uuid == uuid for r in registries):
        logger.error(f"Registry with UUID {uuid} not found")
        raise HTTPException(status_code=404, detail=f"Registry with UUID {uuid} not found in configuration")
    registry = next(r for r in registries if r.uuid == uuid)
    registries = [r for r in registries if r.uuid != uuid]
    save_config(registries)
    logger.info(f"Successfully removed registry: {uuid}: {registry.url}")
    return {"message": f"Registry {registry.url} removed"}

@app.post("/auth/{registry_url}")
async def authenticate(registry_url: str, request: Request):
    data = await request.json()
    password = data.get("password")
    registries = load_config()
    normalized_url = normalize_url(registry_url)
    registry = next((r for r in registries if r.url == normalized_url), None)
    if not registry:
        logger.error(f"Registry not found for URL: {normalized_url}")
        raise HTTPException(status_code=404, detail=f"Registry not found: {normalized_url}")
    
    try:
        url = f"{registry.url}/v2/_catalog"
        headers = get_auth_headers(registry, password)
        response = requests.get(url, headers=headers, verify=not registry.insecure, timeout=10)
        response.raise_for_status()
        session_passwords[registry_url] = password
        logger.debug(f"Authentication successful for {registry_url}")
        return {"message": "Authentication successful"}
    except requests.Timeout:
        logger.error(f"Timeout connecting to {registry_url}")
        raise HTTPException(status_code=504, detail=f"Registry connection timed out: {registry_url}")
    except requests.ConnectionError as e:
        logger.error(f"Connection error for {registry_url}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to connect to registry {registry_url}: {str(e)}")
    except requests.HTTPError as e:
        logger.error(f"HTTP error for {registry_url}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=401, detail=f"Authentication failed for {registry_url}: {str(e)}")
    except requests.RequestException as e:
        logger.error(f"Request error for {registry_url}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to authenticate with {registry_url}: {str(e)}")

@app.get("/repositories/{registry_url:path}")
async def list_repositories(registry_url: str):
    registries = load_config()
    normalized_url = normalize_url(registry_url)
    logger.debug(f"Attempting to match normalized URL: {normalized_url}")
    logger.debug(f"Available registries: {[f'{r.uuid}: {r.url}' for r in registries]}")
    registry = next((r for r in registries if r.url == normalized_url), None)
    if not registry:
        logger.error(f"Registry not found for URL: {normalized_url}")
        raise HTTPException(status_code=404, detail=f"Registry not found: {normalized_url}")
    
    password = session_passwords.get(registry_url)
    try:
        url = f"{registry.url}/v2/_catalog"
        headers = get_auth_headers(registry, password)
        response = requests.get(url, headers=headers, verify=not registry.insecure, timeout=10)
        response.raise_for_status()
        logger.debug(f"Successfully fetched repositories from {registry_url}")
        return response.json()
    except requests.Timeout:
        logger.error(f"Timeout fetching repositories from {registry_url}")
        raise HTTPException(status_code=504, detail=f"Registry request timed out: {registry_url}")
    except requests.ConnectionError as e:
        logger.error(f"Connection error fetching repositories from {registry_url}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to connect to registry {registry_url}: {str(e)}")
    except requests.HTTPError as e:
        logger.error(f"HTTP error fetching repositories from {registry_url}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=response.status_code, detail=f"Registry error for {registry_url}: {str(e)}")
    except requests.RequestException as e:
        logger.error(f"Request error fetching repositories from {registry_url}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list repositories for {registry_url}: {str(e)}")

@app.get("/tags/{registry_url:path}/{repository:path}")
async def list_tags(registry_url: str, repository: str):
    registries = load_config()
    normalized_url = normalize_url(registry_url)
    registry = next((r for r in registries if r.url.rstrip('/') == normalized_url.rstrip('/')), None)
    if not registry:
        logger.error(f"Registry not found for URL: {normalized_url}")
        raise HTTPException(status_code=404, detail=f"Registry not found: {normalized_url}")
    
    password = session_passwords.get(registry_url)
    try:
        url = f"{registry.url}/v2/{repository}/tags/list"
        headers = get_auth_headers(registry, password)
        response = requests.get(url, headers=headers, verify=not registry.insecure, timeout=10)
        response.raise_for_status()
        tags = response.json().get("tags", [])
        
        # Fetch SHA for each tag, include invalid tags
        tag_details = []
        invalid_tags = []
        has_valid_tags = False
        for tag in tags:
            manifest_url = f"{registry.url}/v2/{repository}/manifests/{tag}"
            headers["Accept"] = "application/vnd.docker.distribution.manifest.v2+json"
            try:
                manifest_response = requests.get(manifest_url, headers=headers, verify=not registry.insecure, timeout=10)
                manifest_response.raise_for_status()
                digest = manifest_response.headers.get("Docker-Content-Digest", "N/A")
                status = "Valid"
                can_delete = False
                has_valid_tags = True
            except requests.HTTPError as e:
                if e.response.status_code == 404:
                    logger.error(f"Invalid tag detected: {registry_url}/{repository}:{tag} has no manifest")
                    digest = "Manifest missing"
                    status = "Invalid"
                    can_delete = True
                    invalid_tags.append(tag)
                else:
                    raise
            tag_details.append({
                "tag": tag,
                "sha": digest,
                "status": status,
                "can_delete": can_delete
            })
        logger.debug(f"Successfully fetched tags for {registry_url}/{repository}: {len(tag_details)} tags")
        response_data = {
            "repository": repository,
            "tags": tag_details,
            "has_valid_tags": has_valid_tags
        }
        if invalid_tags:
            response_data["warnings"] = f"Stale tags found for {repository}: {', '.join(invalid_tags)}. Delete stale tags or re-push missing images to the registry."
        if not tag_details:
            logger.warning(f"No tags found for {registry_url}/{repository}")
        return response_data
    except requests.Timeout:
        logger.error(f"Timeout fetching tags from {registry_url}/{repository}")
        raise HTTPException(status_code=504, detail=f"Tag request timed out: {registry_url}/{repository}")
    except requests.ConnectionError as e:
        logger.error(f"Connection error fetching tags from {registry_url}/{repository}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to connect to registry {registry_url}: {str(e)}")
    except requests.HTTPError as e:
        logger.error(f"HTTP error fetching tags from {registry_url}/{repository}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=response.status_code, detail=f"Registry error for {registry_url}/{repository}: {str(e)}")
    except requests.RequestException as e:
        logger.error(f"Request error fetching tags from {registry_url}/{repository}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list tags for {registry_url}/{repository}: {str(e)}")

@app.delete("/image/{registry_url:path}/{repository:path}")
async def delete_image(registry_url: str, repository: str, delete_request: DeleteRequest):
    registries = load_config()
    normalized_url = normalize_url(registry_url)
    registry = next((r for r in registries if r.url == normalized_url), None)
    if not registry:
        logger.error(f"Registry not found for URL: {normalized_url}")
        raise HTTPException(status_code=404, detail=f"Registry not found: {normalized_url}")
    
    password = session_passwords.get(registry_url)
    try:
        # Get the digest for the tag
        manifest_url = f"{registry.url}/v2/{repository}/manifests/{delete_request.tag}"
        headers = get_auth_headers(registry, password)
        headers["Accept"] = "application/vnd.docker.distribution.manifest.v2+json"
        manifest_response = requests.get(manifest_url, headers=headers, verify=not registry.insecure, timeout=10)
        manifest_response.raise_for_status()
        digest = manifest_response.headers.get("Docker-Content-Digest")
        
        # Delete the image
        delete_url = f"{registry.url}/v2/{repository}/manifests/{digest}"
        delete_response = requests.delete(delete_url, headers=headers, verify=not registry.insecure, timeout=10)
        delete_response.raise_for_status()
        logger.debug(f"Successfully deleted image {registry_url}/{repository}:{delete_request.tag}")
        return {"message": f"Image {repository}:{delete_request.tag} deleted"}
    except requests.Timeout:
        logger.error(f"Timeout deleting image {registry_url}/{repository}:{delete_request.tag}")
        raise HTTPException(status_code=504, detail=f"Delete request timed out: {registry_url}/{repository}")
    except requests.ConnectionError as e:
        logger.error(f"Connection error deleting image {registry_url}/{repository}:{delete_request.tag}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to connect to registry {registry_url}: {str(e)}")
    except requests.HTTPError as e:
        logger.error(f"HTTP error deleting image {registry_url}/{repository}:{delete_request.tag}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=response.status_code, detail=f"Registry error for {registry_url}/{repository}: {str(e)}")
    except requests.RequestException as e:
        logger.error(f"Request error deleting image {registry_url}/{repository}:{delete_request.tag}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete image {registry_url}/{repository}: {str(e)}")

@app.delete("/delete-tag/{registry_url:path}/{repository:path}/{tag}")
async def delete_tag(registry_url: str, repository: str, tag: str):
    registries = load_config()
    normalized_url = normalize_url(registry_url)
    registry = next((r for r in registries if r.url == normalized_url), None)
    if not registry:
        logger.error(f"Registry not found for URL: {normalized_url}")
        raise HTTPException(status_code=404, detail=f"Registry not found: {normalized_url}")
    
    password = session_passwords.get(registry_url)
    try:
        # Check if the tag exists
        tags_url = f"{registry.url}/v2/{repository}/tags/list"
        headers = get_auth_headers(registry, password)
        tags_response = requests.get(tags_url, headers=headers, verify=not registry.insecure, timeout=10)
        tags_response.raise_for_status()
        tags = tags_response.json().get("tags", [])
        if tag not in tags:
            raise HTTPException(status_code=404, detail=f"Tag {tag} not found in {repository}")
        
        # Attempt to get the manifest to confirm it's missing
        manifest_url = f"{registry.url}/v2/{repository}/manifests/{tag}"
        headers["Accept"] = "application/vnd.docker.distribution.manifest.v2+json"
        manifest_response = requests.get(manifest_url, headers=headers, verify=not registry.insecure, timeout=10)
        if manifest_response.status_code == 404:
            logger.warning(f"Manifest missing for {registry_url}/{repository}:{tag}, cannot delete via standard API")
            raise HTTPException(status_code=501, detail=f"Stale tag deletion not supported by registry {registry_url}; run garbage collection manually")
        manifest_response.raise_for_status()
        digest = manifest_response.headers.get("Docker-Content-Digest")
        
        # Delete the tag by removing the manifest
        delete_url = f"{registry.url}/v2/{repository}/manifests/{digest}"
        delete_response = requests.delete(delete_url, headers=headers, verify=not registry.insecure, timeout=10)
        delete_response.raise_for_status()
        logger.debug(f"Successfully deleted tag {registry_url}/{repository}:{tag}")
        return {"message": f"Tag {repository}:{tag} deleted"}
    except requests.Timeout:
        logger.error(f"Timeout deleting tag {registry_url}/{repository}:{tag}")
        raise HTTPException(status_code=504, detail=f"Tag deletion timed out: {registry_url}/{repository}")
    except requests.ConnectionError as e:
        logger.error(f"Connection error deleting tag {registry_url}/{repository}:{tag}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to connect to registry {registry_url}: {str(e)}")
    except requests.HTTPError as e:
        logger.error(f"HTTP error deleting tag {registry_url}/{repository}:{tag}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=response.status_code, detail=f"Registry error for {registry_url}/{repository}: {str(e)}")
    except requests.RequestException as e:
        logger.error(f"Request error deleting tag {registry_url}/{repository}:{tag}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete tag {registry_url}/{repository}: {str(e)}")