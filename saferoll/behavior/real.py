"""Original fixed prompts and exact normalization for real inference probes."""
from __future__ import annotations
import hashlib
import json
import re
from typing import Any

PROMPT_TEMPLATE_VERSION = "real-workload-v1"
CLASSIFICATION_TEMPLATE = "Classify the parity of the integer in this record. Reply with exactly EVEN or ODD, and nothing else.\nRecord: {text}"
STRUCTURED_TEMPLATE = "Extract the integer and parity from this record. Reply with only a JSON object with exactly the keys number (integer) and parity (string).\nRecord: {text}"
WEAKENED_TEMPLATE = "Read this record and answer in a short English sentence, without JSON: {text}"


def prompt_for_item(item: dict[str, Any], variant: str) -> str:
    if variant == "weakened_json":
        if item["task_type"] != "structured_output":
            raise ValueError("weakened_json requires structured_output")
        template = WEAKENED_TEMPLATE
    elif variant == "standard":
        template = CLASSIFICATION_TEMPLATE if item["task_type"] == "classification" else STRUCTURED_TEMPLATE
    else:
        raise ValueError("unsupported prompt variant")
    return template.format(text=item["input"]["text"])


def serving_prompt(seed: int, request_index: int, task_type: str, variant: str) -> str:
    number = seed + request_index
    label = 'even' if number % 2 == 0 else 'odd'
    item = {'task_type': task_type, 'input': {'text': f'Record {number} has parity {label}.'}}
    return prompt_for_item(item, variant)


def prompt_template_sha256() -> str:
    value = json.dumps([PROMPT_TEMPLATE_VERSION, CLASSIFICATION_TEMPLATE, STRUCTURED_TEMPLATE,
                        WEAKENED_TEMPLATE], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_output(task_type: str, raw: str) -> str:
    """Classification must contain one whole label; JSON admits only a whole fenced object."""
    text = raw.strip()
    if task_type == "classification":
        cleaned = text.strip(" \t\r\n.,!?;:*").lower()
        return cleaned if cleaned in {"even", "odd"} else ""
    if task_type != "structured_output":
        raise ValueError("unsupported task type")
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        return text
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
