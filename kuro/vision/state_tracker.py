from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AppState(str, Enum):
    CLOSED = "closed"
    LAUNCHING = "launching"
    OPEN = "open"
    SEARCHING = "searching"
    IN_VIEW = "in_view"
    COMPOSING = "composing"
    LOADING = "loading"
    ERROR = "error"
    POPUP = "popup"


@dataclass
class StateSnapshot:
    app: str
    state: AppState
    detail: str = ""
    timestamp: float = field(default_factory=time.time)
    screen_text: str = ""

    def __str__(self) -> str:
        return f"{self.app}:{self.state.value}" + (f":{self.detail}" if self.detail else "")


class AppStateTracker:
    def __init__(self, history_size: int = 20):
        self._states: Dict[str, StateSnapshot] = {}
        self._history: deque[StateSnapshot] = deque(maxlen=history_size)

    def update(self, app: str, state: AppState, detail: str = "", screen_text: str = "") -> StateSnapshot:
        snap = StateSnapshot(
            app=app.lower(),
            state=state,
            detail=detail,
            screen_text=screen_text[:200],
        )
        self._states[app.lower()] = snap
        self._history.append(snap)
        return snap

    def get_state(self, app: str) -> Optional[StateSnapshot]:
        return self._states.get(app.lower())

    def get_state_string(self, app: str) -> str:
        s = self.get_state(app)
        return str(s) if s else f"{app}:unknown"

    def is_in_state(self, app: str, state: AppState) -> bool:
        s = self.get_state(app)
        return s is not None and s.state == state

    def get_context(self) -> str:
        if not self._states:
            return "[No app states tracked yet]"
        lines = ["[App States]"]
        now = time.time()
        for app, snap in sorted(self._states.items()):
            age = now - snap.timestamp
            age_s = f"{age:.0f}s ago" if age < 60 else f"{age/60:.0f}m ago"
            lines.append(f"  {snap} (updated {age_s})")
        return "\n".join(lines)

    def get_recent_transitions(self, n: int = 5) -> List[str]:
        return [str(s) for s in list(self._history)[-n:]]

    def clear(self, app: Optional[str] = None) -> None:
        if app:
            self._states.pop(app.lower(), None)
            return
        self._states.clear()
        self._history.clear()
