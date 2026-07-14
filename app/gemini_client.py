"""Gemini API client.

Initialized with GEMINI_API_KEY from the environment/config and exposes
an async method for structured analysis.
"""
from __future__ import annotations

import google.generativeai as genai

from .config import get_gemini_api_key
from .models import AggregateOutput, AnalysisOutput


async def analyze_conversation_async(prompt: str) -> AnalysisOutput:
    """Send the transcript prompt to gemini-1.5-flash.

    Uses response_schema to receive structured JSON matching the AnalysisOutput model.
    """
    api_key = get_gemini_api_key()
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel("gemini-1.5-flash")
    response = await model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=AnalysisOutput,
        ),
    )

    # Directly validate the JSON string returned by the model
    return AnalysisOutput.model_validate_json(response.text)


async def synthesize_profile_async(prompt: str) -> AggregateOutput:
    """Send the aggregate prompt to gemini-1.5-pro.

    Uses response_schema to receive structured JSON matching the AggregateOutput model.
    """
    api_key = get_gemini_api_key()
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel("gemini-1.5-pro")
    response = await model.generate_content_async(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=AggregateOutput,
        ),
    )

    # Directly validate the JSON string returned by the model
    return AggregateOutput.model_validate_json(response.text)
