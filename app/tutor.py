from __future__ import annotations

import base64
from pathlib import Path

from openai import OpenAI

from app.config import ROOT, settings
from app.database import messages_for

PROMPT_PATH = ROOT / "tutor-skill" / "SKILL.md"
REFERENCE_DIR = ROOT / "tutor-skill" / "references"


def tutor_instructions(hint_level: int, reveal_answer: bool, subject: str | None) -> str:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    references = "\n\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(REFERENCE_DIR.glob("*.md"))
    )
    mode = (
        "生徒は正答の開示を明示的に許可しています。答えと根拠を伝えてください。"
        if reveal_answer
        else "正答や選択肢の正誤を明かさないでください。"
    )
    return (
        f"{prompt}\n\n{references}\n\n現在の科目: {subject or '未設定'}。"
        f"現在のヒントレベル: {hint_level}/5。{mode}"
    )


def response_text(response) -> str:
    text = getattr(response, "output_text", "")
    return text.strip() or "画像の内容を十分に読み取れませんでした。問題文が見える範囲で再撮影してください。"


def analyze_question(image_data: bytes, mime: str, hint_level: int, subject: str | None) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY が設定されていません。.env にAPIキーを設定してください。")
    encoded = base64.b64encode(image_data).decode("ascii")
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(
        model=settings.openai_model,
        instructions=tutor_instructions(hint_level, False, subject),
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": (
                    "この応用情報技術者試験の問題を読み取ってください。文字や図表が不鮮明・欠落している場合は"
                    "推測で補わず、まず問題の読み取り状況を伝えてください。読める場合は何を問う問題かを"
                    "やさしい日本語で説明し、生徒がどこまで理解しているか尋ねてください。正答は示さないでください。"
                )},
                {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "original"},
            ],
        }],
    )
    return response_text(response)


def chat_response(question_id: str, message: str, image_data: bytes, mime: str,
                  hint_level: int, reveal_answer: bool, subject: str | None) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY が設定されていません。.env にAPIキーを設定してください。")
    history = messages_for(question_id)[:-1]  # the route has already persisted this turn's user message
    input_messages: list[dict] = []
    first_user = True
    for item in history:
        if item["role"] == "user" and first_user:
            first_user = False
            continue
        input_messages.append({"role": item["role"], "content": item["content"]})
    input_messages.append({"role": "user", "content": message})
    encoded = base64.b64encode(image_data).decode("ascii")
    input_messages.append({
        "role": "user",
        "content": [
            {"type": "input_text", "text": "参照画像です。すでに読めている部分を必要に応じて確認してください。"},
            {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "original"},
        ],
    })
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(
        model=settings.openai_model,
        instructions=tutor_instructions(hint_level, reveal_answer, subject),
        input=input_messages,
    )
    return response_text(response)
