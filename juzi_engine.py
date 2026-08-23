import json
import warnings
from google import genai
import config

# Suppress minor SDK warning logs regarding automatic function calling
warnings.filterwarnings("ignore", category=UserWarning, module="google.genai")

class JuziEngine:
    def __init__(self, brain_path="brain.json"):
        self.brain_path = brain_path
        # Initialize the official unified GenAI client
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model_name = getattr(config, "BATCH_MODEL", "gemini-2.5-flash")

    def load_unlocked_chars(self) -> str:
        """Loads unlocked characters from brain.json and returns them as a single string pool."""
        try:
            with open(self.brain_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                chars = list(data.get("unlocked_chars", {}).keys())
                return "".join(chars)
        except FileNotFoundError:
            print(f"Warning: {self.brain_path} not found.")
            return ""

    def generate_session(self, count: int = 5) -> list:
        """Dynamically generates a fresh list of session sentences using ONLY unlocked characters."""
        unlocked = self.load_unlocked_chars()
        
        if not unlocked:
            raise ValueError("Your unlocked_chars pool is empty! Unlock characters in brain.json first.")

        prompt = (
            f"Generate {count} unique, simple Mandarin Chinese sentences using ONLY these characters: {unlocked}. "
            f"Do not use any characters outside this set, except standard Chinese punctuation (，。！？、). "
            f"Format each line strictly as: English | Chinese"
        )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt
        )

        session_sentences = []
        lines = response.text.strip().split("\n")
        allowed_punct = "，。！？、 ；：“”‘’—…\t\r"

        for line in lines:
            if "|" in line:
                parts = line.split("|", 1)
                eng = parts[0].strip()
                chi = parts[1].strip()

                # Strict validation: Check every single character against unlocked pool or punctuation
                if all(c in unlocked or c in allowed_punct for c in chi):
                    session_sentences.append({
                        "english": eng,
                        "chinese": chi,
                        "status": "pending"
                    })

        return session_sentences

if __name__ == "__main__":
    # Quick test run of the module
    engine = JuziEngine()
    try:
        sentences = engine.generate_session(count=3)
        print(f"Successfully generated {len(sentences)} dynamic session sentences:")
        for idx, s in enumerate(sentences, 1):
            print(f"{idx}. {s['english']} -> {s['chinese']}")
    except Exception as e:
        print(f"Error generating session: {e}")
