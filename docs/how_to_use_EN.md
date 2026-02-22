# User Guide — RogoAI Chat Rotator

## Table of Contents

1. [Launching the App](#1-launching-the-app)
2. [Configuring Web AI Services](#2-configuring-web-ai-services)
3. [Basic Usage (Sender Tab)](#3-basic-usage-sender-tab)
4. [Collecting Responses (Viewer Tab)](#4-collecting-responses-viewer-tab)
5. [Advanced Features](#5-advanced-features)
6. [Adding Local LLMs](#6-adding-local-llms)
7. [Adding New Web AI Services](#7-adding-new-web-ai-services)
8. [FAQ](#8-faq)

---

## 1. Launching the App

```bash
python src/chat_rotator_v3_7c.py
```

The database and config files are created automatically on first launch.

---

## 2. Configuring Web AI Services

Open the **AI SERVICES** tab and check the AIs you want to use.

| Default AI | URL |
|-----------|-----|
| Claude | https://claude.ai |
| Gemini | https://gemini.google.com |
| Grok | https://grok.com |
| ChatGPT | https://chatgpt.com |

- **Roles** can be freely customized (e.g., "Legal Expert", "Cost Analyst")
- New Web AIs can be added via the **Add Web AI** button

---

## 3. Basic Usage (Sender Tab)

### 3-1. Launching AI Browser Windows

1. Check up to 4 AIs you want to use
2. Click **[LAUNCH GRID]**
3. Chrome windows open for each selected AI
4. Log in to each service on first launch (auto-login on subsequent launches)

### 3-2. Sending a Prompt

1. Type your prompt in the input field
2. Optionally select a Framework, Viewpoint, and Output Format
3. Click the clipboard button (📋) on each AI card
4. The dedicated browser window comes to the front
5. Press **Ctrl+V** (or right-click → Paste) to paste the prompt
6. Manually click the send button
7. Repeat for all AIs

> ⚠️ Manual operation is by design — it ensures compliance with each AI service's terms of service

### 3-3. Prompt Assist Options

| Option | Choices |
|--------|---------|
| **Framework** | SWOT / PREP / MECE / 5W1H / PDCA / STAR |
| **Viewpoint** | Legal / UX / Cost / Technical / Market / Custom |
| **Output Format** | Bullets / Table / Diagram / Brief / Steps |
| **Per-AI Instructions** | Add individual instructions for each AI |
| **Prompt Preview** | Confirm the exact prompt before sending |

---

## 4. Collecting Responses (Viewer Tab)

1. Once an AI responds, click its **copy button** on the AI's website
   - For Gemini, the copy button is inside a 3-button group
2. Click the **[Collect]** button in the Viewer tab
3. Repeat for all AIs

### Database Columns

| Column | Description |
|--------|-------------|
| Timestamp | When the response was collected |
| Service | AI name |
| Label | Classification tag |
| Question | Prompt sent |
| Response | AI's answer |
| Source | CB (clipboard) / CLI (Local LLM) / LOC (local) |

### Search Syntax

- **Space-separated** → AND search
- **Comma-separated** → OR search
- **! prefix** → NOT search (e.g., `!climate change`)

---

## 5. Advanced Features

### Summary

Have one AI summarize responses from others.

1. Select multiple responses in the Viewer (Shift/Ctrl+click)
2. Click **[Summary]** → select the target AI
3. Send the generated prompt manually from the Sender tab

### Difference

Extract opinion differences across multiple AI responses.

1. Select responses to compare
2. Click **[Difference]** → select the target AI
3. Click **[Run Analysis]** → send the generated prompt manually

### Follow-up

Hand off a task from a rate-limited AI to another AI.

1. Select the relevant responses in the Viewer
2. Click **[Follow-up]** → specify the target AI
3. Send the generated handoff spec manually

---

## 6. Adding Local LLMs

### OLLAMA

```bash
# Start the OLLAMA server (in a separate terminal)
ollama serve

# Pull a model (examples)
ollama pull llama3.1
ollama pull qwen3
```

1. Go to **AI SERVICES** → click **[Add Local LLM]**
2. Select OLLAMA and enter the model name (or use **Get Model List**)
3. In the Sender tab, use the **⚡ button** (individual) or **[Send All Local LLM]** (broadcast)

### LM Studio

1. Launch LM Studio and enable the local server (default: `localhost:1234`)
2. Follow the same steps as above

---

## 7. Adding New Web AI Services

1. Go to **AI SERVICES** → click **[Add Web AI]**
2. Enter the name, URL, and role
3. Return to the Sender tab and use Launch Grid to open it

**Tip for agent-style AIs (e.g., Manus):**
If the response contains graphs or tables that can't be copied normally, instruct the AI:

```
Please present the report as copyable plain text inside an HTML block.
```

---

## 8. FAQ

**Q: Why isn't auto-send supported?**
A: Scraping and automated interactions are prohibited by most AI service terms of service. This app is intentionally designed to stay within the safe zone.

**Q: The service name isn't displayed correctly — what do I do?**
A: This happens when signature matching fails. Right-click the record → "Change Service Name" to fix it manually. You can also adjust the matching threshold in the SETTINGS tab.

**Q: I'm getting errors with Local LLM — how do I fix it?**
A: Check that the model name is correct. Use the **Get Model List** button to see which models are actually available.

**Q: Can I send images to Web AIs?**
A: Image sending via clipboard is not supported. Please upload images manually using the upload button on each AI's website. Automatic image sending is supported for Local LLMs only.
