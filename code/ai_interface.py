import os
import json
import time
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from PIL import Image
from google import genai
from google.genai import types
from google.genai.errors import APIError

from config import CACHE_FILE, IMAGES_DIR
from logger import tracker

# Define output schemas
class MessageFact(BaseModel):
    action: str = Field(description="One of: CONFIRM, INCREASE, REDUCE, DELAY, CANCEL, PENDING")
    amount: float = Field(description="The new or modified amount mentioned. 0.0 if none", default=0.0)
    date: str = Field(description="The new or modified date mentioned (YYYY-MM-DD). 'NONE' if none", default="NONE")
    target_event_id: str = Field(description="The related event_id if explicitly mentioned, or 'NONE'", default="NONE")

class ImageFact(BaseModel):
    extracted_amount: float = Field(description="The exact final numerical amount extracted from the image.")

class AIInterface:
    def __init__(self):
        # We assume GOOGLE_API_KEY is in the environment
        import os
        from dotenv import load_dotenv
        load_dotenv()
        self.client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
        self.model = 'gemini-3.6-flash'
        self.cache: Dict[str, Any] = self._load_cache()

    def _load_cache(self) -> Dict[str, Any]:
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, indent=2)

    def extract_image_amount(self, image_id: str, description: str, currency: str) -> Optional[float]:
        cache_key = f"img_{image_id}"
        if cache_key in self.cache:
            print(f" (Cache hit for {image_id})")
            return self.cache[cache_key]['extracted_amount']

        image_path = IMAGES_DIR / f"{image_id}.png"
        if not image_path.exists():
            print(f"Warning: Image {image_id} not found at {image_path}")
            return None

        prompt = (
            f"You are a strict data extraction tool. Look at this image representing a financial transaction "
            f"described as '{description}'. The currency is {currency}. "
            f"Extract ONLY the final, total numeric amount. Return it as a float."
        )

        for attempt in range(3):
            try:
                img = Image.open(image_path)
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[img, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ImageFact,
                        temperature=0.0
                    )
                )
                
                # Track usage
                tracker.add_vlm_usage(
                    response.usage_metadata.prompt_token_count,
                    response.usage_metadata.candidates_token_count
                )
                
                result = json.loads(response.text)
                amount = result.get('extracted_amount')
                
                self.cache[cache_key] = result
                self.cache[cache_key]['_metadata'] = {
                    'description': description,
                    'currency': currency
                }
                self._save_cache()
                return float(amount) if amount is not None else None
                
            except Exception as e:
                print(f"Attempt {attempt+1} failed for image {image_id}: {e}")
                time.sleep(2 ** attempt)
        
        return None

    def interpret_message(self, message_id: str, text: str, source_type: str) -> Optional[Dict[str, Any]]:
        cache_key = f"msg_{message_id}"
        if cache_key in self.cache:
            print(f" (Cache hit for {message_id})")
            return self.cache[cache_key]

        prompt = f"""
You are a strict data extraction tool for financial messages.
Source type: {source_type}
Message text: {text}

Extract the underlying fact. The action must be EXACTLY one of: 
CONFIRM, INCREASE, REDUCE, DELAY, CANCEL, PENDING.

- CONFIRM: Solidifies a pending date/amount.
- INCREASE: Raises a recurring amount.
- REDUCE / TEMPORARY: Lowers a recurring amount. Use REDUCE.
- DELAY: Shifts an expected date.
- CANCEL: Terminates a recurring contract/expense.
- PENDING: Warns that an expected credit is not yet final.

Return the action, the new amount (if specified), the new date (if specified in YYYY-MM-DD), and the target_event_id if mentioned.
"""

        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=MessageFact,
                        temperature=0.0
                    )
                )
                
                # Track usage
                tracker.add_llm_usage(
                    response.usage_metadata.prompt_token_count,
                    response.usage_metadata.candidates_token_count
                )
                
                result = json.loads(response.text)
                self.cache[cache_key] = result
                self.cache[cache_key]['_metadata'] = {'text': text[:100] + '...'}
                self._save_cache()
                return result
                
            except Exception as e:
                print(f"Attempt {attempt+1} failed for message {message_id}: {e}")
                time.sleep(2 ** attempt)
                
        return None
