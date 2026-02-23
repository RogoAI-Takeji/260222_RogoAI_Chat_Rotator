# 老後AI チャットローテーター

> 複数のAIのあいだをチャットしながら飛び回るアプリ

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()
[![Release](https://img.shields.io/github/v/release/RogoAI-Takeji/260222_RogoAI_Chat_Rotator)](https://github.com/RogoAI-Takeji/260222_RogoAI_Chat_Rotator/releases/latest)

---

## ダウンロード

**exeをそのまま使いたい方はこちら：**

[📦 最新版をダウンロード（exe）](https://github.com/RogoAI-Takeji/260222_RogoAI_Chat_Rotator/releases/latest)

> Python不要。ダウンロードして解凍するだけで起動できます。

---

## 概要

**老後AI チャットローテーター**は、Claude・Gemini・Grok・ChatGPTなどの複数のWeb AIと、OLLAMAやLM StudioのLocal LLMを横断して使うためのデスクトップアプリです。

- **APIキー不要** — サブスク利用の範囲で動作（年金生活に優しい設計）
- **規約準拠** — 各AIサービスの利用規約に沿った手動操作を徹底
- **回答の資産化** — 全AIの回答をローカルデータベースに保存・検索可能

---

## 主な機能

### 基本機能
- 複数AIへの一括プロンプト送信（最大4画面同時展開）
- フレームワーク・ビューポイント・出力フォーマットの指定
- AI回答のデータベース保存・ビューワー・AND/OR/NOT検索

### 分析機能
| 機能 | 説明 |
|------|------|
| **Summary** | 複数AI回答を別のAIに要約させる |
| **Difference** | 複数AI回答の意見の相違を抽出する |
| **Follow-up** | 利用制限中のAIのタスクを別AIに引き継ぐ仕様書を生成 |

### Local LLM 対応
- OLLAMA / LM Studio に自動送信
- 画像認識モデル（MoonDream・Llava-phi3 等）対応
- **画像埋め込み回答（CF_HTML形式）の取り込みに対応**
- Local LLMのみAPIや自動処理の制約なし

---

## 動作環境

- Windows 10 / 11
- Python 3.10 以上
- Google Chrome（Web AI 用）
- OLLAMA または LM Studio（Local LLM を使う場合）

---

## インストール・起動

```bash
# 1. リポジトリをクローン
git clone https://github.com/RogoAI-Takeji/260222_RogoAI_Chat_Rotator.git
cd 260222_RogoAI_Chat_Rotator

# 2. 依存パッケージをインストール
pip install -r src/requirements.txt

# Windows: 画像埋め込み回答取り込みに必要
pip install pywin32

# 3. アプリを起動
python src/chat_rotator_v3_7f.py
```

---

## 使い方

詳細は [`docs/how_to_use_JP.md`](docs/how_to_use_JP.md) を参照してください。

---

## AI サービスの利用規約について

本アプリは各AIサービスの利用規約を遵守するよう設計されています。

- スクレーピング・自動送信・自動コピーは**実装していません**
- プロンプトの貼り付け・送信・回答のコピーはすべて**ユーザーの手動操作**です
- 利用規約に違反する改変・改修はご遠慮ください

---

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照してください。

---

## 作者

**老後AI / たけ爺** — [YouTube チャンネル](https://www.youtube.com/@RogoAI)

> アラセブンティーが見よう見まねで最先端のAIに挑戦するチャンネルです。
