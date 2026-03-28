"""
Filler clip management for Operator.

Pre-generated audio clips are played while LLM + TTS synthesis runs,
bridging the silence between the user's prompt and Operator's response.

Clips live in assets/fillers/{bucket}/ and are generated offline via
scripts/gen_fillers.py. If a bucket has no clips, fillers are silently skipped
and the pipeline falls back to silent waiting (current behaviour).
"""
import os
import random
import logging

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILLERS_DIR = os.path.join(_BASE, "assets", "fillers")

# Keywords that route to each bucket.  Checked in order: empathetic first,
# then cerebral, then neutral as the default.
_EMPATHETIC_KEYWORDS = {
    "feel", "felt", "feeling", "hard", "difficult", "struggling", "worried",
    "anxious", "frustrated", "confused", "lost", "scared", "nervous", "upset",
    "honest", "honestly", "stress", "stressed", "overwhelming", "overwhelmed",
    "afraid", "fear", "hurt", "pain", "tired", "exhausted",
}
_CEREBRAL_KEYWORDS = {
    "what", "why", "how", "which", "explain", "difference", "better", "should",
    "compare", "versus", "recommend", "suggest", "think", "opinion",
    "best", "worst", "pros", "cons", "tradeoff", "strategy", "approach",
    "analyze", "understand", "mean", "means", "when", "where", "whether",
}


def classify(text: str) -> str:
    """Return 'empathetic', 'cerebral', or 'neutral' based on prompt keywords."""
    words = {w.strip(".,!?;:\"'()") for w in text.lower().split()}
    if words & _EMPATHETIC_KEYWORDS:
        return "empathetic"
    if words & _CEREBRAL_KEYWORDS:
        return "cerebral"
    return "neutral"


def get_clips(bucket: str) -> list:
    """Return a shuffled list of clip paths for the given bucket.

    Falls back to neutral if the requested bucket has no clips.
    Returns empty list if no clips exist at all.
    """
    clips = _load_bucket(bucket)
    if not clips and bucket != "neutral":
        clips = _load_bucket("neutral")
    return clips


def _load_bucket(bucket: str) -> list:
    bucket_dir = os.path.join(FILLERS_DIR, bucket)
    if not os.path.isdir(bucket_dir):
        return []
    clips = [
        os.path.join(bucket_dir, f)
        for f in sorted(os.listdir(bucket_dir))
        if f.endswith(".mp3")
    ]
    random.shuffle(clips)
    return clips
