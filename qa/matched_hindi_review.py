"""Compare two recognizers on identical Hindi audio with independent command and negation checks."""
import argparse
import json
import time
from contextlib import ExitStack
from pathlib import Path

import httpx

from qa.run_acceptance import CASES, json_api, synthesize, validate_turn


NEGATIVES = [
    "बेडरूम की लाइट चालू मत करो।",
    "बेडरूम की लाइट बंद नहीं करनी है।",
    "दस सेकंड का टाइमर मत लगाओ।",
    "स्टडी का पंखा चालू मत करो।",
    "बेडरूम का एसी चौबीस डिग्री पर सेट मत करो।",
    "मुझे बताओ कि पंखा कैसे काम करता है।",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int, choices=range(8010, 8100))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baseline-port", type=int, choices=range(8010, 8100))
    args = parser.parse_args()
    positives = [c for c in CASES if c.get("audio") and c["language"] == "hi"]
    guards = [dict(id=f"guard-{i}", text=text, language="hi", category="negative")
              for i, text in enumerate(NEGATIVES, 1)]
    rows = []
    with ExitStack() as stack:
        def open_client(port):
            return stack.enter_context(httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120, trust_env=False))
        current = open_client(args.port)
        variants = [("current", current, "multilingual", "auto"), ("current", current, "strict", "hi")]
        if args.baseline_port:
            variants.insert(0, ("baseline", open_client(args.baseline_port), "multilingual", "auto"))
        for case in positives + guards:
            audio = synthesize(current, case)
            for build, client, mode, language in variants:
                before = time.time()
                last_change = json_api(client, "/smart-home/history?limit=1")
                timers = {t["id"] for t in json_api(client, "/timers?include_closed=true")}
                result = json_api(client, "/voice/transcribe", "POST", files={"file": ("public.wav", audio, "audio/wav")},
                                  data={"language": language, "recognition_mode": mode, "speak": "false", "allow_llm_fallback": "false"})
                changes = json_api(client, "/smart-home/history?limit=1") != last_change
                new_timers = {t["id"] for t in json_api(client, "/timers?include_closed=true")} - timers
                if case["category"] == "negative":
                    checks = {"no_mutation": not changes and not new_timers and not result["action_executed"]}
                else:
                    checks = validate_turn(client, case, result, before)
                rows.append(dict(id=case["id"], input=case["text"], build=build, mode=mode, transcript=result["transcript"],
                                 language=result["detected_language"], probability=result.get("language_probability"),
                                 model=result["stt_model"], intent=result["intent"], slots=result["slots"],
                                 action_executed=result["action_executed"], device_history_changed=changes,
                                 new_timer_count=len(new_timers), checks=checks, passed=all(checks.values())))
                args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    print(f"Scored {len(rows)} paired trials")


if __name__ == "__main__":
    main()
