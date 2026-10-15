import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.messages import ToolMessage
from langchain.tools import tool
from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_pinecone import PineconeVectorStore
from langchain_openai import OpenAIEmbeddings

from backend.models import MODELS, resolve as resolve_model
from logger import log_warning

load_dotenv()

# How many chunks each retriever pulls before the ensemble merges/dedupes them.
RETRIEVAL_K = 10

# MUST match ingestion.py exactly: query vectors and stored vectors have to come
# from the same model or similarity search is meaningless.
# Initialize embeddings (same as ingestion.py)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# Read-only handle on the index that ingestion.py populated.
#Initialize vector store
vectorstore = PineconeVectorStore(
    index_name="strudel-doc-index", embedding=embeddings
)
# The chat model is chosen per request from the allowlist in models.py. Instances are
# built lazily and cached, one per id.
#
# Benchmarked on this app's own melody task (five runs each through the full
# retrieval pipeline, scoring whether the transcribed melody survives verbatim,
# `.slow(n)` is kept, no invalid variable assignment appears, and no unloaded
# soundfont is used):
#
#     gpt-5.4-mini   5/5 clean    4.5s   (2026-09-12; 11.0s on 2026-10-14's 4-cycle hum)
#     gpt-5.5        5/5 clean   12.6s
#     gpt-6-astra    5/5 clean   10.1s
#     gpt-5.6-sol    5/5 clean   24.3s
#     gpt-4o-mini    1/5 clean    5.2s   - dropped `.slow(2)` in four runs
#     gpt-5.6-luna   5/5 clean    9.5s   (2026-10-14, 4-cycle hum; $0.20 / $1.20 per M tokens)
#
# The 5.6 models reject function tools on Chat Completions unless reasoning_effort is
# "none" (models.py sets it); with that they return plain string content, so run_llm and
# server.py need no change. An earlier note here said Sol needed the Responses API. It
# did not: the default reasoning setting was what it rejected.
#
# max_retries: the OpenAI client retries 429s and 5xx with exponential backoff, honouring
# Retry-After, but defaults to 2 attempts. 5 rides out a short rate-limit window (about
# 15s of waiting) before the request fails.
_models: Dict[str, Any] = {}
_models_lock = threading.Lock()


def get_model(model_id: Optional[str] = None):
    """The chat model for an allowlisted id (the default when None).

    Raises ValueError for an id that is not on the list.
    """
    model_id = resolve_model(model_id)
    if model_id not in _models:
        with _models_lock:
            if model_id not in _models:
                _models[model_id] = init_chat_model(
                    model_id,
                    model_provider="openai",
                    max_retries=5,
                    **MODELS[model_id]["kwargs"],
                )
    return _models[model_id]


# Chunk dump written by ingestion.py (app/ -> project root -> data/chunks.json).
CHUNKS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "chunks.json"


def _load_local_chunks() -> List[Document]:
    if not CHUNKS_PATH.exists():
        return []
    raw = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    return [Document(page_content=item["page_content"], metadata=item["metadata"]) for item in raw]


def _build_retriever():
    """Hybrid retriever: BM25 (lexical) + Pinecone (semantic), merged by
    EnsembleRetriever (reciprocal rank fusion). BM25 catches exact
    keyword/function-name matches ("lpf", "gain") that dense embeddings alone
    sometimes rank low; the vector side catches paraphrases/synonyms BM25 can't
    see. Falls back to vector-only if ingestion hasn't been run yet (no local
    chunk dump to build BM25 from).
    """
    vector_retriever = vectorstore.as_retriever(search_kwargs={"k": RETRIEVAL_K})

    local_chunks = _load_local_chunks()
    if not local_chunks:
        log_warning(
            f"No local chunk dump found at {CHUNKS_PATH}: falling back to vector-only retrieval. "
            "Run `python app/ingestion.py` to enable hybrid BM25 + vector search."
        )
        return vector_retriever

    bm25_retriever = BM25Retriever.from_documents(local_chunks)
    bm25_retriever.k = RETRIEVAL_K

    return EnsembleRetriever(retrievers=[bm25_retriever, vector_retriever], weights=[0.5, 0.5])


retriever = _build_retriever()


# response_format="content_and_artifact" makes the tool return a 2-tuple:
#   [0] content  -> the string the MODEL sees (must be text)
#   [1] artifact -> an arbitrary Python object the model never sees, stored on
#                   the ToolMessage for the application to use
# That is how the raw Document objects survive the round-trip and reach the UI,
# instead of being flattened into the prompt string.
@tool(response_format="content_and_artifact")
def retrieve_context(query: str):
    """Retrieve relevant documentation to help answer user queries about Strudel REPL."""
    # Hybrid BM25 + vector retrieval (see _build_retriever above), k=RETRIEVAL_K
    # actually applied via search_kwargs/bm25_retriever.k, not passed to invoke().
    retrieved_docs = retriever.invoke(query)

    # The source URL is embedded in the text the model reads, which is what makes
    # the "always cite the sources" instruction in the system prompt satisfiable.
    # Serialize documents for the model
    serialized = "\n\n".join(
        (f"Source: {doc.metadata.get('source', 'Unknown')}\n\nContent: {doc.page_content}")
        for doc in retrieved_docs
    )
    
    # Return both serialized content and raw documents
    return serialized, retrieved_docs


# app/backend/ -> app/ -> project root -> sandbox/
SANDBOX_DIR = Path(__file__).resolve().parent.parent.parent / "sandbox"
SANDBOX_ENTRY = SANDBOX_DIR / "validate.mjs"

# A query over four cycles is arithmetic; a second is already generous. The
# ceiling exists for the pattern that loops forever, not for the slow one.
SANDBOX_TIMEOUT_SEC = 20

# Node's permission model, verified against this sandbox: without these flags
# evaluated code writes to the filesystem and opens sockets freely, and with
# them filesystem writes, network and subprocesses are all denied. Read access
# is granted only to the sandbox directory, which is where its own modules
# live. See docs/humming.md.
SANDBOX_FLAGS = ["--permission", f"--allow-fs-read={SANDBOX_DIR}"]


def _sandbox_problem() -> Optional[str]:
    """Why the validator can't run, or None when it can."""
    if not shutil.which("node"):
        return "Node.js is not installed"
    if not SANDBOX_ENTRY.exists() or not (SANDBOX_DIR / "node_modules").exists():
        return "the sandbox is not installed (run `npm install` in sandbox/)"
    return None


@tool
def validate_pattern(code: str) -> str:
    """Run a Strudel pattern and report whether it works.

    Use this on every pattern you write, before giving it to the user. It
    evaluates the code the way the Strudel REPL would and returns either what
    the pattern does (how many events, which sounds, which notes) or the exact
    error. If it reports an error or a warning, fix the pattern and validate
    again. If it says a function is not defined, search the documentation for
    the right name rather than guessing.

    Args:
        code: The complete pattern, exactly as you would give it to the user.
    """
    problem = _sandbox_problem()
    if problem:
        return (
            f"The validator is unavailable: {problem}. "
            "Answer from the documentation alone and say the pattern is unverified."
        )

    try:
        finished = subprocess.run(
            ["node", *SANDBOX_FLAGS, str(SANDBOX_ENTRY)],
            input=code,
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT_SEC,
            cwd=SANDBOX_DIR,
        )
    except subprocess.TimeoutExpired:
        return (
            f"The pattern did not finish within {SANDBOX_TIMEOUT_SEC} seconds. "
            "Something in it loops forever. Simplify it and try again."
        )

    try:
        report = json.loads(finished.stdout)
    except json.JSONDecodeError:
        # The sandbox writes JSON on stdout and everything else on stderr, so
        # this means it died before it could report - a crash, not a bad pattern.
        detail = (finished.stderr or "").strip().splitlines()
        return "The validator crashed: " + (detail[-1] if detail else "no output.")

    if not report.get("ok"):
        return (
            f"INVALID ({report.get('stage', 'unknown')} stage): {report.get('error')}\n"
            "Fix the pattern and validate it again."
        )

    lines = [
        f"VALID. Over {report['cycles_queried']} cycles it produces "
        f"{report['events']} events."
    ]
    if report.get("sounds"):
        lines.append(f"Sounds used: {', '.join(report['sounds'])}.")
    if report.get("distinct_notes"):
        lines.append(f"Distinct notes: {report['distinct_notes']}.")
    if report.get("cpm") is not None:
        lines.append(f"Tempo: {report['cpm']} cycles per minute.")
    for warning in report.get("warnings", []):
        lines.append(f"WARNING: {warning} Fix this and validate again.")
    return " ".join(lines)


# Said once, when the server first loads this module (at startup, via the prewarm
# thread), so a missing sandbox shows in the log instead of only in answers.
_startup_problem = _sandbox_problem()
if _startup_problem:
    log_warning(
        f"Pattern validator disabled: {_startup_problem}. "
        "The agent will answer without checking its patterns."
    )


def run_llm(
    query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the RAG pipeline to answer a query using retrieved documentation.

    Args:
        query: The user's question
        chat_history: Prior turns as [{"role": "user"|"assistant", "content": ...}, ...],
            oldest first, NOT including `query` itself. Lets the agent resolve
            follow-ups ("and how do I add reverb to that?") against what was
            already discussed - without it every question was answered in
            isolation, so the retriever had no way to know what "that" referred to.
        model_id: An id from backend.models.MODELS; the default when omitted.

    Returns:
        Dictionary containing:
            - answer: The generated answer
            - context: List of retrieved documents
    """
    # Create the agent with retrieval tool
    system_prompt = (
        "You are a knowledgeable assistant for Strudel (https://strudel.cc), a JavaScript live-coding "
        "environment for algorithmic music (a port of TidalCycles). Users ask how to write, fix, or "
        "understand Strudel patterns, and what specific functions do.\n\n"
        "The `retrieve_context` tool searches a documentation index built from two different sources, "
        "which the `Source:` field of each retrieved chunk lets you tell apart:\n"
        "1. Tutorial/concept pages (strudel.cc/workshop, /learn, /recipes) - prose explanations of "
        "concepts such as mini-notation, effect chains, or arrangement.\n"
        "2. API reference entries - one per Strudel function (e.g. `lpf`, `euclid`, `room`), each with "
        "its description, parameters, synonyms, and runnable examples, extracted directly from that "
        "function's JSDoc comment in the Strudel source code (codeberg.org/uzu/strudel). These are the "
        "ground truth for exact syntax, parameter types, and synonyms (e.g. `cutoff`/`ctf`/`lp` are all "
        "synonyms of `lpf`) - trust them over your own prior knowledge of Strudel/TidalCycles, which may "
        "be outdated or refer to a different version.\n\n"
        "You have two tools and they are meant to be used in a loop, not once "
        "each. `retrieve_context` is how you learn; `validate_pattern` is how "
        "you check what you wrote. A typical turn looks like: search the docs, "
        "write a pattern, validate it, read the error, search for the function "
        "you got wrong, fix it, validate again. Keep going until it comes back "
        "VALID with no warnings. Do not present a pattern you have not "
        "validated, and never argue with the validator - it runs the real "
        "Strudel engine, so if it says a function does not exist, it does not "
        "exist, whatever you remember.\n\n"
        "You handle two kinds of requests:\n"
        "- Questions/explanations (\"what does lpf do?\", \"how does mini-notation work?\") -> answer in "
        "prose, citing sources.\n"
        "- Code creation or refinement (\"give me a techno bassline\", \"add a reverb to this\", or a "
        "message that includes the user's current pattern and asks to change it) -> retrieve context for "
        "every function/effect you intend to use, then reply with the COMPLETE resulting pattern in a "
        "single fenced code block (not a diff or fragment, so it can be copy-pasted straight into the "
        "Strudel REPL), followed by a short bullet list of what you changed or why you wrote it that way. "
        "If the user supplied their own pattern, preserve everything they didn't ask you to change.\n\n"
        "Rules:\n"
        "- Always call `retrieve_context` before answering any question about Strudel syntax, functions, "
        "or concepts, and before writing or modifying any pattern - never rely on memory alone.\n"
        "- Always call `validate_pattern` on the complete pattern before you present it, and again after "
        "every change you make to it. If it reports INVALID or a WARNING, that is not advice - fix it and "
        "re-validate. Give up only after three failed attempts, and then say plainly what the validator "
        "reported rather than presenting the broken pattern as if it worked.\n"
        "- If the first retrieval doesn't clearly answer the question, call the tool again with a "
        "rephrased query, a likely function name, or a synonym, before giving up.\n"
        "- Use only functions and syntax that appear in the retrieved documentation, written as valid "
        "Strudel mini-notation. Never invent a function name, parameter, or syntax you haven't seen "
        "retrieved - if you're not sure something exists, search for it first.\n"
        "- Always cite the sources you used, listing the `Source:` URL(s) from the retrieved context at "
        "the end of your answer.\n"
        "- If the retrieved documentation doesn't cover the request, say so explicitly rather than "
        "guessing or inventing function names or parameters.\n"
        "- Keep answers concise and practical, favoring a runnable example over a long explanation."
    )
    
    # A fresh agent per call. In production build it once at module level - this
    # rebuilds the graph on every request.
    agent = create_agent(
        get_model(model_id),
        tools=[retrieve_context, validate_pattern],
        system_prompt=system_prompt,
    )
    
    # Prior turns first (only role/content survive - the UI-only "sources" key
    # main.py stores alongside each message must not leak into the agent
    # input), then the current question last.
    messages = [{"role": m["role"], "content": m["content"]} for m in (chat_history or [])]
    messages.append({"role": "user", "content": query})
    
    # Invoke the agent
    response = agent.invoke(cast(Any, {"messages": messages}))
    
    # The loop has ended, so the last message is the AI's plain-text answer.
    # Extract the answer from the last AI message
    answer = response["messages"][-1].content
    
    # Walk the transcript and collect every retrieved Document. There may be
    # several ToolMessages if the agent searched more than once.
    # Extract context documents from ToolMessage artifacts
    context_docs = []
    for message in response["messages"]:
        # Check if this is a ToolMessage with artifact
        if isinstance(message, ToolMessage) and hasattr(message, "artifact"):
            # The artifact should contain the list of Document objects
            if isinstance(message.artifact, list):
                context_docs.extend(message.artifact)
    
    return {
        "answer": answer,
        "context": context_docs
    }

if __name__ == '__main__':
    result = run_llm(query="what are deep agents?")
    print(result)