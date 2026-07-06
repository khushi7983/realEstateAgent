import base64
import logging
import os
import time
import uuid

# ── Gemini (commented out — kept for rollback) ──────────────────────────────
# import google.generativeai as genai
# ─────────────────────────────────────────────────────────────────────────────

import jwt
import requests
from dotenv import load_dotenv
from fastapi.concurrency import run_in_threadpool
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from requests.adapters import HTTPAdapter
from pydantic import BaseModel

from services.inventory import (
    build_inventory_context,
    detect_city_from_text,
    format_available_cities,
    get_apartments_for_city,
)
from services.language import language_instruction, normalize_language_code, resolve_response_language

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
SORAVM_API_KEY = os.getenv("SORAVM_API_KEY")

# ── DeepSeek (active) ────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL   = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "120"))
DEEPSEEK_TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.4"))
DEEPSEEK_HISTORY_TURNS = int(os.getenv("DEEPSEEK_HISTORY_TURNS", "8"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "45"))
TTS_CODEC = os.getenv("TTS_CODEC", "mp3")
# ─────────────────────────────────────────────────────────────────────────────

# ── Gemini env vars (commented out — kept for rollback) ─────────────────────
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash-lite")
# GEMINI_MODEL_FALLBACKS = [
#     model.strip()
#     for model in os.getenv(
#         "GEMINI_MODEL_FALLBACKS",
#         "models/gemini-flash-lite-latest,models/gemini-flash-latest,models/gemini-2.5-flash",
#     ).split(",")
#     if model.strip()
# ]
# if GEMINI_API_KEY:
#     genai.configure(api_key=GEMINI_API_KEY)
# ─────────────────────────────────────────────────────────────────────────────

from routers.properties import router as properties_router

app = FastAPI(title="RealEstateAgent Voice Pipeline")
app.include_router(properties_router)

log = logging.getLogger(__name__)

_http_session = requests.Session()
_http_adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
_http_session.mount("http://", _http_adapter)
_http_session.mount("https://", _http_adapter)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class VoiceTurnRequest(BaseModel):
    transcript: str
    voice: str = "default"
    language: str = "unknown"     # STT-detected BCP-47 code, or "unknown" for text detection
    session_id: str = "default"   # unique per browser session
    city: str | None = None       # active city from UI or detected from speech


# In-memory conversation store  { session_id -> [messages] }
# Each message is a dict like {"role": "user" | "assistant", "content": "..."}
# We cap history at MAX_HISTORY_MESSAGES (pairs of user+assistant turns) so
# the context window never blows up.
MAX_HISTORY_MESSAGES = 20   # = 10 user turns + 10 assistant turns
conversation_store: dict[str, list[dict]] = {}


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(status_code=500, detail=f"Missing {name} in backend/.env")
    return value


def soravm_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {require_env('SORAVM_API_KEY')}"}


def transcribe_with_soravm(audio_file: UploadFile, language_code: str = "unknown") -> dict:
    started_at = time.perf_counter()
    audio_bytes = audio_file.file.read()
    url = "https://api.sarvam.ai/speech-to-text"
    files = {"file": (audio_file.filename, audio_bytes, audio_file.content_type)}
    data = {
        "model": "saaras:v3",
        "mode": "transcribe",
        "language_code": language_code or "unknown",
    }

    response = _http_session.post(url, headers=soravm_headers(), files=files, data=data, timeout=HTTP_TIMEOUT)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=response.text)

    payload = response.json()
    log.info(
        "latency stage=stt total_ms=%.0f audio_bytes=%d language=%s",
        (time.perf_counter() - started_at) * 1000,
        len(audio_bytes),
        language_code or "unknown",
    )
    return payload


def resolve_speaker(voice: str) -> str:
    if not voice or voice == "default":
        return "shubh"
    return voice


def tts_mime_type() -> str:
    if TTS_CODEC.lower() == "mp3":
        return "audio/mpeg"
    if TTS_CODEC.lower() in {"wav", "wave"}:
        return "audio/wav"
    return f"audio/{TTS_CODEC.lower()}"


def synthesize_sarvam_tts(text: str, target_language_code: str, voice: str = "default") -> tuple[str, str]:
    """Call Sarvam Bulbul v3 TTS. Returns (audio_base64, mime_type)."""
    started_at = time.perf_counter()
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {**soravm_headers(), "Content-Type": "application/json"}
    payload = {
        "text": text,
        "target_language_code": normalize_language_code(target_language_code),
        "model": "bulbul:v3",
        "speaker": resolve_speaker(voice),
        "output_audio_codec": TTS_CODEC,
    }

    response = _http_session.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=response.text)

    if response.headers.get("content-type", "").startswith("application/json"):
        body = response.json()
        audios = body.get("audios")
        if not audios or not isinstance(audios, list) or not isinstance(audios[0], str):
            raise HTTPException(status_code=502, detail=f"TTS service returned invalid JSON payload: {body}")
        log.info(
            "latency stage=tts total_ms=%.0f codec=%s language=%s",
            (time.perf_counter() - started_at) * 1000,
            TTS_CODEC,
            target_language_code,
        )
        return audios[0], tts_mime_type()

    content_type = tts_content_type(response)
    if not content_type.startswith("audio/"):
        raise HTTPException(status_code=502, detail="TTS service returned a non-audio response")

    audio_base64 = base64.b64encode(response.content).decode("utf-8")
    log.info(
        "latency stage=tts total_ms=%.0f codec=%s language=%s response_bytes=%d",
        (time.perf_counter() - started_at) * 1000,
        TTS_CODEC,
        target_language_code,
        len(response.content),
    )
    return audio_base64, content_type


def synthesize_with_soravm(text: str, voice: str, target_language_code: str = "en-IN") -> bytes:
    audio_base64, _ = synthesize_sarvam_tts(text, target_language_code, voice)
    return base64.b64decode(audio_base64)


def tts_content_type(response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "audio/wav")
    return content_type.split(";", 1)[0].strip() or "audio/wav"


# def build_real_estate_reply(transcript: str, city: str | None = None) -> str:
#     cleaned_text = transcript.strip()
#     lowered_text = cleaned_text.lower()

#     if not cleaned_text:
#         return "I did not catch that. Tell me your budget, preferred area, or number of bedrooms."

#     resolved_city = city or detect_city_from_text(cleaned_text)
#     available_cities = format_available_cities()

#     if not resolved_city and any(
#         keyword in lowered_text
#         for keyword in (
#             "property",
#             "properties",
#             "apartment",
#             "flat",
#             "bhk",
#             "option",
#             "show",
#             "recommend",
#             "looking",
#             "location",
#             "area",
#             "near",
#             "commute",
#         )
#     ):
#         return (
#             f"I can help in these locations: {available_cities}. "
#             "Tell me which city or locality you want, and I will narrow the options."
#         )

#     listings = get_apartments_for_city(resolved_city)

#     if resolved_city and not listings:
#         return (
#             f"I do not have listings in {resolved_city} right now. "
#             f"Available locations are {available_cities}. Which city should I search next?"
#         )

#     if listings and any(keyword in lowered_text for keyword in ("property", "properties", "apartment", "flat", "bhk", "option", "show", "recommend", "looking")):
#         names = ", ".join(
#             f"{item['name']} ({item['bhk']}, {item['price']})" for item in listings
#         )
#         return f"In {resolved_city}, I have these options: {names}. Tell me which one interests you."

#     if listings and resolved_city and any(keyword in lowered_text for keyword in ("delhi", "pune", "mumbai", "hyderabad", "gurugram", "gurgaon")):
#         names = ", ".join(
#             f"{item['name']} ({item['bhk']}, {item['price']})" for item in listings
#         )
#         return f"For {resolved_city}, here are our listings: {names}. Which would you like to explore?"

#     if listings:
#         for item in listings:
#             name_lower = item["name"].lower()
#             if name_lower in lowered_text or any(
#                 part in lowered_text for part in name_lower.split() if len(part) > 3
#             ):
#                 return (
#                     f"{item['name']} is a great choice — {item['bhk']} starting at {item['price']} "
#                     f"in {item['address']}. {item['description']}"
#                 )

#     if any(keyword in lowered_text for keyword in ("budget", "price", "cost")):
#         return (
#             "I can narrow homes by budget. Share your price band and preferred locality, "
#             "and I will shortlist the most relevant projects."
#         )

#     if any(keyword in lowered_text for keyword in ("visit", "site visit", "book", "schedule")):
#         return (
#             "I can help schedule a site visit. Tell me the project name and your preferred time window, "
#             "and I will prepare the booking details."
#         )

#     if any(keyword in lowered_text for keyword in ("location", "area", "near", "commute")):
#         return (
#             "I can search by location, commute, or nearby landmarks. Share the area you want, "
#             "and I will rank the closest matches."
#         )

#     if any(keyword in lowered_text for keyword in ("2 bhk", "3 bhk", "apartment", "villa", "flat")):
#         return (
#             "I can filter inventory by property type and configuration. Tell me the exact home size "
#             "and I will refine the list."
#         )

#     return (
#         "I can help with budget, location, amenities, and site visits. Tell me what matters most, "
#         "and I will narrow the options."
#     )


# ── Gemini helpers (commented out — kept for rollback) ───────────────────────
# def extract_gemini_text(response) -> str:
#     try:
#         if response.text:
#             return response.text.strip()
#     except ValueError:
#         pass
#     for candidate in getattr(response, "candidates", []) or []:
#         content = getattr(candidate, "content", None)
#         if not content:
#             continue
#         parts = getattr(content, "parts", None) or []
#         text_parts = [getattr(part, "text", "") or "" for part in parts]
#         combined = "".join(text_parts).strip()
#         if combined:
#             return combined
#     return ""
#
# def gemini_model_candidates() -> list[str]:
#     models: list[str] = []
#     for model_name in [GEMINI_MODEL, *GEMINI_MODEL_FALLBACKS]:
#         if model_name and model_name not in models:
#             models.append(model_name)
#     return models
#
# def generate_gemini_reply(transcript: str) -> tuple[str, str]:
#     if not GEMINI_API_KEY:
#         return build_real_estate_reply(transcript), "fallback"
#     prompt = (
#         "You are a helpful real estate assistant. Respond concisely and naturally to the user query. "
#         "Keep the answer focused on property search, budgets, locations, amenities, or site visits. "
#         f"User query: {transcript}"
#     )
#     last_error = "Unknown Gemini error"
#     for model_name in gemini_model_candidates():
#         try:
#             model = genai.GenerativeModel(model_name)
#             response = model.generate_content(prompt)
#             output_text = extract_gemini_text(response)
#             if output_text:
#                 print(f"Gemini reply generated with {model_name}")
#                 return output_text, model_name
#             last_error = f"{model_name} returned an empty response"
#             print(f"Gemini empty response from {model_name}, trying next model")
#         except Exception as exc:
#             last_error = str(exc)
#             print(f"Gemini generation failed for {model_name}: {exc}")
#     print(f"Gemini generation failed for all models, falling back: {last_error}")
#     return build_real_estate_reply(transcript), "fallback"
# ─────────────────────────────────────────────────────────────────────────────


# ── DeepSeek LLM (active) ────────────────────────────────────────────────────
def generate_deepseek_reply(
    transcript: str,
    history: list[dict],
    city: str | None = None,
    language: str = "en-IN",
) -> tuple[str, str]:
    """Call DeepSeek's OpenAI-compatible chat completions endpoint.
    `history` is the full message list for this session (excluding the current
    user message — we append it inside this function).
    Falls back to the local rule-based reply if the API key is missing
    or the call fails.
    """
    started_at = time.perf_counter()
    resolved_city = city or detect_city_from_text(transcript)
    inventory_block = build_inventory_context(resolved_city)
    recent_history = history[-DEEPSEEK_HISTORY_TURNS:]

    if not DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY not set — using local fallback reply")
        return build_real_estate_reply(transcript, resolved_city), "fallback"

    # ── OLD system prompt (commented out) ────────────────────────────────────
    # system_prompt = (
    #     "You are a concise, helpful real estate assistant for the Indian market. "
    #     "Remember everything the user has told you in this conversation — their budget, "
    #     "preferred location, property type, number of bedrooms, and any other preferences. "
    #     "Use that context in every reply without asking for information already given. "
    #     "Answer only questions about property search, budgets, locations, amenities, "
    #     "EMI, or site visits. Keep replies under 3 sentences.\n"
    #     f"{language_instruction(language)}\n\n"
    #     + (inventory_block if inventory_block else "No property inventory loaded for this city yet.")
    # )
    # ─────────────────────────────────────────────────────────────────────────

    # ── NEW system prompt (agent-facing real estate AI) ───────────────────────
    system_prompt = (
        "You are ARIA — an expert AI Real Estate Assistant for a reputed Indian property company.\n\n"

        "Your primary users are SALES AGENTS who need fast, accurate, and context-aware information "
        "to confidently assist customers. Your responsibility is to provide clear, reliable, and concise "
        "answers using only the available project knowledge and the ongoing conversation context.\n\n"

        "═══════════════════════════════════════════════════════\n"
        "CONTEXT MANAGEMENT RULES (CRITICAL — FOLLOW STRICTLY)\n"
        "═══════════════════════════════════════════════════════\n"
        "• Always treat the latest user-provided value for any preference as the active context.\n"
        "• Whenever the user provides a new city, project, budget, BHK, possession timeline, or customer "
        "requirement, immediately replace the previous value and continue using only the latest information.\n"
        "• Track and remember throughout the conversation:\n"
        "  - Active city\n"
        "  - Active project\n"
        "  - Budget range\n"
        "  - BHK preference\n"
        "  - Customer requirements\n"
        "  - Possession timeline (if mentioned)\n"
        "  - Investment purpose (if mentioned)\n\n"
        "• Follow-up questions should automatically refer to the current active context unless the user "
        "explicitly changes it.\n\n"
        "Example:\n"
        "User: Show me projects in Hyderabad.\n"
        "User: Which schools are nearby?\n"
        "→ Answer for Hyderabad without asking the city again.\n\n"
        "• Never ask the user to repeat information that has already been provided.\n"
        "• Never repeat information that has already been explained unless the user specifically requests a recap.\n"
        "• If multiple projects are being discussed together (for example, during a comparison), keep all "
        "referenced projects active until the comparison is complete.\n"
        "• If the user's request is ambiguous, ask one concise clarifying question before answering.\n\n"

        "═══════════════════════════════════════════════════════\n"
        "KNOWLEDGE & ACCURACY\n"
        "═══════════════════════════════════════════════════════\n"
        "• Base every response only on:\n"
        "  1. The retrieved project information.\n"
        "  2. The current conversation context.\n"
        "• Never invent, assume, or guess project details.\n"
        "• If information is unavailable or uncertain, clearly say that the information is currently "
        "unavailable instead of generating an answer.\n"
        "• Never fabricate pricing, possession dates, amenities, distances, approvals, or investment claims.\n\n"

        "═══════════════════════════════════════════════════════\n"
        "RESPONSE STYLE\n"
        "═══════════════════════════════════════════════════════\n"
        "• Respond like an experienced senior real estate sales consultant.\n"
        "• Be professional, confident, conversational, and helpful.\n"
        "• Keep responses concise while ensuring all important information is included.\n"
        "• Prefer short paragraphs or bullet points.\n"
        "• Lead with the direct answer first, followed by supporting details.\n"
        "• Avoid unnecessary introductions.\n"
        "• Do NOT use phrases such as: 'Great question!', 'Certainly!', 'Absolutely!', or 'I'd be happy to help.'\n\n"

        "═══════════════════════════════════════════════════════\n"
        "LOCATION QUERIES\n"
        "═══════════════════════════════════════════════════════\n"
        "For location-related questions always include:\n"
        "• Project location\n"
        "• Nearby landmarks\n"
        "• Metro connectivity (if available)\n"
        "• Airport connectivity (if available)\n"
        "• Railway station (if available)\n"
        "• Schools and hospitals nearby\n"
        "• Distance in kilometers\n"
        "• Approximate travel time\n"
        "• 1–2 investment advantages whenever available\n\n"

        "═══════════════════════════════════════════════════════\n"
        "PROJECT COMPARISONS\n"
        "═══════════════════════════════════════════════════════\n"
        "When comparing projects, present in a clear side-by-side format covering:\n"
        "• Location\n"
        "• Price\n"
        "• Configuration (BHK)\n"
        "• Amenities\n"
        "• Connectivity\n"
        "• Possession timeline\n"
        "• Investment potential\n"
        "• Best suited for\n"
        "End with a brief recommendation based on the comparison.\n\n"

        "═══════════════════════════════════════════════════════\n"
        "PROJECT RECOMMENDATIONS\n"
        "═══════════════════════════════════════════════════════\n"
        "When recommending projects, prioritize customer preferences in this order:\n"
        "1. City\n"
        "2. Budget\n"
        "3. BHK\n"
        "4. Location preference\n"
        "5. Possession timeline\n"
        "6. Investment purpose\n"
        "7. Amenities\n"
        "Recommend only the best 1–2 matching projects and briefly explain why each is suitable.\n\n"

        "═══════════════════════════════════════════════════════\n"
        "WHAT YOU CAN ANSWER\n"
        "═══════════════════════════════════════════════════════\n"
        "✔ Project location, address, locality, city, sector\n"
        "✔ Nearby landmarks: metro stations, schools, hospitals, malls, airports, railway stations\n"
        "✔ Distance in kilometers and approximate travel time\n"
        "✔ Property configurations, BHK availability, pricing, possession timelines, amenities\n"
        "✔ Investment advantages and appreciation potential\n"
        "✔ Project comparisons\n"
        "✔ Budget guidance and EMI estimates\n"
        "✔ Site visit scheduling\n"
        "✔ Best project recommendations based on customer needs\n\n"

        "═══════════════════════════════════════════════════════\n"
        "OUT OF SCOPE\n"
        "═══════════════════════════════════════════════════════\n"
        "• Politely decline questions unrelated to real estate or the company's property portfolio.\n"
        "• Redirect the conversation back to property-related assistance whenever appropriate.\n\n"

        "═══════════════════════════════════════════════════════\n"
        "FINAL RESPONSE RULES\n"
        "═══════════════════════════════════════════════════════\n"
        "Before responding, ensure that:\n"
        "✓ The latest conversation context has been applied.\n"
        "✓ No outdated city, project, or customer preference is used.\n"
        "✓ No information has been guessed.\n"
        "✓ The answer is concise, accurate, and directly addresses the user's query.\n"
        "✓ Customer preferences are remembered throughout the conversation until they are changed.\n\n"

        f"{language_instruction(language)}\n\n"

        "═══════════════════════════════════════════════════════\n"
        "ACTIVE PROJECT INVENTORY\n"
        "═══════════════════════════════════════════════════════\n"
        + (inventory_block if inventory_block else
           "⚠ No project inventory loaded for this city yet. "
           "Tell the agent which cities you do have data for if asked, and ask them to select an available city.")
    )
    # ─────────────────────────────────────────────────────────────────────────

    messages = [
        {"role": "system", "content": system_prompt},
        *recent_history,                              # previous turns
        {"role": "user", "content": transcript},     # current user message
    ]

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": DEEPSEEK_MAX_TOKENS,
        "temperature": DEEPSEEK_TEMPERATURE,
    }

    try:
        resp = _http_session.post(
            DEEPSEEK_BASE_URL,
            headers=headers,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"DeepSeek API error {resp.status_code}: {resp.text}")
            return build_real_estate_reply(transcript, resolved_city), "fallback"

        data = resp.json()
        reply = data["choices"][0]["message"]["content"].strip()
        model_used = data.get("model", DEEPSEEK_MODEL)
        log.info(
            "latency stage=llm total_ms=%.0f model=%s history_used=%d history_total=%d tokens=%d",
            (time.perf_counter() - started_at) * 1000,
            model_used,
            len(recent_history),
            len(history),
            DEEPSEEK_MAX_TOKENS,
        )
        return reply, model_used

    except Exception as exc:
        print(f"DeepSeek call failed: {exc} — using local fallback")
        return build_real_estate_reply(transcript, resolved_city), "fallback"
# ─────────────────────────────────────────────────────────────────────────────


def create_livekit_token(room_name: str, identity: str) -> str:
    api_key = require_env("LIVEKIT_API_KEY")
    api_secret = require_env("LIVEKIT_API_SECRET")
    now = int(time.time())
    payload = {
        "jti": str(uuid.uuid4()),
        "iss": api_key,
        "sub": identity,
        "nbf": now,
        "exp": now + 60 * 60,
        "name": identity,
        "video": {
            "roomJoin": True,
            "room": room_name,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    token = jwt.encode(payload, api_secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "livekit_configured": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
        "soravm_configured": bool(SORAVM_API_KEY),
        # "gemini_configured": bool(GEMINI_API_KEY),  # rolled back
        "deepseek_configured": bool(DEEPSEEK_API_KEY),
    }


@app.get("/config")
async def config():
    return {
        "livekit_url": LIVEKIT_URL,
        "livekit_configured": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
        "soravm_configured": bool(SORAVM_API_KEY),
        # "gemini_configured": bool(GEMINI_API_KEY),  # rolled back
        "deepseek_configured": bool(DEEPSEEK_API_KEY),
    }


@app.get("/livekit/token")
async def livekit_token(room: str, identity: str):
    if not room or not identity:
        raise HTTPException(status_code=400, detail="room and identity are required")

    token = create_livekit_token(room_name=room, identity=identity)
    return {
        "livekit_url": LIVEKIT_URL,
        "access_token": token,
        "room": room,
        "identity": identity,
    }


@app.post("/soravm/stt")
async def soravm_stt(
    audio_file: UploadFile = File(...),
    language_code: str = Form("unknown"),
    language: str | None = Form(None),
):
    # Accept both `language_code` (Sarvam) and legacy `language` from older clients.
    resolved_code = language_code if language_code else (language or "unknown")
    started_at = time.perf_counter()
    payload = await run_in_threadpool(transcribe_with_soravm, audio_file, resolved_code)
    log.info("latency stage=stt_endpoint total_ms=%.0f", (time.perf_counter() - started_at) * 1000)
    return payload


@app.post("/soravm/tts")
async def soravm_tts(
    text: str = Form(...),
    voice: str = Form("default"),
    target_language_code: str = Form("en-IN"),
):
    started_at = time.perf_counter()
    audio_base64, audio_mime_type = await run_in_threadpool(
        synthesize_sarvam_tts,
        text,
        target_language_code,
        voice,
    )
    log.info("latency stage=tts_endpoint total_ms=%.0f", (time.perf_counter() - started_at) * 1000)
    return {"audio_base64": audio_base64, "audio_mime_type": audio_mime_type}


@app.post("/voice/turn")
async def voice_turn(turn: VoiceTurnRequest):
    request_started_at = time.perf_counter()
    # Retrieve or create conversation history for this session
    session_id = turn.session_id or "default"
    history = conversation_store.setdefault(session_id, [])

    resolved_city = turn.city or detect_city_from_text(turn.transcript)
    resolved_language = resolve_response_language(turn.language, turn.transcript)

    # ── Active: DeepSeek (with conversation memory) ───────────────────────────
    llm_started_at = time.perf_counter()
    response_text, reply_source = await run_in_threadpool(
        generate_deepseek_reply,
        turn.transcript,
        history,
        resolved_city,
        resolved_language,
    )
    log.info("latency stage=voice_turn_llm total_ms=%.0f", (time.perf_counter() - llm_started_at) * 1000)
    # ── Rollback: swap the line above with the one below to revert to Gemini ──
    # response_text, reply_source = generate_gemini_reply(turn.transcript)
    # ─────────────────────────────────────────────────────────────────────────

    # Append this turn to the history and trim to the rolling window
    history.append({"role": "user",      "content": turn.transcript})
    history.append({"role": "assistant", "content": response_text})
    if len(history) > MAX_HISTORY_MESSAGES:
        # Drop oldest pairs from the front, keeping the most recent context
        excess = len(history) - MAX_HISTORY_MESSAGES
        del history[:excess]

    tts_started_at = time.perf_counter()
    audio_base64, audio_mime_type = await run_in_threadpool(
        synthesize_sarvam_tts,
        response_text,
        resolved_language,
        turn.voice,
    )
    log.info("latency stage=voice_turn_tts total_ms=%.0f", (time.perf_counter() - tts_started_at) * 1000)
    log.info("latency stage=voice_turn_total total_ms=%.0f", (time.perf_counter() - request_started_at) * 1000)

    return {
        "transcript": turn.transcript,
        "response_text": response_text,
        "reply_source": reply_source,
        "audio_base64": audio_base64,
        "audio_mime_type": audio_mime_type,
        "language": resolved_language,
        "voice": turn.voice,
    }


@app.post("/session/clear")
async def session_clear(session_id: str = "default"):
    """Wipe conversation history for the given session.
    Called by the frontend when the user clicks 'End Conversation'.
    """
    removed = session_id in conversation_store
    conversation_store.pop(session_id, None)
    print(f"Session cleared: {session_id} (existed={removed})")
    return {"cleared": removed, "session_id": session_id}
