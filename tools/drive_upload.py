"""
Google Drive upload utility using service account authentication.

Handles upsert (update if exists, create otherwise) for HTML reports.
Uses supportsAllDrives for access to shared drives.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def _find_or_create_folder(svc, name: str, parent_id: str) -> str:
    """Return the Drive folder ID for `name` inside `parent_id`, creating if absent."""
    q = (f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
         f"and '{parent_id}' in parents and trashed = false")
    res = svc.files().list(
        q=q,
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()

    if res.get("files"):
        return res["files"][0]["id"]

    folder = svc.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        },
        fields="id",
        supportsAllDrives=True,
    ).execute()
    print(f"  Created Drive folder '{name}' (ID: {folder['id']})")
    return folder["id"]


def upload_to_drive(
    service_account_json_path: str,
    root_folder_id: str,
    subfolder_name: str,
    filename: str,
    file_path: str,
) -> str:
    """
    Upload or update an HTML file in a Google Drive folder.

    Creates subfolder if needed, then upserts the file (update if exists, create otherwise).

    Args:
        service_account_json_path: Path to service account JSON
        root_folder_id: Root Drive folder ID (must be shared with service account)
        subfolder_name: Subfolder name under root (e.g., "Publisher Billing")
        filename: Target filename (e.g., "publisher_billing_report.html")
        file_path: Local path to HTML file

    Returns:
        Google Drive file URL
    """
    # Load credentials
    sa_path = Path(service_account_json_path)
    if not sa_path.exists():
        raise FileNotFoundError(f"Service account JSON not found: {sa_path}")

    creds = service_account.Credentials.from_service_account_file(
        str(sa_path),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    # Create/find subfolder
    folder_id = _find_or_create_folder(svc, subfolder_name, root_folder_id)
    print(f"  Drive subfolder ready (ID: {folder_id})")

    # Prepare file
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Local file not found: {file_path}")

    media = MediaFileUpload(str(file_path), mimetype="text/html", resumable=False)

    # Check if file exists
    q_file = (
        f"name = '{filename}' and '{folder_id}' in parents "
        f"and mimeType = 'text/html' and trashed = false"
    )
    res = svc.files().list(
        q=q_file,
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()

    # Upsert
    if res.get("files"):
        # Update existing file
        file_id = res["files"][0]["id"]
        svc.files().update(
            fileId=file_id,
            media_body=media,
            supportsAllDrives=True,
        ).execute()
        print(f"  Updated existing file (ID: {file_id})")
    else:
        # Create new file
        f = svc.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        file_id = f["id"]
        print(f"  Created new file (ID: {file_id})")

    file_url = f"https://drive.google.com/file/d/{file_id}/view"
    print(f"  {file_url}")
    return file_url
