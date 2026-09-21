"""
KURO - Memory Extractor
Uses the local LLM to auto-extract important facts from conversation turns.
"""
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm_engine import LLMEngine
    from core.long_term_memory import KuroMemory

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """
Analyze this brief conversational exchange and extract any concrete, long-term facts you learned about the user.
Examples of important facts: their name, preferences, favorite things, ongoing projects, important dates, skills, or constraints.
Do NOT extract transient information like "the user said hello" or "the user asked a question".
If there are no concrete long-term facts, return an empty JSON array: []

Conversation:
{conversation}

Respond ONLY with a valid JSON array of strings containing the facts. No markdown formatting, no explanations. Just the JSON array. Example: ["The user's name is John", "The user is learning React"]
"""

def extract_and_save_facts(llm: "LLMEngine", memory: "KuroMemory", user_msg: str, kuro_response: str) -> None:
    """Ask the local LLM if there are new facts to learn, and save them to memory."""
    try:
        dialogue = f"User: {user_msg}\nKuro: {kuro_response}"
        prompt = EXTRACT_PROMPT.format(conversation=dialogue)
        
        # We always use the local model for this background task to save free API credits
        # And we use a very fast setting, using stream=False. Give system prompt to act as data extractor.
        messages = [{"role": "user", "content": prompt}]
        
        logger.debug("Running background memory extraction...")
        
        # Temporary hack: since chat_local forces a specific system prompt, 
        # let's just use chat() directly. We add instructions into the user prompt itself.
        result = llm.chat(messages, use_cloud=False, mode="fast")
        
        result_clean = result.strip()
        
        # Handle cases where model wraps in ```json ... ```
        if result_clean.startswith("```"):
            lines = result_clean.split("\n")
            if len(lines) >= 3:
                result_clean = "\n".join(lines[1:-1]).strip()
        
        if not result_clean or result_clean == "[]":
            return
            
        facts = json.loads(result_clean)
        
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, str) and len(fact) > 3:
                    memory.save_fact(fact)
                    
    except json.JSONDecodeError:
        logger.debug(f"Memory extraction failed to parse JSON: '{result_clean}'")
    except Exception as e:
        logger.warning(f"Memory extraction failed: {e}")
