import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
THEME_LIST = [
    "greed",
    "community",
    "patience",
    "injustice",
    "resilience",
    "caution",
    "wisdom",
    "humility",
    "justice",
    "integrity",
    "change",
    "hope",
    "perseverance",
    "accountability",
    "truth",
    "compassion",
    "discipline",
    "courage",
    "adaptation",
    "unity",
    "memory",
    "gratitude",
    "self_reliance",
    "perspective",
    "care",
    "reward",
    "consequence",
]


def classify_batch(api_key, items):
    prompt = (
        "You assign relevant themes to each proverb. "
        "Return JSON only as an array of objects with keys: text, themes. "
        "Themes must be chosen only from this list: "
        + ", ".join(THEME_LIST)
        + ".\n\n"
        "Proverbs:\n"
        + "\n".join(
            [
                f"- {item['text']}"
                + (f" (meaning: {item['meaning']})" if item["meaning"] else "")
                for item in items
            ]
        )
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL}:generateContent?key={api_key}"
    )
    req = urllib.request.Request(
        url, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(
            req, data=json.dumps(payload).encode("utf-8"), timeout=60
        ) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                f"Model not found: {MODEL}. Set GEMINI_MODEL to a valid model."
            ) from exc
        raise

    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("No candidates from Gemini")
    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    return json.loads(text)


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set")

    extra_path = Path(__file__).resolve().parent.parent / "proverbs_extra.json"
    with open(extra_path, "r", encoding="utf-8") as f:
        extra = json.load(f)

    batch_size = 8
    results = {}

    for i in range(0, len(extra), batch_size):
        batch = extra[i : i + batch_size]
        for attempt in range(5):
            try:
                classified = classify_batch(api_key, batch)
                for entry in classified:
                    text = (entry.get("text") or "").strip()
                    if text:
                        results[text] = entry.get("themes") or []
                break
            except Exception as exc:
                if "429" in str(exc):
                    delay = min(30, 2 ** (attempt + 1))
                    time.sleep(delay)
                    continue
                if attempt == 4:
                    raise
                time.sleep(2)
        time.sleep(1.0)

    for item in extra:
        item["themes"] = results.get(item["text"], [])

    with open(extra_path, "w", encoding="utf-8") as f:
        json.dump(extra, f, ensure_ascii=False, indent=2)

    print(f"tagged {len(extra)}")


if __name__ == "__main__":
    main()
