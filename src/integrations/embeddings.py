"""
Turns text into a stored embedding and back, for semantic search over saved
links and conversation history.

Uses Gemini's embedding API (via gemini.embed_content) rather than a local
model - avoids installing a heavy ML runtime (e.g. sentence-transformers pulls
in PyTorch, ~1-2GB) on a home server that already runs several other services,
and this project already has Gemini billing enabled for an unrelated reason
(PRD 14.3). Vectors are stored as raw float32 bytes via the stdlib `array`
module rather than pulling in numpy - brute-force cosine similarity over a few
hundred rows in pure Python is fast enough at personal scale.
"""
import math
from array import array

from src.integrations.gemini import embed_content


def embed_text(text: str) -> bytes:
    """Returns the embedding for a piece of text, serialized as float32 bytes."""
    vector = embed_content(text)
    return array("f", vector).tobytes()


def cosine_similarity(a: bytes, b: bytes) -> float:
    """Cosine similarity between two stored embeddings. Returns 0.0 if either
    vector is degenerate (all zeros) rather than dividing by zero."""
    va = array("f")
    va.frombytes(a)
    vb = array("f")
    vb.frombytes(b)

    dot = sum(x * y for x, y in zip(va, vb))
    norm_a = math.sqrt(sum(x * x for x in va))
    norm_b = math.sqrt(sum(x * x for x in vb))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
