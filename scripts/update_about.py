import re

with open("tree_output.txt", "r", encoding="utf-8") as f:
    tree_text = f.read()

with open("about.md", "r", encoding="utf-8") as f:
    about_text = f.read()

# Replace the tree block
new_about = re.sub(
    r"```text\nKuro-LLM/.*?```",
    f"```text\n{tree_text}\n```",
    about_text,
    flags=re.DOTALL
)

# Replace the LLM block
new_about = re.sub(
    r"- \*\*LLM Engines\*\*: `Groq` API.*?OpenRouter\.",
    r"- **LLM Engines**: `Groq` API (Llama-3.1-8b-instant) for ultra-fast tool calling and core intent parsing.",
    new_about
)

# Also ensure it worked:
if "- **LLM Engines**: `Groq`" not in new_about:
    print("Failed to replace LLM block!")

with open("about.md", "w", encoding="utf-8") as f:
    f.write(new_about)

print("Update complete")
