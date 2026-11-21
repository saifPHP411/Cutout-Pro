"""Make one bounded Gemini request using a staging-only environment credential."""
import json
import os
import re
import sys

import httpx


def main():
    key = os.environ.get("STAGE_GEMINI_KEY", "")
    model = os.environ.get("STAGE_GEMINI_MODEL", "").removeprefix("models/")
    if not key or not re.fullmatch(r"gemini-[a-zA-Z0-9_.-]+", model):
        print("Set a staging Gemini key and model before running the smoke test.", file=sys.stderr)
        return 2
    try:
        response = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              headers={"x-goog-api-key": key}, timeout=15,
                              json={"contents": [{"parts": [{"text": "Reply only with OK."}]}],
                                    "generationConfig": {"maxOutputTokens": 128}})
        if response.status_code != 200:
            print(json.dumps({"http": response.status_code, "ok": False}))
            return 1
        body = response.json()
        texts = [part.get("text", "") for candidate in body.get("candidates", []) if candidate.get("finishReason") == "STOP"
                 for part in candidate.get("content", {}).get("parts", []) if not part.get("thought")]
        ok = any(text.strip().upper().rstrip(".") == "OK" for text in texts)
        print(json.dumps({"http": 200, "ok": ok, "total_tokens": body.get("usageMetadata", {}).get("totalTokenCount")}))
        return 0 if ok else 1
    except (httpx.HTTPError, ValueError):
        print("Gemini request or response could not be verified.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
