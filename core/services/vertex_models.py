import json
import os
import urllib.request
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2 import service_account


class VertexModelError(Exception):
    pass


def _get_access_token():
    default_path = Path(__file__).resolve().parent.parent / "vertex-sa.json"
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and Path(creds_path).exists():
        resolved_path = Path(creds_path)
    else:
        resolved_path = default_path
    if not resolved_path.exists():
        return None
    creds = service_account.Credentials.from_service_account_file(
        str(resolved_path),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(Request())
    return creds.token


def list_models():
    token = _get_access_token()
    project = "ngd-africa"
    location = os.environ.get("VERTEX_LOCATION", "us-central1")
    host = os.environ.get("VERTEX_HOST", f"{location}-aiplatform.googleapis.com")
    if not token:
        raise VertexModelError("Vertex credentials not available.")
    if not project:
        raise VertexModelError("VERTEX_PROJECT is not set.")

    url = f"https://{host}/v1beta1/publishers/google/models"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise VertexModelError(f"HTTP {exc.code} for {url}: {body}") from exc
    except Exception as exc:
        raise VertexModelError(str(exc)) from exc

    return data
