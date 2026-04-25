"""Whimsical present-participle verbs used as tool-execution heartbeats.

The pool is shuffled into a queue; each `pick()` pops the next word and
refills the queue when empty (no immediate repeats across the seam). The
picker is process-global and thread-safe — multiple ChatRunners share one
non-repeating stream.
"""
import random
import threading

VERBS = [
    "Flambéing", "Simmering", "Sautéing", "Braising", "Whisking",
    "Marinating", "Glazing", "Caramelizing", "Roasting", "Steeping",
    "Brining", "Kneading", "Reducing", "Folding", "Emulsifying",
    "Plating", "Garnishing", "Searing", "Poaching", "Tempering",
    "Proofing", "Drizzling", "Basting", "Curing", "Frosting",
    "Macerating", "Pickling", "Whittling", "Forging", "Tinkering",
    "Soldering", "Sanding", "Lacquering", "Polishing", "Etching",
    "Embossing", "Riveting", "Annealing", "Quilting", "Crocheting",
    "Weaving", "Cobbling", "Smelting", "Sashaying", "Frolicking",
    "Galloping", "Bounding", "Strutting", "Shuffling", "Pirouetting",
    "Scurrying", "Loping", "Cavorting", "Gambolling", "Prancing",
    "Conjuring", "Bamboozling", "Hornswoggling", "Discombobulating", "Befuddling",
    "Flummoxing", "Wrangling", "Untangling", "Bedazzling", "Bewitching",
    "Levitating", "Materializing", "Transmuting", "Alchemizing", "Burrowing",
    "Sprouting", "Blossoming", "Foraging", "Germinating", "Pollinating",
    "Doodling", "Noodling", "Jiggering", "Finagling", "Schmoozing",
    "Faffing", "Dawdling", "Pondering", "Mulling", "Ruminating",
    "Cogitating", "Percolating", "Brewing", "Stewing", "Perusing",
    "Rummaging", "Sleuthing", "Snooping", "Bunning", "Hodgepodging",
    "Whirligigging", "Kerfuffling", "Lollygagging", "Skedaddling", "Moseying",
    "Puttering", "Tinkering anew", "Spelunking",
    # Productive vibe — focused, getting-things-done
    "Drafting", "Compiling", "Assembling", "Synthesizing", "Outlining",
    "Mapping", "Indexing", "Cataloguing", "Cross-referencing", "Triaging",
    "Prioritizing", "Consolidating", "Reconciling", "Auditing", "Validating",
    "Optimizing", "Streamlining", "Calibrating", "Provisioning", "Shipping",
]

_lock = threading.Lock()
_queue: list[str] = []
_last: str | None = None


def _refill() -> None:
    global _queue
    pool = VERBS[:]
    random.shuffle(pool)
    if _last and pool and pool[0] == _last:
        # avoid repeat across the seam — swap with a later element
        swap_idx = random.randint(1, len(pool) - 1)
        pool[0], pool[swap_idx] = pool[swap_idx], pool[0]
    _queue = pool


def pick() -> str:
    """Return the next non-repeating verb."""
    global _last
    with _lock:
        if not _queue:
            _refill()
        verb = _queue.pop()
        _last = verb
        return verb
