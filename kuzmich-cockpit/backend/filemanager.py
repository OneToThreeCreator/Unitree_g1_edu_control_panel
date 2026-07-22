import shutil
import subprocess
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse
from typing import List

from .config import CONFIG

router = APIRouter(prefix="/api/files", tags=["files"])

BASE_DIR = Path(CONFIG.files_base_dir)

def safe_path(path: str) -> Path:
    """Resolve path relative to BASE_DIR, forbid escaping."""
    target = (BASE_DIR / path.lstrip("/")).resolve()
    try:
        target.relative_to(BASE_DIR)
    except ValueError:
        raise HTTPException(403, "Access denied")
    return target


# --- Specific routes first (before catch-all {path:path}) ---

@router.post("/upload")
async def upload_file(path: str = "", file: UploadFile = File(...)):
    target = safe_path(path)
    if not target.is_dir():
        raise HTTPException(400, "Target is not a directory")
    dest = target / file.filename
    with dest.open("wb") as f:
        content = await file.read()
        f.write(content)
    return {"status": "ok", "saved": str(dest.relative_to(BASE_DIR))}

@router.put("/{path:path}/mkdir")
async def make_dir(path: str, data: dict):
    name = data.get("name", "")
    if not name:
        raise HTTPException(400, "Отсутствует 'name'")
    target = safe_path(path)
    if not target.is_dir():
        raise HTTPException(400, "Parent is not a directory")
    new_dir = target / name
    new_dir.mkdir(exist_ok=False)
    return {"status": "ok"}

@router.post("/run")
async def run_script(path: str = Body(...), args: List[str] = Body(default=[])):
    target = safe_path(path)
    if not target.is_file() or target.suffix != ".py":
        raise HTTPException(400, "Only .py files can be executed")
    try:
        process = subprocess.run(
            ["python", str(target)] + args,
            capture_output=True,
            text=True,
            cwd=str(target.parent),
            timeout=30
        )
        return {
            "stdout": process.stdout,
            "stderr": process.stderr,
            "returncode": process.returncode
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Script execution timed out")
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/download")
async def download_file(path: str):
    target = safe_path(path)
    if not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream"
    )


# --- Catch-all routes: GET=read-or-list, PUT=save-or-rename, DELETE=delete ---

@router.get("/{path:path}")
async def read_or_list(path: str = ""):
    target = safe_path(path)
    if not target.exists():
        raise HTTPException(404, "Path not found")
    if target.is_dir():
        items = []
        for item in target.iterdir():
            is_link = item.is_symlink()
            is_dir = item.is_dir()
            try:
                st = item.stat()
                size = st.st_size if item.is_file() else 0
                mtime = st.st_mtime
            except (OSError, FileNotFoundError):
                size = 0
                mtime = 0
            items.append({
                "name": item.name,
                "is_dir": is_dir,
                "is_link": is_link,
                "size": size,
                "mtime": mtime,
                "path": str(item.relative_to(BASE_DIR))
            })
        return {"path": str(target.relative_to(BASE_DIR)), "items": sorted(items, key=lambda x: (not x["is_dir"], x["name"].lower()))}
    if not target.is_file():
        raise HTTPException(404, "Not a file")
    try:
        content = target.read_text(encoding="utf-8")
        return {"content": content}
    except UnicodeDecodeError:
        raise HTTPException(400, "File is not text or encoding unsupported")


@router.put("/{path:path}")
async def save_or_rename_file(path: str, data: dict):
    if "content" in data:
        target = safe_path(path)
        if target.exists() and target.is_dir():
            raise HTTPException(400, "Cannot save to a directory")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data["content"], encoding="utf-8")
            return {"status": "ok", "path": str(target.relative_to(BASE_DIR))}
        except Exception as e:
            raise HTTPException(500, str(e))
    elif "new_name" in data:
        new_name = data["new_name"]
        if not new_name:
            raise HTTPException(400, "Отсутствует 'new_name'")
        target = safe_path(path)
        if not target.exists():
            raise HTTPException(404, "Item not found")
        new_path = target.parent / new_name
        target.rename(new_path)
        return {"status": "ok"}
    raise HTTPException(400, "Отсутствует 'content' или 'new_name'")


@router.delete("/{path:path}")
async def delete_item(path: str):
    target = safe_path(path)
    if not target.exists():
        raise HTTPException(404, "Item not found")
    if target.is_dir():
        if any(target.iterdir()):
            raise HTTPException(400, "Directory not empty")
        target.rmdir()
    else:
        target.unlink()
    return {"status": "ok"}
