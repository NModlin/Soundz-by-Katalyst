"""Private, fail-closed music manager API for the Billy Bassline playlist."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MUSIC_DATA_DIR", BASE / "data"))
STAGING_DIR = DATA_DIR / "staging"
STATE_FILE = DATA_DIR / "library.json"
ALLOWED_EXTENSIONS = {".mp3", ".wav"}
MAX_BYTES = int(os.environ.get("MUSIC_MAX_UPLOAD_BYTES", 250 * 1024 * 1024))
ADMIN_TOKEN = os.environ.get("MUSIC_ADMIN_TOKEN", "").strip()

app = Flask(__name__, static_folder=".", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES * 20


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return {"tracks": []}
    try:
        value = json.loads(STATE_FILE.read_text())
        return value if isinstance(value, dict) and isinstance(value.get("tracks"), list) else {"tracks": []}
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Music library state is unreadable; refusing to continue")


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(STATE_FILE)


def auth_required() -> bool:
    supplied = request.headers.get("Authorization", "")
    return bool(ADMIN_TOKEN) and secrets.compare_digest(supplied, f"Bearer {ADMIN_TOKEN}")


def private_endpoint(view):
    def wrapped(*args, **kwargs):
        if not ADMIN_TOKEN:
            return jsonify(error="Manager is not configured: set MUSIC_ADMIN_TOKEN"), 503
        if not auth_required():
            return jsonify(error="Authentication required"), 401
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


def clean_title(value: str, fallback: str) -> str:
    title = re.sub(r"\s+", " ", (value or "").strip())
    return title[:160] or fallback[:160]


def validate_file(upload):
    original = secure_filename(upload.filename or "")
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Only WAV and MP3 files are accepted")
    if not original or upload.content_length and upload.content_length > MAX_BYTES:
        raise ValueError("File is missing or exceeds the upload limit")
    upload.stream.seek(0)
    head = upload.stream.read(16)
    upload.stream.seek(0)
    # WAV is RIFF/WAVE. MP3 is either an ID3 file or an MPEG frame.
    is_wav = suffix == ".wav" and head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    is_mp3 = suffix == ".mp3" and (head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))
    if not (is_wav or is_mp3):
        raise ValueError("File signature does not match its extension")
    return original, suffix


@app.get("/api/health")
def health():
    return jsonify(ok=True, manager_configured=bool(ADMIN_TOKEN))


@app.get("/api/playlist")
def playlist():
    state = load_state()
    tracks = [t for t in state["tracks"] if t.get("status") == "published"]
    tracks.sort(key=lambda t: (t.get("position", 0), t.get("created_at", "")))
    return jsonify(tracks=[{k: t[k] for k in ("id", "title", "version", "position", "published_at")} for t in tracks])


@app.get("/api/playlist/<track_id>/audio")
def public_audio(track_id: str):
    state = load_state()
    track = next((t for t in state["tracks"] if t["id"] == track_id and t.get("status") == "published"), None)
    if not track:
        return jsonify(error="Track not found"), 404
    return send_from_directory(STAGING_DIR, track["stored_name"], conditional=True)


@app.get("/api/manager/tracks/<track_id>/audio")
@private_endpoint
def private_audio(track_id: str):
    track = find_track(load_state(), track_id)
    if not track:
        return jsonify(error="Track not found"), 404
    return send_from_directory(STAGING_DIR, track["stored_name"], conditional=True)


@app.get("/api/manager/library")
@private_endpoint
def manager_library():
    state = load_state()
    tracks = sorted(state["tracks"], key=lambda t: (t.get("position", 0), t.get("created_at", "")))
    return jsonify(tracks=tracks)


@app.post("/api/manager/upload")
@private_endpoint
def upload():
    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify(error="Choose one or more WAV or MP3 files"), 400
    state = load_state()
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    created = []
    current_destination = None
    try:
        for upload in uploads:
            original, suffix = validate_file(upload)
            track_id = uuid.uuid4().hex
            stored_name = f"{track_id}{suffix}"
            destination = STAGING_DIR / stored_name
            current_destination = destination
            upload.save(destination)
            if destination.stat().st_size > MAX_BYTES:
                destination.unlink(missing_ok=True)
                raise ValueError("File exceeds the upload limit")
            title = clean_title(request.form.get(f"title:{upload.filename}"), Path(original).stem)
            track = {"id": track_id, "title": title, "version": clean_title(request.form.get(f"version:{upload.filename}"), "Original"),
                     "original_name": original, "stored_name": stored_name, "status": "draft", "position": len(state["tracks"]),
                     "created_at": now(), "updated_at": now(), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
            state["tracks"].append(track)
            created.append(track)
        save_state(state)
    except Exception as exc:
        if current_destination:
            current_destination.unlink(missing_ok=True)
        for track in created:
            (STAGING_DIR / track["stored_name"]).unlink(missing_ok=True)
        return jsonify(error=str(exc)), 400
    return jsonify(tracks=created), 201


def find_track(state, track_id):
    return next((t for t in state["tracks"] if t["id"] == track_id), None)


@app.patch("/api/manager/tracks/<track_id>")
@private_endpoint
def update_track(track_id):
    state = load_state(); track = find_track(state, track_id)
    if not track:
        return jsonify(error="Track not found"), 404
    data = request.get_json(silent=True) or {}
    if "title" in data: track["title"] = clean_title(str(data["title"]), track["title"])
    if "version" in data: track["version"] = clean_title(str(data["version"]), track["version"])
    track["updated_at"] = now(); save_state(state)
    return jsonify(track=track)


@app.post("/api/manager/tracks/<track_id>/publish")
@private_endpoint
def publish(track_id):
    state = load_state(); track = find_track(state, track_id)
    if not track: return jsonify(error="Track not found"), 404
    track["status"] = "published"; track["published_at"] = now(); track["updated_at"] = now()
    save_state(state); return jsonify(track=track)


@app.post("/api/manager/tracks/<track_id>/unpublish")
@private_endpoint
def unpublish(track_id):
    state = load_state(); track = find_track(state, track_id)
    if not track: return jsonify(error="Track not found"), 404
    track["status"] = "draft"; track["updated_at"] = now(); save_state(state)
    return jsonify(track=track)


@app.post("/api/manager/order")
@private_endpoint
def order_tracks():
    ids = (request.get_json(silent=True) or {}).get("ids", [])
    state = load_state(); by_id = {t["id"]: t for t in state["tracks"]}
    if set(ids) != set(by_id) or len(ids) != len(by_id): return jsonify(error="Order must include every track exactly once"), 400
    for position, track_id in enumerate(ids): by_id[track_id]["position"] = position
    save_state(state); return jsonify(ok=True)


@app.delete("/api/manager/tracks/<track_id>")
@private_endpoint
def remove_track(track_id):
    state = load_state(); track = find_track(state, track_id)
    if not track: return jsonify(error="Track not found"), 404
    # Deliberately keep the staged recording: removing a playlist entry never deletes audio.
    state["tracks"] = [t for t in state["tracks"] if t["id"] != track_id]
    save_state(state); return jsonify(ok=True, preserved_file=track["stored_name"])


@app.get("/manager.html")
def manager_page():
    return send_from_directory(BASE, "manager.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
