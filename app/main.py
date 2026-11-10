from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import ROOT, settings
from app.database import add_message, connect, init_db, now_iso
from app.tutor import analyze_question, chat_response

app = FastAPI(title="AP個別指導", version="0.1.0")
WEB_DIR = ROOT / "web"
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_BYTES = settings.max_upload_mb * 1024 * 1024
subscribers: set[WebSocket] = set()
question_images: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
MAX_CACHED_IMAGES = 12
MAX_CACHED_IMAGE_BYTES = 64 * 1024 * 1024
agent_last_seen = 0.0
agent_clipboard_available = False


@app.on_event("startup")
def startup() -> None:
    init_db()


def cache_image(question_id: str, data: bytes, mime: str) -> None:
    question_images[question_id] = (data, mime)
    while (len(question_images) > MAX_CACHED_IMAGES or
           sum(len(image) for image, _ in question_images.values()) > MAX_CACHED_IMAGE_BYTES):
        question_images.popitem(last=False)


async def broadcast(event: dict) -> None:
    stale = []
    for client in tuple(subscribers):
        try:
            await client.send_json(event)
        except Exception:
            stale.append(client)
    for client in stale:
        subscribers.discard(client)


def detect_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_learning_enabled() -> bool:
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='learning_mode'").fetchone()
    return bool(row and row["value"] == "1")


def require_agent_token(authorization: str | None) -> None:
    if settings.api_token and not secrets.compare_digest(
        authorization or "", f"Bearer {settings.api_token}"
    ):
        raise HTTPException(status_code=401, detail="エージェント認証に失敗しました")


async def save_screenshot(data: bytes, declared_mime: str, subject: str | None,
                          background_tasks: BackgroundTasks) -> dict:
    if not is_learning_enabled():
        raise HTTPException(status_code=423, detail="学習モードがOFFです。設定をONにしてください。")
    if not data:
        raise HTTPException(status_code=400, detail="画像ファイルが空です")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"画像は{settings.max_upload_mb}MB以下にしてください")
    mime = detect_image(data)
    if mime not in ALLOWED_MIME or declared_mime not in ALLOWED_MIME or declared_mime != mime:
        raise HTTPException(status_code=415, detail="PNG、JPEG、WebP、GIF画像を指定してください")
    digest = hashlib.sha256(data).hexdigest()
    question_id = str(uuid.uuid4())
    with connect() as db:
        fingerprint = digest
        if db.execute("SELECT 1 FROM questions WHERE image_hash=?", (fingerprint,)).fetchone():
            fingerprint = f"{digest}:{question_id}"
        db.execute(
            "INSERT INTO questions(id,image_name,image_mime,image_hash,subject,created_at) VALUES(?,?,?,?,?,?)",
            (question_id, "", mime, fingerprint, subject if subject in ("A", "B") else None, now_iso()),
        )
        db.execute("INSERT INTO chat_messages(question_id,role,content,created_at) VALUES(?,?,?,?)",
                   (question_id, "user", "画像を送信しました", now_iso()))
    cache_image(question_id, data, mime)
    background_tasks.add_task(analyze_saved_question, question_id, data, mime)
    result = {"id": question_id}
    await broadcast({"type": "question_created", "question": result})
    return result


async def analyze_saved_question(question_id: str, image_data: bytes, mime: str) -> None:
    with connect() as db:
        row = db.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
    if not row:
        return
    try:
        text = await asyncio.to_thread(
            analyze_question, image_data, mime, row["hint_level"], row["subject"],
        )
        status = "ready"
    except Exception as exc:
        text = f"AI解析に失敗しました: {exc}"
        status = "error"
    with connect() as db:
        db.execute("UPDATE questions SET recognized_text=? WHERE id=?", (text, question_id))
    add_message(question_id, "assistant", text)
    await broadcast({"type": "analysis_complete", "id": question_id,
                     "status": status, "text": text})


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class SettingBody(BaseModel):
    learning_mode: bool


class QuestionUpdate(BaseModel):
    hint_level: int | None = Field(default=None, ge=0, le=5)
    reveal_answer: bool | None = None
    subject: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "ai_configured": bool(settings.openai_api_key), "model": settings.openai_model}


@app.get("/api/settings")
def get_settings() -> dict:
    return {"learning_mode": is_learning_enabled()}


@app.put("/api/settings")
async def set_settings(body: SettingBody) -> dict:
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('learning_mode',?)",
                   ("1" if body.learning_mode else "0",))
    await broadcast({"type": "settings_changed", "learning_mode": body.learning_mode})
    return {"learning_mode": body.learning_mode}


@app.post("/api/screenshots")
async def upload_screenshot(request: Request, background_tasks: BackgroundTasks,
                            subject: str | None = Query(default=None)) -> dict:
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdecimal() and int(content_length) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"画像は{settings.max_upload_mb}MB以下にしてください")
    data = await request.body()
    mime = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    return await save_screenshot(data, mime, subject, background_tasks)


@app.post("/api/agent/screenshots")
async def agent_upload(request: Request, background_tasks: BackgroundTasks,
                       subject: str | None = Query(default=None),
                       authorization: str | None = Header(default=None)) -> dict:
    require_agent_token(authorization)
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdecimal() and int(content_length) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"画像は{settings.max_upload_mb}MB以下にしてください")
    data = await request.body()
    mime = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    return await save_screenshot(data, mime, subject, background_tasks)


@app.post("/api/agent/heartbeat")
async def agent_heartbeat(clipboard_available: bool = Query(default=False),
                          authorization: str | None = Header(default=None)) -> dict:
    global agent_last_seen, agent_clipboard_available
    require_agent_token(authorization)
    agent_last_seen = time.time()
    agent_clipboard_available = clipboard_available
    return {"ok": True}


@app.get("/api/agent/status")
def get_agent_status() -> dict:
    return {"online": time.time() - agent_last_seen < 35 if agent_last_seen else False,
            "clipboard_available": agent_clipboard_available}


@app.get("/api/questions")
def list_questions() -> list[dict]:
    with connect() as db:
        rows = db.execute("""SELECT id,subject,recognized_text,hint_level,reveal_answer,created_at
                              FROM questions ORDER BY created_at DESC""").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/questions/{question_id}")
def get_question(question_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="問題が見つかりません")
    return {**dict(row), "image_url": f"/api/questions/{question_id}/image",
            "image_available": question_id in question_images or bool(row["image_name"] and
                (settings.images_dir / row["image_name"]).is_file())}


@app.get("/api/questions/{question_id}/messages")
def get_messages(question_id: str) -> list[dict]:
    with connect() as db:
        exists = db.execute("SELECT 1 FROM questions WHERE id=?", (question_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="問題が見つかりません")
        rows = db.execute("SELECT role,content,created_at FROM chat_messages WHERE question_id=? ORDER BY id",
                          (question_id,)).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/questions/{question_id}/image")
def get_image(question_id: str) -> Response:
    with connect() as db:
        row = db.execute("SELECT image_name,image_mime FROM questions WHERE id=?", (question_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="問題が見つかりません")
    cached = question_images.get(question_id)
    if cached:
        return Response(content=cached[0], media_type=cached[1], headers={"Cache-Control": "no-store"})
    legacy_path = settings.images_dir / row["image_name"] if row["image_name"] else None
    if legacy_path and legacy_path.is_file():
        return FileResponse(legacy_path, media_type=row["image_mime"], headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=410, detail="画像はメモリー上にありません。撮影し直してください。")


@app.patch("/api/questions/{question_id}")
async def update_question(question_id: str, body: QuestionUpdate) -> dict:
    if body.subject not in (None, "A", "B"):
        raise HTTPException(status_code=422, detail="科目はAまたはBを指定してください")
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"ok": True}
    with connect() as db:
        if not db.execute("SELECT 1 FROM questions WHERE id=?", (question_id,)).fetchone():
            raise HTTPException(status_code=404, detail="問題が見つかりません")
        db.execute("UPDATE questions SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?",
                   (*updates.values(), question_id))
    return {"ok": True}


@app.post("/api/questions/{question_id}/chat")
async def chat(question_id: str, body: ChatBody) -> dict:
    if not is_learning_enabled():
        raise HTTPException(status_code=423, detail="学習モードがOFFです")
    with connect() as db:
        row = db.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="問題が見つかりません")
    cached = question_images.get(question_id)
    if cached:
        image_data, image_mime = cached
    elif row["image_name"] and (settings.images_dir / row["image_name"]).is_file():
        image_data = (settings.images_dir / row["image_name"]).read_bytes()
        image_mime = row["image_mime"]
    else:
        raise HTTPException(status_code=410, detail="画像はメモリー上にありません。撮影し直してください。")
    add_message(question_id, "user", body.message)
    try:
        answer = await asyncio.to_thread(
            chat_response, question_id, body.message, image_data,
            image_mime, row["hint_level"], bool(row["reveal_answer"]), row["subject"],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AIへの問い合わせに失敗しました: {exc}") from exc
    add_message(question_id, "assistant", answer)
    await broadcast({"type": "message_added", "id": question_id})
    return {"role": "assistant", "content": answer, "created_at": now_iso()}


@app.delete("/api/questions/{question_id}")
async def delete_question(question_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT image_name FROM questions WHERE id=?", (question_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="問題が見つかりません")
        db.execute("DELETE FROM questions WHERE id=?", (question_id,))
    question_images.pop(question_id, None)
    if row["image_name"]:
        (settings.images_dir / row["image_name"]).unlink(missing_ok=True)
    await broadcast({"type": "question_deleted", "id": question_id})
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    subscribers.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers.discard(websocket)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
