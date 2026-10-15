"""The chat models a user may pick from.

Kept free of heavy imports on purpose: backend.core builds BM25 and opens
Pinecone at import time, but the server needs this list to answer
GET /api/models and to reject an unknown id before core is ever loaded.

Only ids listed here ever reach init_chat_model, so a client cannot choose a
model (and a bill) that is not on this list.

The 5.6 models reject function tools on Chat Completions unless
reasoning_effort is "none", so every entry carries it. All three still return
plain string content, which run_llm and server.py assume.
"""

from typing import Any, Dict, List, Optional

DEFAULT_MODEL = "gpt-5.6-luna"

# Order is the order shown in the picker: cheapest first.
MODELS: Dict[str, Dict[str, Any]] = {
    "gpt-5.6-luna": {
        "label": "GPT-5.6 Luna",
        "note": "fastest and cheapest",
        "kwargs": {"reasoning_effort": "none"},
    },
    "gpt-5.6-terra": {
        "label": "GPT-5.6 Terra",
        "note": "balanced",
        "kwargs": {"reasoning_effort": "none"},
    },
    "gpt-5.6-sol": {
        "label": "GPT-5.6 Sol",
        "note": "most capable, slowest and priciest",
        "kwargs": {"reasoning_effort": "none"},
    },
}


def resolve(model_id: Optional[str]) -> str:
    """The id to use: the default when none was asked for, else a listed id.

    Raises ValueError for anything not on the list.
    """
    if not model_id:
        return DEFAULT_MODEL
    if model_id not in MODELS:
        raise ValueError(f"Unknown model: {model_id}")
    return model_id


def public_list() -> List[Dict[str, str]]:
    return [{"id": mid, "label": m["label"], "note": m["note"]} for mid, m in MODELS.items()]
