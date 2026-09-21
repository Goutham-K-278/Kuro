"""
KURO - Intent Classifier
Two-stage classification:
  Stage 1: Regex pattern matching (fast, deterministic)
  Stage 2: Keyword scoring (for ambiguous inputs)
"""
import re
import logging

from core.model_router import Intent

logger = logging.getLogger(__name__)


# ─── Stage 1: Regex Patterns ────────────────────────────────────────────────
# Ordered by priority (first match wins)
PATTERNS: list[tuple[Intent, list[str]]] = [
    (Intent.BLENDER_CREATE, [
        r"create .*(3d|scene|mesh|object|material|shader|blender)",
        r"(generate|make|build) .*(blender|bpy|3d model)",
        r"blender .*(script|python|create|make|scene)",
        r"(animate|render) .*(3d|blender)",
    ]),
    (Intent.BLENDER_MODIFY, [
        r"(modify|change|edit|update|fix) .*(blender|3d|mesh|scene|material)",
        r"add .*(light|camera|modifier|texture) .*(blender|scene)",
    ]),
    (Intent.TEACH_DSA, [
        r"teach me .*(array|tree|graph|stack|queue|heap|dp|sorting|algorithm)",
        r"explain .*(data structure|algorithm|big.?o|complexity)",
        r"(how does|what is) .*(binary search|linked list|hash|bfs|dfs)",
        r"(learn|study|understand) .*(dynamic programming|recursion|backtracking)",
    ]),
    (Intent.CODE_REVIEW, [
        r"review (my |this |the )?code",
        r"(what'?s wrong|bug|error|fix) .*(code|function|script)",
        r"(optimize|improve|refactor) .*(code|function|class)",
        r"check (my |this )?code",
    ]),
    (Intent.CODE_HELP, [
        r"(write|generate|create) .*(function|class|script|program|code)",
        r"(how (do|to)|example of) .*(python|code|function|implement)",
        r"code (for|to|that)",
    ]),
    (Intent.SYSTEM_COMMAND, [
        r"open (chrome|firefox|notepad|spotify|terminal|youtube|vscode|blender|whatsapp|telegram|discord|calculator|paint|excel|word|settings|explorer)",
        r"(close|quit|kill) .*(app|window|chrome|firefox|notepad|spotify)",
        r"take (a )?screenshot",
        r"(increase|decrease|set|mute) (volume|brightness)",
        r"(shutdown|restart|sleep|lock) (computer|pc|system|windows)?",
        r"copy .*(clipboard)?",
        r"(click|press) .*(keyboard|button|key)",
        # type / write commands — match without requiring 'keyboard'
        r"^(type|write|input)\s+.+",
        r"(type|write|input) (this|that|the following|something|a message|something)",
        r"(minimize|maximize|snap|switch to) .*(window|app|chrome|notepad)?",
        r"(find|click on|click) .*(on screen|in .*(notepad|chat|app|window))?",
        r"send .*(message|whatsapp|telegram|discord)?",
        r"(volume|brightness) (up|down|to \d+)",
        r"(scroll|zoom) (up|down|in|out)",
    ]),
    (Intent.FILE_OPERATION, [
        r"(create|make|new) .*(file|folder|directory)",
        r"(delete|remove|move|copy|rename) .*(file|folder)",
        r"(open|read|write|save) .*(file|document|txt|json|csv)",
        r"(list|show|find) .*(files|folders) in",
    ]),
    (Intent.SYSTEM_INFO, [
        r"(how much|check|show|what is) .*(cpu|ram|memory|disk|storage|battery|gpu)",
        r"(system|process|performance) (info|status|usage|stats)",
        r"what('?s| is) (running|using|consuming)",
        r"(temperature|temp) of .*(cpu|gpu)",
    ]),
    (Intent.MEMORY_RECALL, [
        r"(what did (i|we)|do you remember|recall) .*(say|ask|tell|discuss)",
        r"(earlier|before|last time|previously) (you|we|i)",
        r"what('?s| is) (my|the) .*(name|preference|goal)",
    ]),
    (Intent.WEB_SEARCH, [
        r"search (for|about|on) ",
        r"(google|look up|find) .*(online|web|internet)",
        r"(latest|current|news|today'?s?) .*(on|about)",
    ]),
    (Intent.SETTINGS, [
        r"(change|set|update|configure) .*(setting|config|preference|voice|theme)",
        r"(enable|disable|turn on|turn off) .*(voice|feature|mode)",
    ]),
    (Intent.GENERAL_CHAT, [
        r"^(hi|hello|hey|yo|sup|what'?s up)",
        r"^(how are you|how'?s it going)",
        r"(thank|thanks|good job|nice|cool|awesome)",
        r"(who are you|what are you|your name)",
    ]),
]


# ─── Stage 2: Keyword Scoring ────────────────────────────────────────────────
KEYWORD_SCORES: dict[Intent, list[str]] = {
    Intent.TEACH_DSA:      ["sort", "search", "tree", "graph", "queue", "dp", "algorithm", "leetcode", "complexity"],
    Intent.CODE_HELP:      ["write", "function", "code", "implement", "python", "loop", "class", "method"],
    Intent.CODE_REVIEW:    ["review", "check", "bug", "error", "optimize", "refactor"],
    Intent.BLENDER_CREATE: ["blender", "3d", "mesh", "bpy", "material", "render", "animate", "scene"],
    Intent.SYSTEM_COMMAND: ["open", "close", "screenshot", "volume", "brightness", "shutdown", "type"],
    Intent.SYSTEM_INFO:    ["cpu", "ram", "disk", "gpu", "battery", "memory", "usage"],
    Intent.GENERAL_CHAT:   ["hi", "hello", "hey", "thanks", "ok", "sure", "cool"],
}


def classify(user_message: str) -> Intent:
    """
    Classify user intent using two-stage approach:
    Stage 1: Pattern matching → return immediately on first match
    Stage 2: Keyword scoring → winner takes all
    """
    msg = user_message.lower().strip()

    # Stage 1: Pattern matching
    for intent, patterns in PATTERNS:
        for pattern in patterns:
            if re.search(pattern, msg):
                logger.debug(f"Pattern match: '{pattern}' → {intent.value}")
                return intent

    # Stage 2: Keyword scoring
    scores: dict[Intent, int] = {intent: 0 for intent in KEYWORD_SCORES}
    for intent, keywords in KEYWORD_SCORES.items():
        for kw in keywords:
            if kw in msg:
                scores[intent] += 1

    best_intent = max(scores, key=lambda i: scores[i])
    if scores[best_intent] > 0:
        logger.debug(f"Keyword score: {best_intent.value} ({scores[best_intent]} hits)")
        return best_intent

    logger.debug("No match found → GENERAL_CHAT")
    return Intent.GENERAL_CHAT
