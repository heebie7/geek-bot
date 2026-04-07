"""
Translation module for geek-bot.

Detects language (RU/EN) and translates to the other via Gemini.
Handles both text and image (screenshot OCR + translate).
"""

from typing import Optional

from google.genai import types

from config import gemini_client, GEMINI_MODEL, logger


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
