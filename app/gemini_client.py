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


