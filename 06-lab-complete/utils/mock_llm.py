"""
Mock LLM used by the self-contained Part 6 project.
"""
import random
import time


MOCK_RESPONSES = {
    "default": [
        "This is a mock response from the production agent.",
        "The agent is running correctly in mock mode.",
        "Your request reached the deployed AI agent successfully.",
    ],
    "docker": [
        "Containers package the app and its dependencies so it runs consistently.",
    ],
    "deploy": [
        "Deployment is the process of publishing your app so others can use it.",
    ],
    "health": [
        "The agent is healthy and operational.",
    ],
}


def ask(question: str, delay: float = 0.05) -> str:
    time.sleep(delay + random.uniform(0, 0.02))

    question_lower = question.lower()
    for keyword, responses in MOCK_RESPONSES.items():
        if keyword in question_lower:
            return random.choice(responses)

    return random.choice(MOCK_RESPONSES["default"])
