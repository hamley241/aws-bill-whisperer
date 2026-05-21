"""FastAPI-based web chat interface for AWS Bill Whisperer."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from .chatbot import BillWhispererChat

app = FastAPI(title="AWS Bill Whisperer Chat")
chat = BillWhispererChat()

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AWS Bill Whisperer Chat</title>
  <style>
    body { font-family: Inter, system-ui, -apple-system; margin: 0; background: #111; color: #f2f2f2; }
    #chat { max-width: 800px; margin: 40px auto; padding: 20px; background: #1b1b1b; border-radius: 12px; }
    .msg { margin-bottom: 16px; }
    .user { color: #7dd3fc; }
    .bot { color: #a5b4fc; }
    #commands { font-size: 0.9em; margin-top: 8px; }
    input { width: calc(100% - 110px); padding: 10px; border-radius: 8px; border: none; }
    button { width: 90px; padding: 10px; margin-left: 10px; border-radius: 8px; border: none; background: #2563eb; color: white; cursor: pointer; }
  </style>
</head>
<body>
  <div id="chat">
    <div id="log"></div>
    <div style="display:flex; margin-top:20px;">
      <input id="input" placeholder="Ask about storage, compute, quick wins..." />
      <button onclick="sendMessage()">Send</button>
    </div>
  </div>

<script>
  const ws = new WebSocket(`ws://${location.host}/ws`);
  const log = document.getElementById('log');
  const input = document.getElementById('input');

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    append('bot', data.text);
    if (data.commands && data.commands.length) {
      const cmdBlock = document.createElement('div');
      cmdBlock.className = 'msg bot';
      cmdBlock.innerHTML = '<strong>Suggested commands:</strong><br>' + data.commands.map(cmd => `<code>${cmd.command}</code>`).join('<br>');
      log.appendChild(cmdBlock);
      window.scrollTo(0, document.body.scrollHeight);
    }
  };

  function append(role, text) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.innerText = `${role === 'user' ? 'you' : 'agent'}> ${text}`;
    log.appendChild(div);
    window.scrollTo(0, document.body.scrollHeight);
  }

  window.sendMessage = function() {
    const value = input.value.trim();
    if (!value) return;
    append('user', value);
    ws.send(value);
    input.value = '';
  }

  input.addEventListener('keyup', (ev) => {
    if (ev.key === 'Enter') sendMessage();
  });
</script>
</body>
</html>
"""


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive_text()
            response = await chat.ask(message)
            await websocket.send_json({
                "text": response.text,
                "commands": response.commands,
            })
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("chat_interface.chat_server:app", host="0.0.0.0", port=8000, reload=False)
