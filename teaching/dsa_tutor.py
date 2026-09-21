"""
KURO - DSA Tutor
Teaching module for Data Structures & Algorithms
Heavy explanations → CLOUD brain. Quick recalls → LOCAL brain.
"""
import logging

logger = logging.getLogger(__name__)


class DSATutor:
    """
    Interactive DSA (Data Structures & Algorithms) teacher.
    Uses CLOUD model for thorough explanations, LOCAL for quick lookups.
    """

    def __init__(self, llm_engine):
        self.llm = llm_engine

    def teach(self, topic: str) -> str:
        """
        Give a detailed explanation of a DSA topic.
        Routes to CLOUD Qwen3-Coder-480B for quality.
        """
        messages = [{"role": "user", "content": self._build_teach_prompt(topic)}]
        return self.llm.chat(messages, use_cloud=True, mode="dsa")

    def quick_recall(self, question: str) -> str:
        """
        Quick definition or fact lookup.
        Uses LOCAL model for speed.
        """
        messages = [{"role": "user", "content": question}]
        return self.llm.chat(messages, use_cloud=False, mode="dsa")

    def generate_practice_problem(self, topic: str, difficulty: str = "medium") -> str:
        """
        Generate a practice problem for a given topic.
        Routes to CLOUD for creative, high-quality problems.
        """
        prompt = f"""Generate a {difficulty} difficulty coding problem about: {topic}

Format:
## Problem Title
**Difficulty:** {difficulty}

### Problem Statement
[Clear problem description]

### Examples
Input: ...
Output: ...

### Constraints
- [List constraints]

### Hint (optional)
[One small hint]

Do NOT include the solution yet."""

        messages = [{"role": "user", "content": prompt}]
        return self.llm.chat(messages, use_cloud=True, mode="dsa")

    def review_solution(self, problem: str, code: str) -> str:
        """
        Review a user's solution to a DSA problem.
        Checks correctness, time/space complexity, and code quality.
        Routes to CLOUD for accuracy.
        """
        prompt = f"""Review this solution to the following problem:

### Problem
{problem}

### User's Solution
```python
{code}
```

Please provide:
1. **Correctness**: Does it solve the problem? Any edge cases missed?
2. **Time Complexity**: What is it? Can it be improved?
3. **Space Complexity**: What is it? Can it be improved?
4. **Code Quality**: Is it clean, readable? Any improvements?
5. **Alternative Approach** (if better one exists)

Be encouraging but honest."""

        messages = [{"role": "user", "content": prompt}]
        return self.llm.chat(messages, use_cloud=True, mode="dsa")

    def explain_complexity(self, code: str) -> str:
        """Analyze and explain time/space complexity of provided code."""
        prompt = f"""Analyze the time and space complexity of this code:

```python
{code}
```

Explain step by step with Big O notation. Show which lines/loops contribute to the complexity."""

        messages = [{"role": "user", "content": prompt}]
        return self.llm.chat(messages, use_cloud=True, mode="dsa")

    def _build_teach_prompt(self, topic: str) -> str:
        return f"""Teach me about: {topic}

Please structure your explanation like this:
1. **What is it?** — Simple definition in 2-3 sentences
2. **Visual/Analogy** — A real-world analogy to make it click
3. **How it works** — Step-by-step walkthrough
4. **Python Implementation** — Clean, commented code
5. **Time & Space Complexity** — Big O analysis
6. **When to use it** — Practical situations
7. **Common Mistakes** — Pitfalls to avoid
8. **Practice Problems** — 2-3 problems to try

Make it beginner-friendly but technically accurate."""
