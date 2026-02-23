# CHANGELOG

## [v3.7f] - 2026-02-23

### バグ修正
- **ChatGPT回答が取り込めないバグを修正**
  - Base64パターンによる誤検知でChatGPT英語回答がブロックされていた問題を修正
- **画像埋め込み回答（CF_HTML形式）の取り込みに対応**
  - ChatGPTが画像付きで回答した場合、クリップボードにプレーンテキストが存在しない問題を修正
  - Windows CF_HTML形式からテキストを抽出するフォールバック処理を追加
  - 対応に `pywin32` が必要（`pip install pywin32`）
- **DB LIST 複数行選択中にタイマーで選択が解除されるバグを修正**
  - 2.5秒タイマーによる `tree.clear()` が選択状態を破壊していた問題を修正
  - `_refresh_viewer`（タイマー用）と `_force_refresh_viewer`（操作用）を分離
- **DB LIST 右クリック削除でクラッシュするバグを修正**
  - `QTreeWidgetItem` の破棄済み参照アクセスによる `RuntimeError` を修正
  - `msg_id` ベースの検索方式に変更
- **QDialogButtonBox スタイル警告を修正**
  - `Could not parse stylesheet` 警告の原因だった `bb.setStyleSheet(STYLE)` を修正

### 新機能
- **Follow-up 引き継ぎ仕様書ダイアログ**（VIEWERのアクションバーに追加）
  - 複数行選択 → 引き継ぎ先AIをセレクトボックスで指定 → 仕様書プロンプトを自動生成
  - アクションバーのボタン順を `Summary / Difference / Follow-up` に統一
- **Local LLM 画像送信対応**
  - Ollama ネイティブ形式（`images` キー）で画像を送信するよう修正
  - 画像送信時は `think` / `options` パラメータを自動除外（ビジョンモデル対応）
- **DB LIST ページネーション**（100件/ページ）
- **DB LIST 全セッション表示**（従来は当日セッションのみ）
- **日付検索対応**（`date=2026-02` 形式）

### 依存関係
- `pywin32>=306`（Windows、画像埋め込み回答取り込みに必要）
  ```
  pip install pywin32
  ```

---

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
