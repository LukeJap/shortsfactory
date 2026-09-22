import json
from urllib.request import Request, urlopen

payload = {
    "model": "llama3.2:3b",
    "prompt": """Read this short transcript and return JSON only.

Transcript:
One of these fights was so much lamer with Rogan. We went to a fight on the lawn of the Playboy Mansion. What was weird was how dated the mansion is. There's an old button phone on the grotto. Everything is just sad.

Return exactly:
{
  "moments": [
    {
      "timestamp": "00:00:00.000",
      "description": "short description",
      "why_interesting": "short explanation"
    }
  ]
}

Find 3 interesting moments. Do not invent information.""",
    "stream": False,
    "format": "json",
    "options": {
        "temperature": 0.1
    }
}

request = Request(
    "http://127.0.0.1:11434/api/generate",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

print("Sending test request to Ollama...")

with urlopen(request, timeout=60) as response:
    result = json.loads(response.read().decode("utf-8"))

print("\nOllama response:")
print(result["response"])
