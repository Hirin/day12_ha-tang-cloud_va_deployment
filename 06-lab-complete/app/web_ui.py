from __future__ import annotations

import re


CHAT_PAGE_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Production AI Agent</title>
    <style>
      :root { color-scheme: light; }
      body { margin: 0; font-family: Georgia, serif; background: linear-gradient(160deg, #f4efe6, #dfe9f3); color: #1b1d1f; }
      main { max-width: 760px; margin: 0 auto; padding: 40px 20px 64px; }
      h1 { margin: 0 0 12px; font-size: 2.2rem; }
      p { margin: 0 0 20px; line-height: 1.5; }
      form, .chat-log { background: rgba(255,255,255,0.88); border: 1px solid rgba(27,29,31,0.12); border-radius: 16px; }
      form { padding: 16px; display: grid; gap: 12px; }
      label { display: grid; gap: 6px; font-weight: 600; }
      input, textarea, button { font: inherit; }
      input, textarea { width: 100%; padding: 12px; border: 1px solid #c3ccd5; border-radius: 10px; box-sizing: border-box; }
      textarea { min-height: 110px; resize: vertical; }
      button { width: fit-content; padding: 12px 18px; border: 0; border-radius: 999px; background: #153b50; color: white; cursor: pointer; }
      .chat-log { margin-top: 18px; padding: 16px; min-height: 220px; }
      .message { padding: 12px 14px; border-radius: 12px; margin-bottom: 10px; }
      .message.user { background: #edf4ff; }
      .message.bot { background: #f7f2e8; }
      .status { min-height: 24px; margin-top: 12px; color: #6a2c2c; }
    </style>
  </head>
  <body>
    <main>
      <h1>Public AI Chat</h1>
      <p>Enter a nickname, ask a question, and the backend will keep your conversation history in Redis.</p>
      <form id="chat-form">
        <label>
          Nickname
          <input id="nickname" name="nickname" maxlength="40" required />
        </label>
        <label>
          Question
          <textarea id="question" name="question" maxlength="2000" required></textarea>
        </label>
        <button type="submit">Send</button>
      </form>
      <div id="status" class="status" aria-live="polite"></div>
      <section id="chat-log" class="chat-log"></section>
    </main>
    <script>
      const form = document.getElementById("chat-form");
      const chatLog = document.getElementById("chat-log");
      const statusEl = document.getElementById("status");
      const nicknameEl = document.getElementById("nickname");
      const questionEl = document.getElementById("question");

      function appendMessage(role, content) {
        const item = document.createElement("article");
        item.className = `message ${role}`;
        item.textContent = content;
        chatLog.appendChild(item);
      }

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        statusEl.textContent = "Thinking...";

        const nickname = nicknameEl.value;
        const question = questionEl.value;
        appendMessage("user", question);

        try {
          const response = await fetch("/web/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nickname, question }),
          });
          const payload = await response.json();
          if (!response.ok) {
            throw new Error(payload.detail || "The bot is temporarily unavailable. Please try again.");
          }
          appendMessage("bot", payload.answer);
          questionEl.value = "";
          statusEl.textContent = "";
        } catch (error) {
          statusEl.textContent = error.message;
        }
      });
    </script>
  </body>
</html>
"""


def normalize_nickname(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    return cleaned[:40]
