# RogoAI Chat Rotator

> Switch between multiple AI chatbots and collect their answers into a local database

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()

---

## Overview

**RogoAI Chat Rotator** is a desktop app for sending prompts to multiple Web AIs (Claude, Gemini, Grok, ChatGPT) and Local LLMs (OLLAMA, LM Studio) simultaneously — **without an API key**.

- **No API key required** — works within your existing subscriptions
- **Terms-of-service compliant** — all clipboard and browser operations are performed manually by the user
- **Knowledge asset builder** — stores all AI responses in a local SQLite database for search and reuse

---

## Key Features

### Core
- Launch up to 4 AI browser windows simultaneously (Launch Grid)
- Assign custom roles, frameworks, viewpoints, and output formats per AI
- Collect responses into a searchable local database (AND / OR / NOT search)

### Analysis
| Button | Description |
|--------|-------------|
| **Summary** | Have one AI summarize responses from others |
| **Difference** | Extract opinion differences across multiple AI responses |
| **Follow-up** | Generate a handoff spec to continue a task on a different AI |

### Local LLM Support
- Auto-send to OLLAMA and LM Studio (no manual steps needed)
- Vision model support (MoonDream, Llava-phi3, etc.)
- No API or automation restrictions for Local LLMs

### File Attachment
- Attach text files (`.txt`, `.py`, `.md`, `.json`, etc.) up to 300 KB
- Image attachment supported for Local LLM vision models
- Web AI image upload is guided as a manual step (base64 not recognized by chat UIs)

---

## Requirements

- Windows 10 / 11
- Python 3.10 or later
- Google Chrome (for Web AI)
- OLLAMA or LM Studio (optional, for Local LLM)

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/rogoai/260222_RogoAI_Chat_Rotator.git
cd 260222_RogoAI_Chat_Rotator

# 2. Install dependencies
pip install -r src/requirements.txt

# Optional: PDF support
pip install pypdf

# 3. Launch
python src/chat_rotator_v3_7c.py
```

---

## Usage

See [`docs/how_to_use_EN.md`](docs/how_to_use_EN.md) for full instructions.

**Quick start:**
1. Launch the app and click **Launch Grid** to open AI browser windows
2. Log in to each AI service (saved automatically on next launch)
3. Type your prompt and click the clipboard button for each AI
4. Paste manually (Ctrl+V) into each AI's input field and send
5. Copy each response and click the **Collect** button in the Viewer tab

---

## Terms of Service Compliance

This app is designed to respect the terms of service of all supported AI platforms.

- **No scraping, no auto-send, no auto-copy** — these are intentionally not implemented
- All prompt pasting, sending, and response copying are **manual user actions**
- Please do not modify the app in ways that would violate AI provider terms of service

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Author

**RogoAI / Take-jii** — [YouTube Channel](https://www.youtube.com/@RogoAI)

> An almost-70 creator learning AI from scratch — making tools that work for everyone.
