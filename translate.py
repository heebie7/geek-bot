"""
Translation module for geek-bot.

Detects language (RU/EN) and translates to the other via Gemini.
Handles both text and image (screenshot OCR + translate).
"""

import asyncio
from typing import Optional

import requests as _requests
from google.genai import types
import google.generativeai as genai

from config import gemini_client, GEMINI_MODEL, GEMINI_PRO_MODEL, logger


TRANSLATE_PROMPT = """Determine the language of the following text.
If it's Russian — translate to English. If it's English — translate to Russian.
If the text contains both languages, translate each part to the other language.

Rules:
- Return ONLY the translation, no explanations or labels
- Preserve formatting (line breaks, lists, emphasis)
- Keep proper nouns, brand names, and technical terms as-is when appropriate

Text:
{text}"""

FORMULATE_PROMPT = """The user wants to express this idea in English.
Style: {style}

{style_description}

Rules:
- Return ONLY the English text, no explanations
- Keep the core meaning and emotional tone
- Make it sound natural for a native speaker in that context
- If the input is already partially in English, refine it

Text to formulate:
{text}"""

STYLE_DESCRIPTIONS = {
    "tumblr": "Tumblr post style: casual, expressive, can use lowercase, sentence fragments, "
              "emotional emphasis via italics or caps. Fandom-literate, neurodivergent-friendly tone. "
              "Can be analytical or personal. Tags-style commentary is OK.",
    "dm": "Direct message / correspondence style: conversational but clear, warm, slightly informal. "
          "Like writing to a colleague you respect but are friendly with. "
          "No slang overload, no overly formal constructions.",
}

OCR_TRANSLATE_PROMPT = """This is a screenshot containing text.
1. Extract all readable text from the image.
2. Determine the language: if Russian — translate to English; if English — translate to Russian.
   If mixed, translate each part to the other language.

Rules:
- Return ONLY the translation, no explanations or labels
- Preserve the structure and formatting of the original text
- Keep proper nouns, brand names, and technical terms as-is when appropriate"""


def translate_text(text: str) -> Optional[str]:
    """Translate text RU↔EN via Gemini."""
    if not gemini_client:
        logger.error("No Gemini client for translation")
        return None

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=TRANSLATE_PROMPT.format(text=text),
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return None


def formulate_text(text: str, style: str) -> Optional[str]:
    """Formulate text in English with given style (tumblr/dm) via Gemini."""
    if not gemini_client:
        logger.error("No Gemini client for formulation")
        return None

    style_desc = STYLE_DESCRIPTIONS.get(style, STYLE_DESCRIPTIONS["dm"])
    prompt = FORMULATE_PROMPT.format(
        style=style,
        style_description=style_desc,
        text=text,
    )

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Formulation error: {e}")
        return None


def translate_image(photo_bytes: bytes, caption: Optional[str] = None) -> Optional[str]:
    """OCR screenshot and translate RU↔EN via Gemini Vision."""
    if not gemini_client:
        logger.error("No Gemini client for translation")
        return None

    parts = [
        types.Part.from_bytes(data=photo_bytes, mime_type="image/jpeg"),
        types.Part(text=OCR_TRANSLATE_PROMPT),
    ]
    if caption:
        parts.append(types.Part(text=f"\nCaption context: {caption}"))

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(parts=parts)],
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Translation image error: {e}")
        return None


_ARTICLE_PROMPT = """Перед тобой HTML веб-страницы. Задача:
1. Найди и извлеки только основной текст статьи — без меню, навигации, рекламы, футеров, комментариев.
2. Переведи на русский язык.
3. Верни ТОЛЬКО переведённый текст. Никаких пояснений.
4. Сохраняй структуру: если есть заголовки — выдели их на отдельной строке. Абзацы разделяй пустой строкой.

HTML:
{html}"""


def _fetch_url_sync(url: str) -> str:
    """Synchronous HTTP fetch. Run via asyncio.to_thread."""
    resp = _requests.get(
        url, timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
        allow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text[:80000]


def _chunk_text(text: str, max_len: int = 3800) -> list:
    """Split text into chunks ≤ max_len chars, breaking at paragraph boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for \n\n
        if current_len + para_len > max_len and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks or ["Текст не извлечён."]


async def fetch_and_translate_url(url: str) -> list:
    """Fetch URL, extract article text via Gemini Pro, translate to Russian.

    Returns list of text chunks ready to send as Telegram messages.
    """
    if not gemini_client:
        return ["Gemini недоступен."]

    try:
        html = await asyncio.to_thread(_fetch_url_sync, url)
    except Exception as e:
        logger.error(f"URL fetch error ({url}): {e}")
        return [f"Не удалось загрузить страницу: {e}"]

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_PRO_MODEL,
            contents=_ARTICLE_PROMPT.format(html=html),
            config=genai.types.GenerateContentConfig(max_output_tokens=8000),
        )
        if not response.text:
            return ["Не удалось извлечь текст статьи."]
        translated = response.text.strip()
    except Exception as e:
        logger.error(f"Article translation error: {e}")
        return [f"Ошибка перевода: {e}"]

    return _chunk_text(translated)
