"""
Build Semantic Index — called by the GitHub Actions workflow when the test
embedding cache is stale or missing.

Reads every test_*.py from /tmp/test-repo/tests/, parses each test function
via the Python AST module, embeds each function with sentence-transformers
(all-MiniLM-L6-v2, CPU-only, no API key), and writes the index to
/tmp/test-index/embeddings.json.

Index format:
{
  "model": "all-MiniLM-L6-v2",
  "chunks": [
    {
      "filename":      "test_products.py",
      "class_name":    "TestCreateProduct",   // empty string if top-level
      "function_name": "test_create_product_returns_201",
      "code":          "def test_create_product_returns_201(...):\n    ...",
      "embedding":     [0.023, -0.14, ...]
    },
    ...
  ]
}
"""
import ast
import json
import pathlib
import sys
import textwrap

MODEL_NAME  = "all-MiniLM-L6-v2"
TEST_DIR    = pathlib.Path("/tmp/test-repo/tests")
OUTPUT_DIR  = pathlib.Path("/tmp/test-index")
OUTPUT_FILE = OUTPUT_DIR / "embeddings.json"


# ── AST helpers ───────────────────────────────────────────────────────────────

def extract_chunks(source: str, filename: str) -> list[dict]:
    """
    Walk the AST of a test file and return one chunk per test function.
    Preserves class context so Claude sees e.g. class TestFoo > def test_bar.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"  Skipping {filename}: SyntaxError — {e}")
        return []

    chunks = []

    for node in ast.walk(tree):
        # Top-level test functions
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            chunks.append({
                "filename":      filename,
                "class_name":    "",
                "function_name": node.name,
                "code":          _extract_source(source, node),
            })

        # Test methods inside a class
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    chunks.append({
                        "filename":      filename,
                        "class_name":    node.name,
                        "function_name": item.name,
                        "code":          f"# class {node.name}\n" + _extract_source(source, item),
                    })

    return chunks


def _extract_source(source: str, node: ast.AST) -> str:
    """Extract and dedent the source lines for an AST node."""
    lines = source.splitlines()
    start = node.lineno - 1
    end   = node.end_lineno
    return textwrap.dedent("\n".join(lines[start:end]))


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_chunks(chunks: list[dict]) -> list[dict]:
    """Embed the code field of each chunk in-place and return the list."""
    from sentence_transformers import SentenceTransformer
    import numpy as np

    print(f"Loading model '{MODEL_NAME}'...")
    model = SentenceTransformer(MODEL_NAME)

    texts = [c["code"] for c in chunks]
    print(f"Embedding {len(texts)} test functions...")
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    for chunk, vec in zip(chunks, embeddings):
        chunk["embedding"] = vec.tolist()

    return chunks


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TEST_DIR.exists():
        print(f"ERROR: Test directory not found: {TEST_DIR}", file=sys.stderr)
        sys.exit(1)

    test_files = sorted(TEST_DIR.glob("test_*.py"))
    if not test_files:
        print("No test_*.py files found — writing empty index.")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps({"model": MODEL_NAME, "chunks": []}, indent=2))
        return

    print(f"Found {len(test_files)} test file(s): {[f.name for f in test_files]}")

    all_chunks = []
    for tf in test_files:
        source = tf.read_text()
        file_chunks = extract_chunks(source, tf.name)
        print(f"  {tf.name}: {len(file_chunks)} test function(s) extracted")
        all_chunks.extend(file_chunks)

    print(f"\nTotal chunks to embed: {len(all_chunks)}")
    all_chunks = embed_chunks(all_chunks)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index = {"model": MODEL_NAME, "chunks": all_chunks}
    OUTPUT_FILE.write_text(json.dumps(index, indent=2))
    print(f"\nIndex written to {OUTPUT_FILE}  ({OUTPUT_FILE.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
