from typing import List

SUPPORTED_SUBJECTS: List[str] = [
    "Programming",
    "Mathematics",
    "Physics",
    "Chemistry",
    "Computer Science",
    "General Study Help",
]


def normalize_subject(subject: str) -> str:
    if not isinstance(subject, str):
        return "General Study Help"

    clean = subject.strip().lower()

    alias_map = {
        "programming": "Programming",
        "coding": "Programming",
        "mathematics": "Mathematics",
        "math": "Mathematics",
        "physics": "Physics",
        "chemistry": "Chemistry",
        "computer science": "Computer Science",
        "cs": "Computer Science",
        "general": "General Study Help",
        "general study help": "General Study Help",
        "study help": "General Study Help",
    }

    if clean in alias_map:
        return alias_map[clean]

    for known in SUPPORTED_SUBJECTS:
        if clean == known.lower():
            return known

    return "General Study Help"


def difficulty_from_focus(focus_score: int) -> str:
    if focus_score > 80:
        return "hard"
    if focus_score >= 40:
        return "normal"
    return "easy"


def focus_support_message(focus_score: int) -> str:
    if focus_score > 80:
        return "Great focus. We can handle an advanced challenge."
    if focus_score >= 40:
        return "Good focus. Let's continue with a balanced explanation."
    return "Your focus dropped. Let's try a quick easy problem and rebuild momentum."
