import json
from google import genai
import config

# Initialize client using your API key from config
client = genai.Client(api_key=config.GEMINI_API_KEY)

# Explicitly pull the batch model from config, defaulting safely if missing
MODEL_NAME = getattr(config, "BATCH_MODEL", "gemini-2.5-flash")

def load_brain():
    try:
        with open("brain.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"unlocked_chars": {}, "sentence_bank": []}

def get_batch():
    data = load_brain()
    unlocked = "".join(data.get("unlocked_chars", {}).keys())

    if not unlocked:
        print("Your unlocked_chars list is empty!")
        return

    print(f"Generating sentences with {MODEL_NAME}...")

    prompt = (
        f"Generate 10 simple sentences using ONLY these characters: {unlocked}. "
        f"Format each line strictly as: English | Chinese"
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt
    )

    new_sentences = []
    lines = response.text.strip().split("\n")
    allowed_punct = "，。！？、 ；：“”‘’—…\t\r"

    for line in lines:
        if "|" in line:
            parts = line.split("|", 1)
            eng = parts[0].strip()
            chi = parts[1].strip()

            if all(c in unlocked or c in allowed_punct for c in chi):
                new_sentences.append(
                    {"english": eng, "chinese": chi, "used_count": 0}
                )

    data.setdefault("sentence_bank", []).extend(new_sentences)

    with open("brain.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Success! Added {len(new_sentences)} verified sentences.")

if __name__ == "__main__":
    get_batch()
