import json
import os
from typing import Any, List
from urllib.request import Request, urlopen

import aiohttp
import docker
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI


def push_docker_image(
    DOCKER_HUB_USERNAME, APP_NAME, docker_client, image_full_name, image_tag
):
    """Pushes the Docker image to Docker Hub."""
    if not DOCKER_HUB_USERNAME:
        print("DOCKER_HUB_USERNAME environment variable not set. Skipping image push.")
        print(
            "This is usually fine for local Kubernetes (like Docker Desktop) if it can access local images."
        )
        return

    print(f"Pushing Docker image to Docker Hub: {image_full_name}:{image_tag}...")
    try:
        # Ensure the image is tagged correctly for Docker Hub
        local_image_name = f"{APP_NAME}:{image_tag}"
        if not any(
            tag.startswith(image_full_name)
            for tag in docker_client.images.get(local_image_name).tags
        ):
            docker_client.images.get(local_image_name).tag(
                f"{image_full_name}:{image_tag}"
            )

        for line in docker_client.images.push(
            f"{image_full_name}:{image_tag}", stream=True, decode=True
        ):
            if "stream" in line:
                print(line["stream"].strip())
            elif "status" in line:
                print(line["status"].strip())
        print(f"Successfully pushed Docker image: {image_full_name}:{image_tag}")
    except docker.errors.APIError as e:
        print(f"Error pushing Docker image: {e}")
        print("Ensure you are logged into Docker Hub: `docker login`")
        raise
    except Exception as e:
        print(f"An unexpected error occurred during Docker image push: {e}")
        raise


def load_llm_model(model_name, temperature=None):
    load_dotenv()
    """Load an OpenAI LLM client using the shared OPENAI_API_KEY configuration."""
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to load an OpenAI LLM model.")
    if not model_name:
        raise RuntimeError("model_name is required to load an OpenAI LLM model.")

    kwargs = {
        "model": model_name,
        "openai_api_key": openai_api_key,
        "openai_api_base": "https://inference.api.nscale.com/v1",
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatOpenAI(**kwargs)


def request_json(method, url, payload=None, timeout=10):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method.upper())
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw:
            return None
        return json.loads(raw)


async def request_json_async(
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: float = 10.0,
) -> Any | None:
    """Async HTTP client for JSON payloads, using aiohttp."""
    async with aiohttp.ClientSession() as session:
        headers = {"Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        async with session.request(
            method.upper(),
            url,
            data=data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            raw = await response.read()
            if not raw:
                return None
            return await response.json()


def content_to_text(content: Any) -> str:
    """
    Convert LLM content blocks into a plain text string.
    gemini-3.5-flash returns a list, not like gemini-2.5-flash which returns a string.
    """
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text_part = item.get("text")
                if isinstance(text_part, str):
                    parts.append(text_part)
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "".join(parts)

    return str(content)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from a model response, if present."""
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped

    lines = []
    for line in stripped.splitlines():
        if line.strip().startswith("```"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def parse_llm_json_payload(
    content: Any,
    *,
    expected_type: type | tuple[type, ...] = dict,
    error_prefix: str = "LLM response",
    allow_trailing_text: bool = True,
) -> Any:
    """Parse JSON from LLM content, tolerating code fences and optional trailing text.

    The parser handles common model outputs such as markdown fenced JSON, prose before
    the JSON object, and extra explanatory text after the first valid JSON value.
    Domain-specific schema validation should stay in the caller.
    """
    raw_text = content_to_text(content)
    cleaned = strip_code_fences(raw_text)
    if not cleaned:
        raise ValueError(
            f"{error_prefix} is empty after converting content to text. "
            f"Raw content type: {type(content).__name__}; raw content: {content!r}"
        )

    if expected_type is dict:
        json_start = cleaned.find("{")
    elif expected_type is list:
        json_start = cleaned.find("[")
    else:
        json_start_positions = [
            position
            for position in (cleaned.find("{"), cleaned.find("["))
            if position != -1
        ]
        json_start = min(json_start_positions) if json_start_positions else -1

    if json_start == -1:
        raise ValueError(
            f"{error_prefix} does not contain JSON. "
            f"Response preview: {cleaned[:1000]!r}"
        )
    json_text = cleaned[json_start:]
    try:
        if allow_trailing_text:
            parsed, _ = json.JSONDecoder().raw_decode(json_text)
        else:
            parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{error_prefix} is not valid JSON. "
            f"JSON error: {exc}. Response preview: {cleaned[:1000]!r}"
        ) from exc

    if expected_type is not None and not isinstance(parsed, expected_type):
        raise ValueError(
            f"{error_prefix} JSON must be {expected_type}. Parsed value: {parsed!r}"
        )
    return parsed
