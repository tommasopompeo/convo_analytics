"""Gemini API client.

Initialized with GEMINI_API_KEY from the environment/config and exposes
an async method for structured analysis.
"""
from __future__ import annotations

from typing import Any
import google.generativeai as genai

from .config import get_gemini_api_key, GEMINI_ANALYSIS_MODEL, GEMINI_SYNTHESIS_MODEL
from .models import AggregateOutput, AnalysisOutput


def _get_clean_schema(model_cls: type) -> dict[str, Any]:
    """Generate OpenAPI schema for a Pydantic model, dereference $defs, and strip 'default' and 'title' keys.

    Preserves the 'required' list (which is stripped by the SDK's default builder) so Gemini
    is forced to output all required properties, preventing Pydantic validation errors.
    """
    schema = model_cls.model_json_schema()
    defs = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].split("/")[-1]
                ref_schema = dict(defs[ref_name])
                return clean(ref_schema)

            cleaned = {}
            for k, v in node.items():
                if k in ("default", "title"):
                    continue
                cleaned[k] = clean(v)
            return cleaned
        elif isinstance(node, list):
            return [clean(item) for item in node]
        return node

    return clean(schema)



async def analyze_conversation_async(prompt: str) -> AnalysisOutput:
    """Send the transcript prompt to the configured analysis model.

    Uses response_schema to receive structured JSON matching the AnalysisOutput model.
    """
    api_key = get_gemini_api_key()
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(GEMINI_ANALYSIS_MODEL)
    response = await model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=_get_clean_schema(AnalysisOutput),
        ),
    )

    # Directly validate the JSON string returned by the model
    return AnalysisOutput.model_validate_json(response.text)


async def synthesize_profile_async(prompt: str) -> AggregateOutput:
    """Send the aggregate prompt to the configured synthesis model.

    Uses response_schema to receive structured JSON matching the AggregateOutput model.
    """
    api_key = get_gemini_api_key()
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(GEMINI_SYNTHESIS_MODEL)
    response = await model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=_get_clean_schema(AggregateOutput),
        ),
    )

    # Directly validate the JSON string returned by the model
    return AggregateOutput.model_validate_json(response.text)


async def chat_with_persona_async(
    chat_history: list[dict[str, str]],
    user_message: str,
    profile_data: dict[str, Any],
    search_enabled: bool = False
) -> dict[str, Any]:
    """Send the user message and history to the model with the digital twin system prompt.

    Enables Google Search Grounding if configured. Extracts source links/titles if present.
    """
    api_key = get_gemini_api_key()
    genai.configure(api_key=api_key)

    profile_str = ""
    if profile_data:
        profile_str = f"""
Unified User Profile (Digital Twin Source):
- Who I am: {profile_data.get('who_i_am', '')}
- Current Issues & Concerns: {", ".join(profile_data.get('current_issues', [])) if isinstance(profile_data.get('current_issues'), list) else profile_data.get('current_issues', '')}
- Recurrent Topics: {", ".join(profile_data.get('recurrent_topics', [])) if isinstance(profile_data.get('recurrent_topics'), list) else profile_data.get('recurrent_topics', '')}
- Strong Opinions & Stances: {", ".join(profile_data.get('strong_opinions', [])) if isinstance(profile_data.get('strong_opinions'), list) else profile_data.get('strong_opinions', '')}
- Tone & Sentiment: {profile_data.get('tone_and_sentiment', '')}
"""

    system_instruction = f"""You are the digital twin of the user. You have access to their unified profile. Help them think through problems, reflect, and analyze their thoughts. Speak to them as a trusted, objective peer.

Here is the user's unified profile data for context:
{profile_str}
"""

    tools = [{"google_search": {}}] if search_enabled else None

    # Construct GenerativeModel
    model = genai.GenerativeModel(
        model_name=GEMINI_ANALYSIS_MODEL,
        system_instruction=system_instruction,
        tools=tools
    )

    # Format historical chat messages + current user query
    contents = []
    for msg in chat_history:
        role = "user" if msg.get("role") == "user" else "model"
        contents.append({
            "role": role,
            "parts": [msg.get("content", "")]
        })
    contents.append({
        "role": "user",
        "parts": [user_message]
    })

    response = await model.generate_content_async(contents)

    try:
        content = response.text
    except Exception:
        content = "I couldn't generate a reflection. Please try another prompt."

    sources = []
    try:
        if response.candidates and response.candidates[0].grounding_metadata:
            metadata = response.candidates[0].grounding_metadata
            if hasattr(metadata, "grounding_chunks") and metadata.grounding_chunks:
                for chunk in metadata.grounding_chunks:
                    if hasattr(chunk, "web") and chunk.web:
                        title = getattr(chunk.web, "title", "")
                        uri = getattr(chunk.web, "uri", "")
                        if uri:
                            sources.append({"title": title or uri, "url": uri})
    except Exception as e:
        print(f"Error parsing grounding metadata: {e}")

    return {
        "content": content,
        "sources": sources
    }



