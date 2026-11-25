"""Probe the regular text entry point with public factual and operational questions."""
import argparse
import json
import re
from pathlib import Path

import httpx

# Each tuple contains independently checkable concepts. Candidate wording still needs review.
CASES = [
    ("sky", "en", "Why is the sky blue?", ["scatter", "blue"]),
    ("shorter", "en", "Make that explanation shorter.", ["scatter", "blue"]),
    ("arithmetic", "en", "What is seventeen multiplied by six?", ["102|one hundred (and )?two"]),
    ("reasoning", "en", "Which is heavier, one kilogram of steel or one kilogram of cotton?", ["same|equal|both weigh"]),
    ("hindi", "hi", "पृथ्वी का प्राकृतिक उपग्रह कौन सा है?", ["चंद्रमा|चन्द्रमा|चाँद|चांद"]),
    ("kannada", "kn", "ನೀರಿನ ರಾಸಾಯನಿಕ ಸೂತ್ರ ಯಾವುದು?", ["h₂o|h2o|ಎಚ್.*ಒ"]),
    ("capabilities", "en", "What can you do?", ["timer", "reminder", "expense"]),
    ("weather", "en", "Is it raining in Mumbai right now?", ["offline|real.time|live|internet|cannot|can't|unable"]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True, choices=range(8010, 8100))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}/api/v1/", timeout=150, trust_env=False) as client:
        for key, language, question, criteria in CASES:
            response = client.post("intent/process", json={"text": question, "language": language, "speak": False})
            response.raise_for_status()
            turn = response.json()
            answer = turn["response_text"]
            checks = {
                "concepts": all(re.search(pattern, answer, re.IGNORECASE) for pattern in criteria),
                "response_language": turn["response_language"] == language,
                "route": turn["intent"] == "Help" if key == "capabilities" else (turn["intent"] == "Calculate" and turn["data"].get("value") == 102)
                         if key == "arithmetic" else not turn["action_executed"],
            }
            rows.append({"id": key, "question": question, "reply": answer, "intent": turn["intent"],
                         "criteria": criteria, "checks": checks, "passed": all(checks.values())})
            args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
            print(key, rows[-1]["passed"], flush=True)


if __name__ == "__main__":
    main()
