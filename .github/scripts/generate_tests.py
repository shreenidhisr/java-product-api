"""
AI Test Generator — called by the GitHub Actions workflow.

1. Reads the PR diff from stdin (or /tmp/pr.diff).
2. Loads the embedding index from /tmp/test-index/embeddings.json.
3. Embeds a compact diff query and retrieves the top-K most semantically
   similar test functions via cosine similarity.
4. Passes only those relevant test functions to Claude as style examples.
5. Writes the generated test file to /tmp/generated_test.py.

Sets GITHUB_OUTPUT variables:
  - test_file_name: filename to use in the test repo (empty string = skip)
  - test_action:    "create" or "update"
"""
import json
import os
import pathlib
import re
import sys

from google import genai
from google.genai import types as genai_types
import numpy as np

TOP_K         = 5
INDEX_PATH    = pathlib.Path("/tmp/test-index/embeddings.json")
OUTPUT_PATH   = pathlib.Path("/tmp/generated_test.py")
GITHUB_OUTPUT = os.environ.get("GITHUB_OUTPUT", "/dev/null")
PR_NUMBER     = os.environ["PR_NUMBER"]
PR_TITLE      = os.environ.get("PR_TITLE", "")


# ── 1. Read PR diff ────────────────────────────────────────────────────────────

diff_content = sys.stdin.read().strip()
if not diff_content:
    diff_file = pathlib.Path("/tmp/pr.diff")
    if diff_file.exists():
        diff_content = diff_file.read_text().strip()

if not diff_content:
    print("ERROR: No diff content found.", file=sys.stderr)
    sys.exit(1)


# ── 2. Load the embedding index ────────────────────────────────────────────────

if not INDEX_PATH.exists():
    print(f"ERROR: Index not found at {INDEX_PATH}. Was build_index.py run?", file=sys.stderr)
    sys.exit(1)

index = json.loads(INDEX_PATH.read_text())
chunks = index.get("chunks", [])
print(f"Loaded index: {len(chunks)} test function(s) from model '{index.get('model')}'")

all_filenames = sorted({c["filename"] for c in chunks})


# ── 3. Semantic search — top-K most relevant test functions ────────────────────

def build_diff_query(diff: str) -> str:
    """
    Extract the added/modified lines from the diff and join them into a
    compact prose query that captures the intent of the change.
    """
    added_lines = []
    for line in diff.splitlines():
        # Capture added lines; skip file header lines (+++/---)
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:].strip())

    # Keep non-empty, non-import, non-brace-only lines
    meaningful = [
        l for l in added_lines
        if l and not l.startswith("import ") and l not in ("{", "}", "*/", "/*")
    ]
    query = " ".join(meaningful[:120])  # cap to avoid huge queries
    return query or diff[:500]          # fallback: first 500 chars of raw diff


def cosine_search(query_vec: np.ndarray, chunks: list[dict], top_k: int) -> list[dict]:
    """Return the top_k chunks sorted by cosine similarity (vectors pre-normalised)."""
    if not chunks:
        return []
    matrix = np.array([c["embedding"] for c in chunks], dtype=np.float32)
    scores = matrix @ query_vec          # dot product = cosine sim (normalised)
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [
        {**chunks[i], "score": float(scores[i])}
        for i in top_indices
    ]


if chunks:
    from sentence_transformers import SentenceTransformer
    model_name = index.get("model", "all-MiniLM-L6-v2")
    print(f"Loading embedding model '{model_name}' for query...")
    embed_model = SentenceTransformer(model_name)

    query_text = build_diff_query(diff_content)
    print(f"Query text (first 200 chars): {query_text[:200]}")

    query_vec = embed_model.encode(query_text, normalize_embeddings=True)
    top_chunks = cosine_search(query_vec, chunks, TOP_K)

    print(f"\nTop-{TOP_K} most relevant test functions:")
    for c in top_chunks:
        label = f"{c['class_name']}::{c['function_name']}" if c["class_name"] else c["function_name"]
        print(f"  [{c['score']:.3f}] {c['filename']} — {label}")
else:
    print("Index is empty — no existing tests to use as examples.")
    top_chunks = []


# ── 4. Build the Claude prompt ─────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior QA engineer specialising in end-to-end API testing with pytest.

Your job is to analyse a Git diff from a Java Spring Boot REST API and either:
  (a) CREATE a new pytest test file covering new behaviour introduced by the diff, OR
  (b) UPDATE an existing test file if the diff modifies behaviour that is already tested.

Rules:
1. Follow the EXACT same style, structure and fixture usage as the example tests provided.
2. Use the `base_url`, `create_product`, and `cleanup_products` fixtures already defined in conftest.py.
3. Tests must be self-contained and clean up after themselves via the `create_product` fixture.
4. Group related tests inside a class (e.g. `class TestDescriptionField:`).
5. Every assertion must be meaningful — test actual values, not just status codes.
6. If the diff adds a new field, test: creating with it, reading it back, updating it, and omitting it.
7. If the diff adds a new endpoint or route, test: happy path, 404, and validation errors.
8. If an existing test file is provided below (under "EXISTING FILE CONTENT"), you MUST include
   ALL of its tests verbatim in your output — do NOT remove, rename, or rewrite any existing test.
   Only APPEND new test classes or functions. The output must be the existing content PLUS new tests.
9. If the diff is a refactor with no observable API change, output SKIP.
10. If the diff is infrastructure/config only, output SKIP.
11. CRITICAL: Never use `self` as a parameter on standalone functions outside a class. Every test
    function outside a class must only take fixture names as parameters (e.g. `def test_foo(base_url):`).
12. CRITICAL: `cleanup_products` is a LIST, not a callable. Never write `cleanup_products(id)`.
    Always prefer the `create_product` fixture which handles cleanup automatically. If you must
    create a product manually, register it with `cleanup_products.append(data["id"])`.

Output format — respond with ONLY a JSON object, no markdown fences:
{
  "skip": false,
  "action": "create" | "update",
  "filename": "test_<snake_case_feature>.py",
  "code": "<full pytest file content as a string>"
}

Or if no tests are needed:
{
  "skip": true,
  "reason": "..."
}
"""

# Format the retrieved test snippets for the prompt
if top_chunks:
    examples_section = "Most relevant existing test functions (retrieved via semantic search):\n\n"
    for c in top_chunks:
        label = f"{c['class_name']}::{c['function_name']}" if c["class_name"] else c["function_name"]
        examples_section += f"# {c['filename']} — {label}  (similarity: {c['score']:.3f})\n"
        examples_section += f"```python\n{c['code']}\n```\n\n"
else:
    examples_section = "No existing tests found — write idiomatic pytest tests using requests.\n"

# If there is a single obvious existing file to update, read its full content so the
# AI can preserve every test in it (not just the top-K retrieved chunks).
TEST_REPO_DIR = pathlib.Path("/tmp/test-repo/tests")
existing_file_section = ""
if len(all_filenames) == 1:
    existing_path = TEST_REPO_DIR / all_filenames[0]
    if existing_path.exists():
        existing_file_section = f"""
### EXISTING FILE CONTENT — `{all_filenames[0]}` (you MUST keep ALL these tests)
```python
{existing_path.read_text()}
```
"""

USER_PROMPT = f"""## PR #{PR_NUMBER}: {PR_TITLE}

### Git Diff
```diff
{diff_content}
```

### All existing test filenames (for overlap / update detection)
{all_filenames if all_filenames else "None"}
{existing_file_section}
### Example tests (top-{TOP_K} most semantically similar to this diff)
{examples_section}
Decide whether to CREATE a new test file or UPDATE an existing one, then output the result.
"""

# ── 5. Call Gemini ─────────────────────────────────────────────────────────────

import time

# Try models in order of free-tier quota (highest RPD first)
CANDIDATE_MODELS = [
    "gemini-3.1-flash-lite-preview",  # 15 RPM / 500 RPD free
    "gemini-2.5-flash-lite",          # 10 RPM / 20 RPD free
    "gemini-2.5-flash",               # 5 RPM / 20 RPD free
    "gemini-flash-lite-latest",       # alias for latest flash lite
]

print("\nCalling Gemini API...")
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

raw_response = None
for model_id in CANDIDATE_MODELS:
    for attempt in range(1, 4):  # up to 3 retries per model
        try:
            print(f"  Trying model={model_id}, attempt={attempt}...")
            response = client.models.generate_content(
                model=model_id,
                contents=USER_PROMPT,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.2,
                    max_output_tokens=4096,
                ),
            )
            raw_response = response.text.strip()
            print(f"  Success with {model_id}")
            break
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait = 20 * attempt
                print(f"  Rate limited on {model_id} — waiting {wait}s before retry...")
                time.sleep(wait)
            elif "404" in err_str or "not found" in err_str.lower():
                print(f"  Model {model_id} not available, trying next...")
                break  # skip to next model
            else:
                print(f"  Unexpected error: {e}", file=sys.stderr)
                sys.exit(1)
    if raw_response:
        break

if not raw_response:
    print("ERROR: All Gemini models exhausted. Check your API key at https://aistudio.google.com/apikey", file=sys.stderr)
    sys.exit(1)
print(f"Claude response (first 300 chars):\n{raw_response[:300]}")

# Strip accidental markdown fences
raw_response = re.sub(r"^```(?:json)?\s*", "", raw_response)
raw_response = re.sub(r"\s*```$", "", raw_response)

try:
    result = json.loads(raw_response)
except json.JSONDecodeError as e:
    print(f"ERROR: Could not parse Claude response as JSON: {e}")
    print("Raw response:", raw_response)
    sys.exit(1)

# ── 6. Handle SKIP ────────────────────────────────────────────────────────────

if result.get("skip"):
    print(f"Claude says no tests needed: {result.get('reason', '')}")
    with open(GITHUB_OUTPUT, "a") as f:
        f.write("test_file_name=\n")
    sys.exit(0)

# ── 7. Write generated test file ──────────────────────────────────────────────

filename  = result["filename"]
test_code = result["code"]
action    = result.get("action", "create")

OUTPUT_PATH.write_text(test_code)

if action == "update":
    print(f"\nUpdating existing test file '{filename}' with new/changed coverage.")
else:
    print(f"\nCreating new test file '{filename}'.")

print("\n--- Generated test preview (first 800 chars) ---")
print(test_code[:800])

with open(GITHUB_OUTPUT, "a") as f:
    f.write(f"test_file_name={filename}\n")
    f.write(f"test_action={action}\n")
