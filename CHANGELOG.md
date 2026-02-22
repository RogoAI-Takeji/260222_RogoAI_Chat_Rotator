# CHANGELOG

## [v3.7] - 2026-02-22 — GitHub 初回公開

### 初回リリース
- Web AI（Claude / Gemini / Grok / ChatGPT）への一括プロンプト送信
- Local LLM（OLLAMA / LM Studio）への自動送信対応
- AI回答のデータベース保存・ビューワー機能
- Summary / Difference / Follow-up 分析機能
- テキスト・画像ファイル添付機能
- フレームワーク・ビューポイント・出力フォーマット指定
- AI間タスク引き継ぎ（フォローアップ仕様書）機能

### v3.7 での変更
- GRID LAUNCH バーを SENDER タブのプロンプト上部に移動
  - 起動前：[LAUNCH GRID] + 配置プレビュー表示
  - 起動後：各AIフォーカスボタン + [⏹ 強制終了]
  - フォーカスボタン押下でプロンプトコピー・ウィンドウフォーカス・ハイライト
  - 強制終了は子プロセスまで確実に終了
- SETTINGS タブをスクロール対応
- Chrome stylesheet パース警告を修正
