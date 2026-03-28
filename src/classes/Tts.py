import os
import re
import soundfile as sf
from kittentts import KittenTTS as KittenModel

from config import ROOT_DIR, get_tts_voice
from llm_provider import generate_text
from status import warning

KITTEN_MODEL = "KittenML/kitten-tts-mini-0.8"
KITTEN_SAMPLE_RATE = 24000

class TTS:
    def __init__(self) -> None:
        self._model = KittenModel(KITTEN_MODEL)
        self._voice = get_tts_voice()

    def synthesize(self, text, output_file=os.path.join(ROOT_DIR, ".mp", "audio.wav")):
        text = str(text).strip()
        if not text:
            raise ValueError("Cannot synthesize empty text.")

        try:
            audio = self._model.generate(text, voice=self._voice)
        except Exception as original_error:
            if not self._contains_arabic(text):
                raise

            warning(
                "KittenTTS failed on Arabic text. Retrying with an English fallback voiceover."
            )
            fallback_text = self._translate_for_tts_fallback(text)
            try:
                audio = self._model.generate(fallback_text, voice=self._voice)
            except Exception as fallback_error:
                raise RuntimeError(
                    "TTS failed for the original Arabic script and the English fallback voiceover."
                ) from fallback_error

        sf.write(output_file, audio, KITTEN_SAMPLE_RATE)
        return output_file

    def _contains_arabic(self, text: str) -> bool:
        return bool(re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]", text))

    def _translate_for_tts_fallback(self, text: str) -> str:
        prompt = (
            "Translate the following Arabic video narration into natural spoken English. "
            "Keep the meaning, keep it concise, and return only the translated narration.\n\n"
            f"{text}"
        )
        translated = generate_text(prompt)
        translated = str(translated).strip()
        if not translated:
            raise RuntimeError("Failed to generate an English fallback translation for TTS.")
        return translated
