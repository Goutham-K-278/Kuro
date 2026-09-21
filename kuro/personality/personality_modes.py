from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class PersonalityMode(str, Enum):
    WORK = "work"
    CHILL = "chill"
    DEEP_FOCUS = "deep_focus"
    ASSISTANT = "assistant"


EMOTION_TO_MODE = {
    "stressed": PersonalityMode.CHILL,
    "angry": PersonalityMode.CHILL,
    "tired": PersonalityMode.ASSISTANT,
    "sad": PersonalityMode.CHILL,
    "happy": PersonalityMode.CHILL,
    "excited": PersonalityMode.WORK,
    "neutral": PersonalityMode.ASSISTANT,
}


def get_time_based_mode(now: datetime | None = None) -> PersonalityMode:
    now = now or datetime.now()
    hour = now.hour
    if 6 <= hour < 9:
        return PersonalityMode.ASSISTANT
    if 9 <= hour < 12:
        return PersonalityMode.WORK
    if 12 <= hour < 13:
        return PersonalityMode.CHILL
    if 13 <= hour < 18:
        return PersonalityMode.WORK
    if 18 <= hour < 22:
        return PersonalityMode.CHILL
    return PersonalityMode.DEEP_FOCUS


@dataclass
class KuroPersonality:
    current_mode: PersonalityMode = PersonalityMode.ASSISTANT
    manual_override: bool = False
    emotion_history: list[str] = field(default_factory=list)

    def set_mode(self, mode: PersonalityMode, manual: bool = False) -> None:
        self.current_mode = mode
        self.manual_override = manual

    def auto_update(self, emotion: str) -> None:
        self.emotion_history.append(emotion)
        self.emotion_history = self.emotion_history[-5:]

        if self.manual_override:
            return

        emotion_mode = EMOTION_TO_MODE.get(emotion, PersonalityMode.ASSISTANT)
        time_mode = get_time_based_mode()
        self.current_mode = emotion_mode if emotion != "neutral" else time_mode

    def handle_voice_command(self, text: str) -> bool:
        t = text.lower()
        if "work mode" in t:
            self.set_mode(PersonalityMode.WORK, manual=True)
            return True
        if "chill mode" in t or "relax mode" in t:
            self.set_mode(PersonalityMode.CHILL, manual=True)
            return True
        if "deep focus" in t or "focus mode" in t:
            self.set_mode(PersonalityMode.DEEP_FOCUS, manual=True)
            return True
        if "auto mode" in t or "reset mode" in t:
            self.manual_override = False
            return True
        return False

    def mode_name(self) -> str:
        return self.current_mode.value

