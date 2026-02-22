#!/usr/bin/env python3
"""
RogoAI Chat Rotator v3.7
========================
【v3.5 変更】
  ■ GRID LAUNCH バーを SENDER タブのプロンプト上部に移動
    - 起動前：[LAUNCH GRID] + 配置プレビュー（左上:Claude 右上:Gemini…）
    - 起動後：[↖Claude][↗Gemini][↙Grok][↘ChatGPT] + [⏹ 強制終了]
    - 各AIフォーカスボタン押下 → プロンプトコピー + ウィンドウフォーカス + ハイライト
    - 強制終了：taskkill /F /T /PID で子プロセスまで確実に終了
    - AIカードのチェック状態を優先（DBのenabled値に依存しない）
    - スレッド→メインスレッド通知を grid_done シグナル経由に統一
  ■ SETTINGS タブをスクロール対応
  ■ Chrome stylesheet パース警告を修正

【依存】
  pip install pyperclip PyQt6 requests
  pip install pypdf  # PDF対応（任意）
"""

import sys, os, sqlite3, hashlib, threading, time, json, re, subprocess
import requests, base64, mimetypes
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

# ─────────────────────────────────────────────────────────────────────
# クリップボード
# ─────────────────────────────────────────────────────────────────────
_cb_backend = None

def _init_cb() -> bool:
    global _cb_backend
    try:
        import pyperclip; pyperclip.paste(); _cb_backend = "pyperclip"; return True
    except: pass
    try:
        import tkinter as tk; _cb_backend = "tkinter"; return True
    except: pass
    return False

def _get_cb() -> str:
    if _cb_backend == "pyperclip":
        import pyperclip; return pyperclip.paste() or ""
    elif _cb_backend == "tkinter":
        import tkinter as tk
        r = tk.Tk(); r.withdraw()
        try: return r.clipboard_get()
        except: return ""
        finally: r.destroy()
    return ""

def _set_cb(text: str):
    if _cb_backend == "pyperclip":
        import pyperclip; pyperclip.copy(text)
    elif _cb_backend == "tkinter":
        import tkinter as tk
        r = tk.Tk(); r.withdraw()
        r.clipboard_clear(); r.clipboard_append(text)
        r.update(); time.sleep(0.3); r.destroy()


# ─────────────────────────────────────────────────────────────────────
# AI サービス定義
# ─────────────────────────────────────────────────────────────────────
DEFAULT_AI_SERVICES = {
    "Claude":  {"type":"browser","url":"https://claude.ai",
                "role":"コーディング専門家","enabled":True,"color":"#818cf8"},
    "Gemini":  {"type":"browser","url":"https://gemini.google.com",
                "role":"総合監督","enabled":True,"color":"#34d399"},
    "Grok":    {"type":"browser","url":"https://grok.com",
                "role":"マーケット解析","enabled":True,"color":"#f472b6"},
    "ChatGPT": {"type":"browser","url":"https://chatgpt.com",
                "role":"アドバイザー","enabled":True,"color":"#60a5fa"},
    "Ollama":  {"type":"local","url":"http://localhost:11434",
                "role":"ローカル処理","enabled":False,"color":"#fb923c",
                "model":"llama3","endpoint":"/api/chat"},
    "LMStudio":{"type":"local","url":"http://localhost:1234",
                "role":"ローカル処理","enabled":False,"color":"#fbbf24",
                "model":"local-model","endpoint":"/v1/chat/completions"},
}

FRAMEWORKS = [
    ("none","─ Free ─"),("swot","SWOT"),("prep","PREP"),
    ("mece","MECE"),("5w1h","5W1H"),("pdca","PDCA"),("star","STAR"),
]
VIEWPOINTS = [
    ("none","─ None ─"),("legal","法的"),("ux","UX"),
    ("cost","コスト"),("tech","技術"),("market","市場"),("custom","カスタム…"),
]
OUTPUT_FORMATS = [
    ("none","─ Free ─"),("bullets","箇条書き"),("table","表"),
    ("diagram","図解"),("brief","簡潔"),("steps","ステップ"),
]
FRAMEWORK_PROMPTS = {
    "swot":"SWOT分析（強み・弱み・機会・脅威）のフレームワークを使って回答してください。",
    "prep":"PREP法（結論→理由→具体例→結論）の構成で回答してください。",
    "mece":"MECE（漏れなくダブりなく）の原則で整理して回答してください。",
    "5w1h":"5W1H（いつ・どこで・誰が・何を・なぜ・どのように）の観点で回答してください。",
    "pdca":"PDCA（計画・実行・評価・改善）のサイクルで整理して回答してください。",
    "star":"STAR法（状況・課題・行動・結果）の構成で回答してください。",
}
VIEWPOINT_PROMPTS = {
    "legal":"法的リスクの観点から回答してください。",
    "ux":"UX・ユーザー体験の視点から回答してください。",
    "cost":"コスト効率・ROIの観点から回答してください。",
    "tech":"技術的実現性・工数の観点から回答してください。",
    "market":"市場動向・競合分析の観点から回答してください。",
}
FORMAT_PROMPTS = {
    "bullets":"回答は箇条書き形式でまとめてください。",
    "table":"回答は表形式（Markdown table）でまとめてください。",
    "diagram":"可能であればASCIIアートの図解を含めて回答してください。",
    "brief":"回答は簡潔に1段落（200字以内）でまとめてください。",
    "steps":"回答はステップ順（Step 1, Step 2…）で整理してください。",
}

SIG_Q_LEN   = 50
SIG_AI_LEN  = 32
SIG_PATTERN = re.compile(
    r'\[AI:(?P<ai>[^\]]+)\]\[Q:(?P<q>[^\]]*)\]\[TS:(?P<ts>[^\]]+)\]'
)


# ─────────────────────────────────────────────────────────────────────
# ファイル添付
# ─────────────────────────────────────────────────────────────────────
@dataclass
class AttachedFile:
    path:str; name:str; size:int; ftype:str
    content_text:str; content_b64:str; mime_type:str; error:str

class FileAttachment:
    TEXT_EXT  = {'.txt','.md','.markdown','.csv','.py','.js','.ts','.jsx','.tsx',
                 '.html','.htm','.css','.json','.yaml','.yml','.xml','.sql',
                 '.sh','.bat','.ps1','.java','.c','.cpp','.h','.rs','.go',
                 '.rb','.php','.swift','.kt','.r','.tex','.rst','.log','.ini','.toml'}
    IMAGE_EXT = {'.jpg','.jpeg','.png','.gif','.webp','.bmp'}
    PDF_EXT   = {'.pdf'}
    MAX_TEXT  = 300_000
    MAX_IMG   = 20_000_000

    @staticmethod
    def load(path:str) -> AttachedFile:
        p=Path(path); name=p.name
        size=p.stat().st_size if p.exists() else 0
        ext=p.suffix.lower()
        mime=mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        def ok(ft,ct="",cb=""): return AttachedFile(str(p),name,size,ft,ct,cb,mime,"")
        def er(ft,msg):         return AttachedFile(str(p),name,size,ft,"","",mime,msg)
        if not p.exists(): return er("unsupported","ファイルが見つかりません")
        if ext in FileAttachment.TEXT_EXT:
            if size>FileAttachment.MAX_TEXT: return er("text",f"サイズ超過({size//1024}KB)")
            for enc in ['utf-8','utf-8-sig','cp932','latin-1']:
                try: return ok("text",ct=p.read_text(encoding=enc))
                except: pass
            return er("text","エンコード検出失敗")
        elif ext in FileAttachment.IMAGE_EXT:
            if size>FileAttachment.MAX_IMG: return er("image","画像が大きすぎます")
            return ok("image",cb=base64.b64encode(p.read_bytes()).decode())
        elif ext in FileAttachment.PDF_EXT:
            for lib in ["pypdf","PyPDF2"]:
                try:
                    if lib=="pypdf":
                        import pypdf; r=pypdf.PdfReader(str(p))
                        return ok("pdf",ct="\n".join(pg.extract_text() or "" for pg in r.pages))
                    else:
                        import PyPDF2
                        with open(p,'rb') as f:
                            r=PyPDF2.PdfReader(f)
                            return ok("pdf",ct="\n".join(pg.extract_text() or "" for pg in r.pages))
                except ImportError: continue
                except Exception as e: return er("pdf",str(e))
            return er("pdf","pip install pypdf でPDF対応を追加できます")
        return er("unsupported",f"非対応形式: {ext}")

    @staticmethod
    def build_prompt_block(files:list) -> str:
        parts=[]
        for af in files:
            if af.error: parts.append(f"\n### [{af.name}] ⚠ {af.error}\n")
            elif af.ftype in ("text","pdf"):
                parts.append(f"\n### {af.name}\n```\n{af.content_text}\n```\n")
            elif af.ftype=="image":
                parts.append(f"\n### {af.name} [画像 - ブラウザで手動アップロード]\n")
        return ("\n\n---\n【添付ファイル】\n"+"".join(parts)) if parts else ""

    @staticmethod
    def build_local_messages(prompt_text:str, files:list) -> list:
        images=[af for af in files if af.ftype=="image" and not af.error]
        extra=FileAttachment.build_prompt_block([af for af in files if af.ftype in ("text","pdf")])
        full_text=prompt_text+extra
        if images:
            # Ollama /api/chat ネイティブ形式: imagesキーにbase64リスト
            return [{
                "role":"user",
                "content":full_text,
                "images":[af.content_b64 for af in images]
            }]
        else:
            # 画像なし: string形式
            return [{"role":"user","content":full_text}]

    @staticmethod
    def fmt_size(n:int) -> str:
        if n<1024: return f"{n}B"
        if n<1024**2: return f"{n//1024}KB"
        return f"{n//1024//1024}MB"


# ─────────────────────────────────────────────────────────────────────
# データベース
# ─────────────────────────────────────────────────────────────────────
class ChatDatabase:
    LABELS = [
        ("summary",     "📄 Summary",    "要約対象"),
        ("difference",  "🔀 Difference", "差分材料"),
        ("code_snippet","💻 Code",        "コード"),
        ("reference",   "📎 Reference",  "参考資料"),
        ("user_note",   "✏️  Note",       "メモ"),
    ]

    def __init__(self, db_path:str="chat_rotator.db"):
        self.db_path=db_path
        self._lock=threading.Lock()
        self._conn=sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory=sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self):
        c=self._conn
        # マイグレーション（既存DBへの列追加）
        for tbl,col,dfn in [
            ("messages","metadata", "TEXT NOT NULL DEFAULT '{}'"),
            ("messages","ts",       "TEXT DEFAULT ''"),
        ]:
            ex=[r[1] for r in c.execute(f"PRAGMA table_info({tbl})").fetchall()]
            if ex and col not in ex:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {dfn}")
        c.commit()

        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   INTEGER NOT NULL,
                ts           TEXT DEFAULT '',
                role         TEXT NOT NULL,
                service      TEXT NOT NULL,
                content      TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                detected_at  TEXT NOT NULL,
                metadata     TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS ai_services (
                name TEXT PRIMARY KEY,
                config TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_msg_hash ON messages(content_hash);
            CREATE INDEX IF NOT EXISTS idx_msg_sess ON messages(session_id,detected_at);
            CREATE INDEX IF NOT EXISTS idx_msg_ts   ON messages(ts);
        """)
        c.commit()
        self._init_ai_services()

    def _init_ai_services(self):
        now=datetime.now().isoformat()
        for name,cfg in DEFAULT_AI_SERVICES.items():
            if not self._conn.execute(
                "SELECT name FROM ai_services WHERE name=?",(name,)
            ).fetchone():
                self._conn.execute(
                    "INSERT INTO ai_services(name,config,updated_at) VALUES(?,?,?)",
                    (name,json.dumps(cfg,ensure_ascii=False),now)
                )
        self._conn.commit()

    # ── Questions ─────────────────────────────────────────────────────
    # ── Questions（messagesテーブルに統合）──────────────────────────────
    def save_question(self, session_id:int, ts:str, content:str,
                      framework="",viewpoint="",output_fmt="") -> int:
        """質問をmessagesテーブルにlabel=questionで保存"""
        now=datetime.now().isoformat()
        meta=json.dumps({"label":"question","fw":framework,"vp":viewpoint,
                         "fmt":output_fmt},ensure_ascii=False)
        h=hashlib.md5(f"Q:{ts}:{content[:80]}".encode()).hexdigest()
        with self._lock:
            if self._conn.execute("SELECT id FROM messages WHERE content_hash=?",(h,)).fetchone():
                row=self._conn.execute("SELECT id FROM messages WHERE content_hash=?",(h,)).fetchone()
                return row[0]
            cur=self._conn.execute(
                "INSERT INTO messages(session_id,ts,role,service,content,content_hash,detected_at,metadata)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (session_id,ts,"user","User",content,h,now,meta)
            )
            self._conn.commit()
        return cur.lastrowid

    def find_question(self, ts:str, q_prefix:str) -> Optional[dict]:
        """ts + q_prefix でmessagesテーブルから質問行を突合"""
        row=self._conn.execute(
            "SELECT * FROM messages WHERE ts=? AND content LIKE ? AND service='User' LIMIT 1",
            (ts, q_prefix+"%")
        ).fetchone()
        if not row:
            row=self._conn.execute(
                "SELECT * FROM messages WHERE ts=? AND service='User' LIMIT 1",(ts,)
            ).fetchone()
        return dict(row) if row else None

    # ── Messages ──────────────────────────────────────────────────────
    def save_message(self, session_id:int, role:str, service:str,
                     content:str, metadata:dict=None, ts:str=None) -> bool:
        # serviceも含めてhash化 → 同内容でもAIが違えば別レコード
        h=hashlib.md5(f"{service}:{content}".encode()).hexdigest()
        now=datetime.now().isoformat()
        meta=json.dumps(metadata or {},ensure_ascii=False)
        with self._lock:
            if self._conn.execute("SELECT id FROM messages WHERE content_hash=?",(h,)).fetchone():
                return False
            self._conn.execute(
                "INSERT INTO messages(session_id,ts,role,service,"
                "content,content_hash,detected_at,metadata) VALUES(?,?,?,?,?,?,?,?)",
                (session_id,ts or "",role,service,content,h,now,meta)
            )
            self._conn.commit()
        return True

    def set_label(self, msg_id:int, label:Optional[str]):
        with self._lock:
            row=self._conn.execute("SELECT metadata FROM messages WHERE id=?",(msg_id,)).fetchone()
            if not row: return
            meta=json.loads(row[0] or "{}")
            if label is None: meta.pop("label",None)
            else: meta["label"]=label
            self._conn.execute(
                "UPDATE messages SET metadata=? WHERE id=?",
                (json.dumps(meta,ensure_ascii=False),msg_id)
            )
            self._conn.commit()

    def get_messages(self, session_id:int, limit:int=500) -> list:
        sql="""
            SELECT id,role,service,content,detected_at,metadata,ts,
                   CASE WHEN json_extract(metadata,'$.label')='question' THEN content ELSE '' END AS question_content
            FROM messages
            WHERE session_id=?
            ORDER BY detected_at ASC LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql,(session_id,limit)).fetchall()]

    def get_all_messages(self) -> list:
        """全セッション全件取得"""
        sql="""
            SELECT id,role,service,content,detected_at,metadata,ts,
                   CASE WHEN json_extract(metadata,'$.label')='question' THEN content ELSE '' END AS question_content
            FROM messages
            ORDER BY detected_at ASC
        """
        return [dict(r) for r in self._conn.execute(sql).fetchall()]

    def search_messages(self, query:str, limit:int=200) -> list:
        """
        項目名参照検索構文（スペース区切りですべてAND結合）:
          service=claude      → SERVICE列がclaudeを含む
          service=!grok       → SERVICE列がgrokを含まない
          label=question      → LABELがquestionを含む
          label=!unknown      → LABELがunknownを含まない
          content=python      → 本文にpythonを含む
          content=!error      → 本文にerrorを含まない
          python              → 項目名なし → content部分一致（従来互換）

        例: service=claude content=python label=!unknown
        """
        import re as _re

        # 項目名→SQLカラムのマッピング
        FIELD_MAP = {
            "service": "service",
            "label":   "json_extract(metadata,'$.label')",
            "content": "content",
            "src":     "json_extract(metadata,'$.source')",
            "date":    "detected_at",
        }

        clauses: list = []
        params:  list = []

        tokens = query.split()
        for token in tokens:
            if not token:
                continue
            # 項目名=値 の形式か判定
            m = _re.match(r'^(\w+)=(!?)(.+)$', token)
            if m and m.group(1).lower() in FIELD_MAP:
                field  = FIELD_MAP[m.group(1).lower()]
                negate = m.group(2) == "!"
                vals   = [v.strip() for v in m.group(3).split(",") if v.strip()]
                if negate:
                    # NOT: すべての値を含まない（AND結合）
                    sub = [f"({field} NOT LIKE ? OR {field} IS NULL)" for _ in vals]
                    clauses.append(f"({' AND '.join(sub)})")
                    params.extend(f"%{v}%" for v in vals)
                else:
                    # OR: いずれかの値を含む
                    sub = [f"{field} LIKE ?" for _ in vals]
                    clauses.append(f"({' OR '.join(sub)})")
                    params.extend(f"%{v}%" for v in vals)
            else:
                # 項目名なし → content部分一致（従来互換）
                if token.startswith("!") and len(token) > 1:
                    clauses.append("content NOT LIKE ?")
                    params.append(f"%{token[1:]}%")
                else:
                    clauses.append("content LIKE ?")
                    params.append(f"%{token}%")

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"""
            SELECT id,role,service,content,detected_at,metadata,ts,
                   CASE WHEN json_extract(metadata,'$.label')='question' THEN content ELSE '' END AS question_content
            FROM messages
            WHERE {where}
            ORDER BY detected_at DESC LIMIT ?
        """
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def count_unknown(self, session_id:int=None) -> int:
        sql=("SELECT COUNT(*) FROM messages WHERE service='Unknown'"
             " AND (json_extract(metadata,'$.label') IS NULL OR json_extract(metadata,'$.label')='')")
        args=()
        if session_id: sql+=" AND session_id=?"; args=(session_id,)
        return self._conn.execute(sql,args).fetchone()[0]

    def delete_unknown(self, session_id:int=None) -> int:
        sql=("DELETE FROM messages WHERE service='Unknown'"
             " AND (json_extract(metadata,'$.label') IS NULL OR json_extract(metadata,'$.label')='')")
        args=()
        if session_id: sql+=" AND session_id=?"; args=(session_id,)
        with self._lock:
            cur=self._conn.execute(sql,args); self._conn.commit()
        return cur.rowcount

    def update_service(self, msg_id:int, new_service:str):
        """メッセージのサービス名を手動変更"""
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET service=? WHERE id=?",
                (new_service, msg_id)
            )
            self._conn.commit()

    def delete_message(self, msg_id:int):
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE id=?",(msg_id,)); self._conn.commit()

    def get_stats(self) -> dict:
        total=self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        q_cnt=self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE json_extract(metadata,'$.label')='question'"
        ).fetchone()[0]
        active=self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE service!='Unknown'"
            " OR (json_extract(metadata,'$.label') IS NOT NULL AND json_extract(metadata,'$.label')!='')"
        ).fetchone()[0]
        by_svc=self._conn.execute(
            "SELECT service,COUNT(*) FROM messages"
            " WHERE json_extract(metadata,'$.label')!='question' OR json_extract(metadata,'$.label') IS NULL"
            " GROUP BY service ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {"total":total,"active":active,"unknown_unlabeled":total-active,
                "questions":q_cnt,"by_service":{r[0]:r[1] for r in by_svc}}

    # ── AI Services ───────────────────────────────────────────────────
    def get_ai_services(self) -> dict:
        rows=self._conn.execute("SELECT name,config FROM ai_services").fetchall()
        return {str(r[0]):json.loads(r[1]) for r in rows}

    def save_ai_service(self, name, config:dict):
        name=str(name).strip()
        if not name or name in ("True","False","0","1"):
            print(f"[DEBUG] save_ai_service rejected invalid name={name!r}", flush=True)
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO ai_services(name,config,updated_at) VALUES(?,?,?)",
            (name,json.dumps(config,ensure_ascii=False),datetime.now().isoformat())
        )
        self._conn.commit()
        print(f"[DEBUG] save_ai_service saved name={name!r}", flush=True)

    def delete_ai_service(self, name):
        print(f"[DEBUG] delete_ai_service name={name!r} type={type(name)}", flush=True)
        # bool・数値残骸を全削除
        self._conn.execute("DELETE FROM ai_services WHERE typeof(name)='integer'")
        # 文字列nameも削除
        if not isinstance(name, bool):
            self._conn.execute("DELETE FROM ai_services WHERE name=?",(str(name),))
        self._conn.commit()
        rows=self._conn.execute("SELECT name FROM ai_services").fetchall()
        print(f"[DEBUG] remaining: {[r[0] for r in rows]}", flush=True)

    # ── Session ───────────────────────────────────────────────────────
    def get_or_create_session(self, name:str=None) -> int:
        now=datetime.now().isoformat()
        today=datetime.now().strftime("%Y-%m-%d")
        name=name or f"Session {today}"
        with self._lock:
            row=self._conn.execute(
                "SELECT id FROM sessions WHERE name LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%{today}%",)
            ).fetchone()
            if row:
                self._conn.execute("UPDATE sessions SET updated_at=? WHERE id=?",(now,row[0]))
                self._conn.commit(); return row[0]
            cur=self._conn.execute(
                "INSERT INTO sessions(name,created_at,updated_at) VALUES(?,?,?)",(name,now,now)
            )
            self._conn.commit(); return cur.lastrowid

    def get_setting(self,key,default=None):
        row=self._conn.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
        return row[0] if row else default

    def set_setting(self,key,value):
        self._conn.execute(
            "INSERT OR REPLACE INTO settings(key,value,updated_at) VALUES(?,?,?)",
            (key,value,datetime.now().isoformat())
        ); self._conn.commit()


# ─────────────────────────────────────────────────────────────────────
# AI サービス判定（シグネチャ優先）
# ─────────────────────────────────────────────────────────────────────
class AIServiceDetector:
    # テキスト内容でのパターンマッチ
    PATTERNS = {
        "Claude" :[r"Anthropic",r"I['']m Claude",r"私はClaude",r"claude\.ai"],
        "Gemini" :[r"Google DeepMind",r"Google AI",r"I['']m Gemini",r"私はGemini",r"gemini\.google"],
        "Grok"   :[r"xAI",r"I['']m Grok",r"私はGrok",r"grok\.com"],
        "ChatGPT":[r"OpenAI",r"I['']m ChatGPT",r"As an AI( language model)?",
                   r"chatgpt\.com",r"ChatGPT",r"GPT-4",r"GPT-3"],
    }
    def __init__(self):
        self._c={s:[re.compile(p,re.I) for p in ps] for s,ps in self.PATTERNS.items()}
    def detect(self, text:str) -> str:
        # シグネチャがあればそれを優先
        m=SIG_PATTERN.search(text)
        if m: return m.group("ai")
        # テキストパターンマッチ
        for svc,pats in self._c.items():
            if any(p.search(text) for p in pats): return svc
        return "Unknown"


# ─────────────────────────────────────────────────────────────────────
# LocalLLM クライアント
# ─────────────────────────────────────────────────────────────────────
class LocalLLMClient:
    @staticmethod
    def chat(base_url:str, endpoint:str, model:str, messages:list, timeout:int=300) -> str:
        url=base_url.rstrip("/")+endpoint
        # 画像あり（imagesキー存在）の場合はthink/optionsを除外
        has_images=any("images" in m for m in messages)
        payload={"model":model,"messages":messages,"stream":False}
        if not has_images:
            payload["think"]=False
            payload["options"]={"num_predict":2048}
        print(f"[DEBUG] LocalLLMClient.chat url={url} model={model} has_images={has_images} timeout={timeout}", flush=True)
        print(f"[DEBUG] payload keys={list(payload.keys())} messages_count={len(messages)}", flush=True)
        try:
            print("[DEBUG] requests.post start", flush=True)
            r=requests.post(url,json=payload,timeout=timeout)
            print(f"[DEBUG] requests.post done, status={r.status_code}", flush=True)
            r.raise_for_status()
            data=r.json()
            print(f"[DEBUG] response keys={list(data.keys())}", flush=True)
            if "message" in data:
                content=data["message"].get("content","")
                import re as _re
                content=_re.sub(r'<think>.*?</think>','',content,flags=_re.DOTALL).strip()
                print(f"[DEBUG] content length={len(content)}", flush=True)
                return content
            if "choices" in data: return data["choices"][0]["message"]["content"]
        except requests.exceptions.ConnectionError:
            print("[DEBUG] ConnectionError", flush=True)
            return f"[接続エラー] {base_url} に接続できません"
        except requests.exceptions.Timeout:
            print(f"[DEBUG] Timeout after {timeout}s", flush=True)
            return f"[タイムアウト] {timeout}秒"
        except Exception as e:
            print(f"[DEBUG] Exception: {e}", flush=True)
            return f"[エラー] {e}"
        return ""

    @staticmethod
    def list_models(base_url:str) -> list:
        print(f"[DEBUG] list_models base_url={base_url}", flush=True)
        for ep in ["/api/tags","/v1/models"]:
            url=base_url.rstrip("/")+ep
            try:
                print(f"[DEBUG] list_models trying {url}", flush=True)
                r=requests.get(url,timeout=3)
                print(f"[DEBUG] list_models {url} status={r.status_code}", flush=True)
                if r.status_code==200:
                    data=r.json()
                    print(f"[DEBUG] list_models response keys={list(data.keys())}", flush=True)
                    if "models" in data:
                        names=[m["name"] for m in data["models"]]
                        print(f"[DEBUG] list_models found {len(names)} models: {names}", flush=True)
                        return names
                    if "data" in data:
                        names=[m["id"] for m in data["data"]]
                        print(f"[DEBUG] list_models found {len(names)} models: {names}", flush=True)
                        return names
                    print(f"[DEBUG] list_models 200 but no 'models'/'data' key, keys={list(data.keys())}", flush=True)
            except Exception as e:
                print(f"[DEBUG] list_models {url} exception: {e}", flush=True)
                continue
        print("[DEBUG] list_models returning []", flush=True)
        return []


# ─────────────────────────────────────────────────────────────────────
# プロンプトビルダー
# ─────────────────────────────────────────────────────────────────────
class PromptBuilder:
    @staticmethod
    def build(base_prompt:str, role:str="",
              framework:str="none", viewpoint:str="none",
              output_fmt:str="none", oneshot:str="",
              custom_viewpoint:str="") -> str:
        parts=[]
        if role and role.strip():
            parts.append(f"あなたは「{role}」の専門家として回答してください。")
        if framework!="none" and framework in FRAMEWORK_PROMPTS:
            parts.append(FRAMEWORK_PROMPTS[framework])
        if viewpoint=="custom" and custom_viewpoint.strip():
            parts.append(f"{custom_viewpoint.strip()}の観点から回答してください。")
        elif viewpoint!="none" and viewpoint in VIEWPOINT_PROMPTS:
            parts.append(VIEWPOINT_PROMPTS[viewpoint])
        if output_fmt!="none" and output_fmt in FORMAT_PROMPTS:
            parts.append(FORMAT_PROMPTS[output_fmt])
        prompt=(("\n".join(parts)+"\n\n---\n\n") if parts else "")+base_prompt.strip()
        if oneshot.strip():
            prompt+=f"\n\n---\n【理想的な回答例】\n{oneshot.strip()}"
        return prompt

    @staticmethod
    def add_signature(prompt:str, ai_name:str, question:str, ts:str) -> str:
        """
        プロンプト末尾にシグネチャ指示を追加。
        AIに回答の冒頭にシグネチャ行を含めるよう指示する。

        シグネチャ形式:
          [AI:Claude][Q:Pythonのデコレータを詳しく...][TS:2026-02-18T14:23:15]
        """
        q_safe  = question[:SIG_Q_LEN].replace("]","）").replace("[","（")
        ai_safe = ai_name[:SIG_AI_LEN].replace("]","）").replace("[","（")
        sig     = f"[AI:{ai_safe}][Q:{q_safe}][TS:{ts}]"
        return (prompt +
                "\n\n---\n"
                "お手数ですが、回答の冒頭に下記の管理用タグ行を1行そのままコピーして記載してから、通常通り回答を続けてください。\n"
                f"{sig}\n"
                "---")

    @staticmethod
    def parse_signature(text:str) -> Optional[dict]:
        m=SIG_PATTERN.search(text)
        if not m: return None
        return {"ai":m.group("ai"),"q":m.group("q"),"ts":m.group("ts")}

    # 指示ブロック全体を除去するパターン
    _SIG_BLOCK_PATTERN = re.compile(
        r'\n*-{3,}\n【システム指示.*?変更・省略しないでください：\n.*?$',
        re.DOTALL
    )

    @staticmethod
    def strip_signature(text:str) -> str:
        """シグネチャ行と指示ブロック全体を除去"""
        # まず指示ブロックごと除去（---\n【システム指示...】〜シグネチャ行まで）
        text = PromptBuilder._SIG_BLOCK_PATTERN.sub("", text)
        # 残ったシグネチャ行も除去
        text = SIG_PATTERN.sub("", text)
        return text.strip()


# ─────────────────────────────────────────────────────────────────────
# クリップボード監視
# ─────────────────────────────────────────────────────────────────────
class ClipboardMonitor:
    def __init__(self, db:ChatDatabase, poll:float=0.8, on_new:Callable=None):
        self.db=db; self.poll=poll; self.on_new=on_new
        self.detector=AIServiceDetector()
        self._running=False; self._last_hash=""
        self.session_id:Optional[int]=None
        self.stats=dict(detected=0,saved=0,dup=0,unknown=0,matched=0)
        self.manual_mode=True   # True=手動取り込み（デフォルト）/ False=常時監視

    def start_session(self, name=None):
        self.session_id=self.db.get_or_create_session(name)
        try: self._last_hash=hashlib.md5(_get_cb().encode()).hexdigest()
        except: pass

    def start(self):
        self._running=True
        threading.Thread(target=self._loop,daemon=True).start()

    def stop(self): self._running=False

    def capture_once(self, hint_ai:str=""):
        """手動モード用：今のクリップボードを1回だけ取り込む"""
        try:
            text=_get_cb()
            if text:
                # hint_aiも含めてhash化 → 同文でも別AIなら通過
                h=hashlib.md5(f"{hint_ai}:{text}".encode()).hexdigest()
                if h!=self._last_hash:
                    self._last_hash=h; self.stats["detected"]+=1
                    self._process(text, hint_ai=hint_ai)
                    return True
        except: pass
        return False

    def _loop(self):
        while self._running:
            try:
                if not self.manual_mode:   # 常時監視モードのみポーリング
                    text=_get_cb()
                    if text:
                        h=hashlib.md5(text.encode()).hexdigest()
                        if h!=self._last_hash:
                            self._last_hash=h; self.stats["detected"]+=1; self._process(text)
            except: pass
            time.sleep(self.poll)

    # 機密っぽい文字列パターン（保存前チェック用）
    _SENSITIVE_PATTERNS = re.compile(
        r'(sk-[A-Za-z0-9]{20,})'           # OpenAI APIキー
        r'|(AKIA[A-Z0-9]{16})'              # AWS アクセスキー
        r'|(eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})' # JWT
        r'|([A-Za-z0-9+/]{40,}={0,2})'     # Base64長文字列（トークン系）
        r'|(ghp_[A-Za-z0-9]{36})'          # GitHub PAT
        r'|(xoxb-[A-Za-z0-9-]{50,})'       # Slack Bot Token
        r'|(AIza[A-Za-z0-9_-]{35})'        # Google API Key
    )

    # 送信プロンプト判定パターン：指示ブロック「---\nお手数ですが〜---」を含むもの
    _PROMPT_BLOCK_PATTERN = re.compile(
        r'\n*-{3,}\n.{0,20}回答の冒頭に.{0,60}管理用タグ',
        re.DOTALL
    )

    def _process(self, text:str, hint_ai:str=""):
        # 送信プロンプトをスキップ（指示ブロックが含まれているものが送信プロンプト）
        if ClipboardMonitor._PROMPT_BLOCK_PATTERN.search(text):
            return

        sig=PromptBuilder.parse_signature(text)
        service="Unknown"
        matched_ts=None
        if sig:
            # シグネチャ突合成功
            service=sig["ai"]
            matched_ts=sig["ts"]
            self.stats["matched"]+=1
        else:
            # シグネチャなし → テキストパターンで検出
            service=self.detector.detect(text)
            if service=="Unknown" and hint_ai:
                # フォールバック：コピー時に記録したAI名を使用
                service=hint_ai
                self._log_fn(f"💡  シグネチャなし → {hint_ai}（フォールバック）") if hasattr(self,'_log_fn') else None

        clean=PromptBuilder.strip_signature(text)

        # 機密文字列ガード（APIキー・JWT・トークン類は保存しない）
        if ClipboardMonitor._SENSITIVE_PATTERNS.search(clean):
            if self.on_new: self.on_new("sensitive", "⚠️", clean[:40])
            return  # 保存せずスキップ

        meta={"source":"clipboard"}
        if matched_ts: meta["ts"]=matched_ts

        saved=self.db.save_message(
            self.session_id,"assistant",service,clean,meta,matched_ts
        )
        if saved:
            self.stats["saved"]+=1
            if service=="Unknown": self.stats["unknown"]+=1
            if self.on_new: self.on_new("saved",service,clean)
        else:
            self.stats["dup"]+=1


# ─────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────
try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QTextEdit,
        QLineEdit, QComboBox, QCheckBox, QSplitter, QTabWidget,
        QMenu, QDialog, QDialogButtonBox, QMessageBox, QStatusBar,
        QFrame, QScrollArea, QGroupBox, QPlainTextEdit,
        QListWidget, QListWidgetItem
    )
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
    from PyQt6.QtGui  import QColor, QCursor, QAction
    HAS_QT=True
except ImportError:
    HAS_QT=False
    class QMainWindow: pass
    class QObject: pass
    class QWidget: pass
    class QDialog: pass
    class QTreeWidgetItem: pass
    class Qt:
        class Orientation: Horizontal=1; Vertical=2
        class ItemDataRole: UserRole=256
        class ContextMenuPolicy: CustomContextMenu=3
        class ScrollBarPolicy: ScrollBarAlwaysOff=1
    class QTimer: pass
    class QColor: pass
    class QCursor: pass
    class QAction: pass
    class QMenu: pass
    class QComboBox: pass
    class QCheckBox: pass
    class QLineEdit: pass
    class QPlainTextEdit: pass
    class QGroupBox: pass
    class QSplitter: pass
    class QTabWidget: pass
    class QScrollArea: pass
    class QStatusBar: pass
    class QFrame:
        class Shape: NoFrame=0
    class QApplication: pass
    class QMessageBox:
        class StandardButton: Yes=0; No=1; Cancel=2
    class QListWidget: pass
    class QListWidgetItem: pass
    def pyqtSignal(*a,**kw): return None


STYLE = """
* { font-family:'Segoe UI','Noto Sans JP','Yu Gothic UI',sans-serif; font-size:13px; }
QMainWindow,QWidget { background:#1c1c1c; color:#dcdcdc; }
QDialog { background:#222222; border:1px solid #3a3a3a; color:#dcdcdc; }

QTabWidget::pane { border:none; border-top:1px solid #333333; margin:0px; padding:0px; }
QTabBar::tab { background:transparent; color:#777777; padding:5px 16px; border:none; letter-spacing:0.06em; font-size:11px; }
QTabBar::tab:selected { color:#f2f2f2; border-bottom:2px solid #4ade80; }
QTabBar::tab:hover { color:#cccccc; }

QGroupBox { border:1px solid #333333; border-radius:4px; margin-top:12px; padding:5px 6px 5px; color:#999999; font-size:11px; font-weight:600; letter-spacing:0.09em; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 5px; color:#aaaaaa; }

QTextEdit,QPlainTextEdit { background:#252525; border:1px solid #383838; border-radius:4px; color:#e2e2e2; padding:8px; selection-background-color:#3a4a5a; }
QTextEdit:focus,QPlainTextEdit:focus { border-color:#505050; background:#282828; }
QLineEdit { background:#252525; border:1px solid #383838; border-radius:4px; color:#dcdcdc; padding:5px 8px; min-height:28px; }
QLineEdit:focus { border-color:#505050; }

QComboBox { background:#252525; border:1px solid #383838; border-radius:4px; color:#cccccc; padding:4px 8px; min-height:26px; }
QComboBox:hover { border-color:#505050; color:#eeeeee; }
QComboBox::drop-down { border:none; width:20px; }
QComboBox QAbstractItemView { background:#2c2c2c; border:1px solid #404040; color:#dcdcdc; selection-background-color:#3a3a3a; outline:none; }

QCheckBox { color:#bbbbbb; spacing:7px; }
QCheckBox::indicator { width:14px; height:14px; border:1px solid #444444; border-radius:3px; background:#252525; }
QCheckBox::indicator:checked { background:#4ade80; border-color:#4ade80; }

QPushButton { background:#2a2a2a; border:1px solid #404040; border-radius:4px; color:#cccccc; padding:6px 14px; min-height:28px; font-weight:500; }
QPushButton:hover { background:#333333; border-color:#606060; color:#f0f0f0; }
QPushButton:pressed { background:#222222; }
QPushButton:disabled { color:#505050; border-color:#2e2e2e; background:#222222; }

#btn_primary { background:#1e3d2a; border-color:#4ade80; color:#6ef09a; font-weight:600; }
#btn_primary:hover { background:#224430; color:#90f8b0; }
#btn_copy { background:#1e2e42; border-color:#60a5fa; color:#80bfff; }
#btn_copy:hover { background:#253548; }
#btn_danger { background:#3d1e1e; border-color:#f87171; color:#ff9999; }
#btn_danger:hover { background:#4a2525; }
#btn_local { background:#3d2a1e; border-color:#fb923c; color:#ffaa66; }
#btn_local:hover { background:#4a3325; }
#btn_file { background:#28253d; border-color:#a78bfa; color:#c4b0ff; }
#btn_file:hover { background:#302e4a; }

#card_copy { background:#1e3228; border-color:#3ade70; color:#5aee88; min-height:26px; padding:3px 8px; font-size:14px; border-radius:4px; }
#card_copy:hover { background:#224030; border-color:#5aee88; }
#card_send { background:#3a2a14; border-color:#e8822c; color:#f8a050; min-height:26px; padding:3px 8px; font-size:14px; border-radius:4px; }
#card_send:hover { background:#462e18; }

QTreeWidget { background:#1e1e1e; border:none; outline:none; font-size:12px; alternate-background-color:#232323; color:#e0e0e0; }
QTreeWidget::item { padding:6px 6px; border-bottom:1px solid #2e2e2e; min-height:22px; }
QTreeWidget::item:selected { background:#1e4032; color:#ffffff; }
QTreeWidget::item:hover { background:#2c2c2c; }
QHeaderView::section { background:#181818; border:none; border-bottom:1px solid #383838; border-right:1px solid #2a2a2a; color:#aaaaaa; font-size:11px; font-weight:700; letter-spacing:0.07em; padding:7px 8px; }

QSplitter::handle { background:#2e2e2e; }
QSplitter::handle:horizontal { width:1px; }
QSplitter::handle:vertical   { height:1px; }
QStatusBar { background:#141414; border-top:1px solid #2a2a2a; color:#777777; font-size:11px; padding:2px 8px; }

QScrollBar:vertical   { background:transparent; width:7px; margin:0; }
QScrollBar:horizontal { background:transparent; height:7px; margin:0; }
QScrollBar::handle:vertical,QScrollBar::handle:horizontal { background:#444444; border-radius:4px; min-height:24px; }
QScrollBar::handle:vertical:hover,QScrollBar::handle:horizontal:hover { background:#555555; }
QScrollBar::add-line,QScrollBar::sub-line { height:0; width:0; }

QMenu { background:#272727; border:1px solid #404040; padding:4px; border-radius:5px; }
QMenu::item { padding:7px 20px; border-radius:3px; color:#cccccc; }
QMenu::item:selected { background:#343434; color:#f2f2f2; }
QMenu::separator { height:1px; background:#383838; margin:3px 8px; }

#fw_btn { background:#232323; border:1px solid #383838; border-radius:4px; color:#888888; padding:4px 10px; font-size:11px; min-height:24px; }
#fw_btn:checked { background:#1e3d28; border-color:#4ade80; color:#6ef09a; font-weight:600; }
#fw_btn:hover { border-color:#555555; color:#cccccc; }

#log_area { background:#161616; border:none; color:#888888; font-size:11px; font-family:'Consolas','Courier New',monospace; }
#ai_card { background:#212121; border:1px solid #333333; border-radius:5px; }
#ai_card:hover { border-color:#454545; }
#detail_panel { background:#191919; border-radius:3px; }
#file_list { background:#202020; border:1px solid #333333; border-radius:4px; color:#bbbbbb; font-size:11px; }
#file_list::item { padding:3px 6px; }
#file_list::item:selected { background:#303030; color:#eeeeee; }
QLabel { color:#cccccc; }
"""

# ─────────────────────────────────────────────────────────────────────
# Grid Launcher  ─  4分割ブラウザ起動
# ─────────────────────────────────────────────────────────────────────
class GridLauncher:
    """
    Chromeプロファイルを使って複数AIブラウザセッションを4分割起動する。
    各AIにプロファイルを割り当て、ログイン情報を永続化する。
    ブラウザの自動操作は一切行わない（URLを開くだけ）。
    """
    PROFILE_DIR_NAME = "RogoAI_Profiles"

    # Chrome実行ファイルの候補パス
    CHROME_CANDIDATES = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        # Linux
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]

    # グリッドレイアウト定義 (col, row) → 0-indexed
    GRID_POSITIONS = [
        (0, 0),  # 左上
        (1, 0),  # 右上
        (0, 1),  # 左下
        (1, 1),  # 右下
    ]

    def __init__(self, chrome_path: str = ""):
        self.chrome_path = chrome_path or self._find_chrome()
        self._processes: list = []   # 起動済みプロセス

    def _find_chrome(self) -> str:
        for p in self.CHROME_CANDIDATES:
            if os.path.exists(p):
                return p
        return ""

    @staticmethod
    def get_profile_dir() -> str:
        """プロファイル保存先（%APPDATA%\RogoAI_Profiles or ~/.RogoAI_Profiles）"""
        if os.name == "nt":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
        else:
            base = os.path.expanduser("~")
        d = os.path.join(base, GridLauncher.PROFILE_DIR_NAME)
        os.makedirs(d, exist_ok=True)
        return d

    def launch(self, ai_services: dict, screen_w: int, screen_h: int,
               on_log: Callable = None) -> list:
        def log(msg):
            if on_log: on_log(msg)

        if not self.chrome_path:
            log("❌  Chromeが見つかりません。SETTINGSでパスを指定してください。")
            return []

        targets = [
            (name, cfg) for name, cfg in ai_services.items()
            if cfg.get("type") == "browser" and cfg.get("enabled") and cfg.get("url")
        ][:4]

        if not targets:
            log("⚠️  有効なBrowser AIがありません。AI SERVICESで設定してください。")
            return []

        profile_base = self.get_profile_dir()
        cols, rows = 2, 2
        cell_w = screen_w // cols
        cell_h = screen_h // rows

        launched = []
        for i, (name, cfg) in enumerate(targets):
            col, row = self.GRID_POSITIONS[i]
            x = col * cell_w
            y = row * cell_h
            profile_path = os.path.join(profile_base, f"profile_{name.lower()}")
            os.makedirs(profile_path, exist_ok=True)

            args = [
                self.chrome_path,
                f"--user-data-dir={profile_path}",
                f"--window-position={x},{y}",
                f"--window-size={cell_w},{cell_h}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                cfg["url"],
            ]
            try:
                kwargs = {}
                if os.name == "nt":
                    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                proc = subprocess.Popen(args, **kwargs)
                pid = proc.pid
                self._processes.append((name, proc, pid))
                launched.append(name)
                log(f"🖥️  {name}  →  ({x},{y})  {cell_w}×{cell_h}  PID={pid}")
                time.sleep(0.8)
            except Exception as e:
                log(f"❌  {name} 起動失敗: {e}")

        return launched

    def focus_window(self, ai_name: str) -> bool:
        """
        指定AIのChromeウィンドウにフォーカスを移す（Windows専用）。
        タイトルバーにURLのドメインが含まれることを利用して検索。
        """
        if os.name != "nt":
            return False
        try:
            import ctypes
            user32 = ctypes.windll.user32

            keywords = {
                "Claude":  ["claude.ai", "Claude"],
                "Gemini":  ["gemini.google", "Gemini"],
                "Grok":    ["grok.com", "Grok"],
                "ChatGPT": ["chatgpt.com", "ChatGPT"],
            }
            kws = keywords.get(ai_name, [ai_name])

            found = []
            def enum_cb(hwnd, _):
                if user32.IsWindowVisible(hwnd):
                    buf = ctypes.create_unicode_buffer(256)
                    user32.GetWindowTextW(hwnd, buf, 256)
                    title = buf.value
                    if any(k.lower() in title.lower() for k in kws):
                        found.append(hwnd)
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

            if found:
                hwnd = found[0]
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return True
        except Exception:
            pass
        return False

    def terminate_all(self):
        for entry in self._processes:
            name, proc, pid = entry[0], entry[1], entry[2] if len(entry) > 2 else None
            if os.name == "nt" and pid:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True, text=True
                    )
                except Exception:
                    pass
            try: proc.terminate()
            except Exception: pass
            try: proc.kill()
            except Exception: pass
        self._processes.clear()

    def is_alive(self) -> list:
        alive = []
        for entry in self._processes:
            name, proc = entry[0], entry[1]
            if proc.poll() is None:
                alive.append(name)
        return alive


if HAS_QT:
    class Signals(QObject):
        new_message      = pyqtSignal(str,str)
        local_result     = pyqtSignal(str,str)
        log_message      = pyqtSignal(str)
        status_update    = pyqtSignal()
        grid_done        = pyqtSignal(object, object)
        reset_local_btns = pyqtSignal(list)  # 完了したai_nameリスト


# ─────────────────────────────────────────────────────────────────────
# AI設定ダイアログ（Web用）
# ─────────────────────────────────────────────────────────────────────
class WebAIConfigDialog(QDialog):
    def __init__(self,parent,name:str="",config:dict=None):
        super().__init__(parent)
        self.setWindowTitle("Add Web AI Service"); self.setMinimumWidth(460); self.setStyleSheet(STYLE)
        config=config or {}; lay=QVBoxLayout(self); lay.setSpacing(8)
        hint=QLabel("🌐  Browser AI（Claude / Gemini / Grok / ChatGPT など）\nURLを開いてプロンプトを手動貼り付けするタイプです。")
        hint.setStyleSheet("color:#888; font-size:11px;"); lay.addWidget(hint)
        lay.addWidget(QLabel("Name")); self.name_e=QLineEdit(name); lay.addWidget(self.name_e)
        lay.addWidget(QLabel("URL")); self.url_e=QLineEdit(config.get("url","")); self.url_e.setPlaceholderText("例: https://claude.ai"); lay.addWidget(self.url_e)
        lay.addWidget(QLabel("Role（役割・任意）")); self.role_e=QLineEdit(config.get("role","")); self.role_e.setPlaceholderText("例: コーディング専門家"); lay.addWidget(self.role_e)
        lay.addWidget(QLabel("Color（カード色）")); self.color_e=QLineEdit(config.get("color","#c0c0c0")); self.color_e.setPlaceholderText("例: #60a5fa"); lay.addWidget(self.color_e)
        btns=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject); lay.addWidget(btns)

    def get_config(self):
        name=self.name_e.text().strip()
        cfg={"type":"browser","url":self.url_e.text().strip(),
             "role":self.role_e.text().strip(),"color":self.color_e.text().strip() or "#c0c0c0","enabled":True}
        return name,cfg


# ─────────────────────────────────────────────────────────────────────
# AI設定ダイアログ（Local LLM用）
# ─────────────────────────────────────────────────────────────────────
class LocalAIConfigDialog(QDialog):
    _models_fetched = pyqtSignal(list)

    def __init__(self,parent,name:str="",config:dict=None):
        super().__init__(parent)
        self.setWindowTitle("Add Local LLM Service"); self.setMinimumWidth(460); self.setStyleSheet(STYLE)
        self._fetch_cancelled=False
        self._models_fetched.connect(self._on_models_fetched)
        config=config or {}; lay=QVBoxLayout(self); lay.setSpacing(8)
        hint=QLabel("⚡  Local LLM（Ollama / LM Studio など）\nAPIに直接送信するタイプです。事前にサーバーを起動してください。")
        hint.setStyleSheet("color:#888; font-size:11px;"); lay.addWidget(hint)
        lay.addWidget(QLabel("Name")); self.name_e=QLineEdit(name); self.name_e.setPlaceholderText("例: Ollama-qwen3"); lay.addWidget(self.name_e)
        lay.addWidget(QLabel("URL / Base URL")); self.url_e=QLineEdit(config.get("url","http://localhost:11434")); lay.addWidget(self.url_e)
        lay.addWidget(QLabel("Model")); self.model_e=QLineEdit(config.get("model","")); self.model_e.setPlaceholderText("「モデル一覧を取得」で選択するか直接入力"); lay.addWidget(self.model_e)
        lay.addWidget(QLabel("Endpoint")); self.ep_e=QLineEdit(config.get("endpoint","/api/chat")); lay.addWidget(self.ep_e)
        lay.addWidget(QLabel("Role（役割・任意）")); self.role_e=QLineEdit(config.get("role","")); self.role_e.setPlaceholderText("例: ローカル処理担当"); lay.addWidget(self.role_e)
        lay.addWidget(QLabel("Color（カード色）")); self.color_e=QLineEdit(config.get("color","#fb923c")); self.color_e.setPlaceholderText("例: #fb923c"); lay.addWidget(self.color_e)
        # モデル取得
        self.fetch_btn=QPushButton("モデル一覧を取得"); self.fetch_btn.setObjectName("btn_local")
        self.fetch_btn.clicked.connect(self._fetch); lay.addWidget(self.fetch_btn)
        self.model_cb=QComboBox()
        self.model_cb.currentTextChanged.connect(lambda t: self.model_e.setText(t) if t else None)
        lay.addWidget(self.model_cb)
        btns=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject); lay.addWidget(btns)

    def _fetch(self):
        url=self.url_e.text().strip()
        if not url: return
        self.fetch_btn.setText("取得中…"); self.fetch_btn.setEnabled(False)
        self._fetch_cancelled=False
        def _do():
            try: models=LocalLLMClient.list_models(url)
            except Exception: models=[]
            self._models_fetched.emit(models)
        threading.Thread(target=_do,daemon=True).start()

    def _on_models_fetched(self, models:list):
        if self._fetch_cancelled: return
        try:
            self.model_cb.clear()
            if models: self.model_cb.addItems(models); self.fetch_btn.setText(f"{len(models)}件取得")
            else: self.fetch_btn.setText("取得失敗（モデルなし）")
            self.fetch_btn.setEnabled(True)
        except RuntimeError: pass

    def closeEvent(self,event): self._fetch_cancelled=True; super().closeEvent(event)
    def reject(self): self._fetch_cancelled=True; super().reject()
    def accept(self): self._fetch_cancelled=True; super().accept()

    def get_config(self):
        name=self.name_e.text().strip()
        cfg={"type":"local","url":self.url_e.text().strip(),"model":self.model_e.text().strip(),
             "endpoint":self.ep_e.text().strip(),"role":self.role_e.text().strip(),
             "color":self.color_e.text().strip() or "#fb923c","enabled":True}
        return name,cfg


# ─────────────────────────────────────────────────────────────────────
# AI設定ダイアログ（Edit用・Web/Local自動判別）
# ─────────────────────────────────────────────────────────────────────
class AIConfigDialog(QDialog):
    _models_fetched = pyqtSignal(list)

    def __init__(self,parent,name:str="",config:dict=None):
        super().__init__(parent)
        self.setWindowTitle("AI Service Config"); self.setMinimumWidth(460); self.setStyleSheet(STYLE)
        self._fetch_cancelled=False
        self._models_fetched.connect(self._on_models_fetched)
        config=config or {}; lay=QVBoxLayout(self); lay.setSpacing(8)
        lay.addWidget(QLabel("Name")); self.name_e=QLineEdit(name); lay.addWidget(self.name_e)
        lay.addWidget(QLabel("URL / Base URL")); self.url_e=QLineEdit(config.get("url","")); lay.addWidget(self.url_e)
        lay.addWidget(QLabel("Role（役割）")); self.role_e=QLineEdit(config.get("role","")); lay.addWidget(self.role_e)
        lay.addWidget(QLabel("Color")); self.color_e=QLineEdit(config.get("color","#c0c0c0")); lay.addWidget(self.color_e)
        lay.addWidget(QLabel("Type")); self.type_cb=QComboBox()
        self.type_cb.addItems(["browser","local"]); self.type_cb.setCurrentText(config.get("type","browser"))
        self.local_g=QGroupBox("LOCAL LLM SETTINGS"); lg=QVBoxLayout(self.local_g)
        lg.addWidget(QLabel("Model")); self.model_e=QLineEdit(config.get("model","")); lg.addWidget(self.model_e)
        lg.addWidget(QLabel("Endpoint")); self.ep_e=QLineEdit(config.get("endpoint","/api/chat")); lg.addWidget(self.ep_e)
        self.fetch_btn=QPushButton("モデル一覧を取得"); self.fetch_btn.setObjectName("btn_local")
        self.fetch_btn.clicked.connect(self._fetch); lg.addWidget(self.fetch_btn)
        self.model_cb=QComboBox()
        self.model_cb.currentTextChanged.connect(lambda t: self.model_e.setText(t) if t else None)
        lg.addWidget(self.model_cb); lay.addWidget(self.local_g)
        self.type_cb.currentTextChanged.connect(self._on_type); lay.addWidget(self.type_cb)
        btns=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject); lay.addWidget(btns)
        self._on_type(self.type_cb.currentText())

    def _on_type(self,t): self.local_g.setVisible(t=="local")

    def _fetch(self):
        url=self.url_e.text().strip()
        if not url: return
        self.fetch_btn.setText("取得中…"); self.fetch_btn.setEnabled(False)
        self._fetch_cancelled=False
        def _do():
            print(f"[DEBUG] _fetch _do start url={url}", flush=True)
            try: models=LocalLLMClient.list_models(url)
            except Exception as e:
                print(f"[DEBUG] _fetch exception: {e}", flush=True)
                models=[]
            print(f"[DEBUG] _fetch _do done, emitting signal models={models}", flush=True)
            self._models_fetched.emit(models)
        threading.Thread(target=_do,daemon=True).start()

    def _on_models_fetched(self, models:list):
        print(f"[DEBUG] _on_models_fetched called cancelled={self._fetch_cancelled} models={models}", flush=True)
        if self._fetch_cancelled: return
        try:
            self.model_cb.clear()
            if models:
                self.model_cb.addItems(models)
                self.fetch_btn.setText(f"{len(models)}件取得")
            else:
                self.fetch_btn.setText("取得失敗（モデルなし）")
            self.fetch_btn.setEnabled(True)
        except RuntimeError as e:
            print(f"[DEBUG] _on_models_fetched RuntimeError: {e}", flush=True)

    def closeEvent(self,event):
        self._fetch_cancelled=True; super().closeEvent(event)

    def reject(self):
        self._fetch_cancelled=True; super().reject()

    def accept(self):
        self._fetch_cancelled=True; super().accept()

    def get_config(self):
        name=self.name_e.text().strip()
        cfg={"type":self.type_cb.currentText(),"url":self.url_e.text().strip(),
             "role":self.role_e.text().strip(),"color":self.color_e.text().strip() or "#c0c0c0","enabled":True}
        if cfg["type"]=="local":
            cfg["model"]=self.model_e.text().strip(); cfg["endpoint"]=self.ep_e.text().strip()
        return name,cfg


# ─────────────────────────────────────────────────────────────────────
# メインウィンドウ
# ─────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    NONE_LABEL = "─ 共通 ─"

    def __init__(self, db:ChatDatabase, monitor:ClipboardMonitor):
        super().__init__()
        self.db=db; self.monitor=monitor
        self.sig=Signals()
        self.sig.new_message.connect(self._on_new_message)
        self.sig.local_result.connect(self._on_local_result)
        self.sig.log_message.connect(self._append_log)
        self.sig.status_update.connect(self._update_status)
        self.sig.grid_done.connect(self._on_grid_done_global)
        self.sig.reset_local_btns.connect(self._on_reset_local_btns)

        self._custom_vp=""; self._ai_cards={}; self._attached_files=[]
        self._current_ts=None; self._current_qid=None
        self._page_current=0; self._page_size=100; self._page_all_msgs=[]
        self._grid_launcher: GridLauncher = None   # 起動後にセット
        self._pending_launcher=None; self._pending_svcs={}
        self._pending_sw=1920; self._pending_sh=1080

        self.setWindowTitle("RogoAI Chat Rotator  v3.7")
        self.resize(900,580); self.setMinimumSize(760,400); self.setStyleSheet(STYLE)
        self._build_ui(); self._load_ai_cards(); self._refresh_viewer()
        self.monitor.on_new=self._monitor_cb
        self._timer=QTimer(); self._timer.timeout.connect(self._refresh_viewer); self._timer.start(2500)
        self._restore_state()   # 起動時に前回状態を復元
        # 初回起動時のみ同意ダイアログ
        QTimer.singleShot(300, self._show_consent_if_first_run)

    # ══════════════════════════════════════════════════════════════════
    # UI 構築
    # ══════════════════════════════════════════════════════════════════
    def _build_ui(self):
        root=QWidget(); self.setCentralWidget(root)
        vb=QVBoxLayout(root); vb.setContentsMargins(0,0,0,0); vb.setSpacing(0)
        vb.addWidget(self._build_header())
        self.tabs=QTabWidget(); self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_sender_tab(),"SENDER")
        self.tabs.addTab(self._build_viewer_tab(),"VIEWER")
        self.tabs.addTab(self._build_history_tab(),"HISTORY")
        self.tabs.addTab(self._build_ai_tab(),"AI SERVICES")
        self.tabs.addTab(self._build_settings_tab(),"SETTINGS")
        vb.addWidget(self.tabs); vb.addWidget(self._build_log_bar())
        self._statusbar=QStatusBar(); self.setStatusBar(self._statusbar); self._update_status()

    def _build_header(self) -> QWidget:
        w=QWidget(); w.setFixedHeight(32)
        w.setStyleSheet("background:#111111; border-bottom:1px solid #222222;")
        h=QHBoxLayout(w); h.setContentsMargins(16,0,16,0)
        t=QLabel("CHAT ROTATOR"); t.setStyleSheet("color:#e8e8e8; font-size:12px; font-weight:700; letter-spacing:0.10em;"); h.addWidget(t)
        s=QLabel("clipboard · signature matching · per-ai prompt · local llm"); s.setStyleSheet("color:#404040; font-size:10px; margin-left:12px;"); h.addWidget(s)
        h.addStretch()
        # 取り込みモードインジケーター
        self._mode_indicator=QLabel("🛡 手動取り込み")
        self._mode_indicator.setStyleSheet("color:#4ade80; font-size:10px; margin-right:12px;")
        h.addWidget(self._mode_indicator)
        dot=QLabel("●"); dot.setStyleSheet("color:#4ade80; font-size:8px;"); h.addWidget(dot); h.addSpacing(8)
        v=QLabel("v3.7"); v.setStyleSheet("color:#333333; font-size:11px;"); h.addWidget(v)
        return w

    # ── SENDER タブ ────────────────────────────────────────────────────
    def _build_sender_tab(self) -> QWidget:
        w=QWidget(); mh=QHBoxLayout(w); mh.setContentsMargins(8,8,8,8); mh.setSpacing(8)

        # 左パネル：スクロール可能なコンテナ
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left=QWidget(); lv=QVBoxLayout(left); lv.setContentsMargins(0,4,4,4); lv.setSpacing(3)
        scroll.setWidget(left)

        # ── GRID LAUNCH バー（プロンプト上部）────────────────────────
        self._grid_bar=self._build_grid_bar()
        lv.addWidget(self._grid_bar)

        # ── PROMPT ────────────────────────────────────────────────────
        pg=QGroupBox("PROMPT"); pgl=QVBoxLayout(pg); pgl.setSpacing(3); pgl.setContentsMargins(6,6,6,4)
        self.prompt_edit=QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(
            "質問・依頼を入力してください。\n"
            "右の各AIカードの [📋] を押すと、そのAI専用プロンプトがクリップボードにコピーされます。"
        )
        self.prompt_edit.setMinimumHeight(120)
        from PyQt6.QtWidgets import QSizePolicy
        self.prompt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pcl=QWidget(); pch=QHBoxLayout(pcl); pch.setContentsMargins(0,0,0,0)
        pc_btn=QPushButton("✕ クリア"); pc_btn.setMaximumWidth(80)
        pc_btn.clicked.connect(lambda: (self.prompt_edit.clear(), self._reset_question_state()))
        pch.addStretch(); pch.addWidget(pc_btn)
        pgl.addWidget(self.prompt_edit); pgl.addWidget(pcl); lv.addWidget(pg,3)

        # ── ATTACHMENTS（PROMPT直下）────────────────────────────────
        att_g=QGroupBox("ATTACHMENTS"); att_gl=QVBoxLayout(att_g); att_gl.setSpacing(3); att_gl.setContentsMargins(6,6,6,4)
        abr=QWidget(); abrh=QHBoxLayout(abr); abrh.setContentsMargins(0,0,0,0); abrh.setSpacing(4)
        for lbl,fn,oid in [("📁 ファイル","_att_add_files","btn_file"),("📂 フォルダ","_att_add_folder","btn_file"),("✕ クリア","_att_clear","")]:
            b=QPushButton(lbl)
            if oid: b.setObjectName(oid)
            b.clicked.connect(getattr(self,fn)); abrh.addWidget(b)
        abrh.addStretch()
        att_gl.addWidget(abr)
        self._att_list=QListWidget(); self._att_list.setObjectName("file_list"); self._att_list.setMaximumHeight(36)
        self._att_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._att_list.customContextMenuRequested.connect(self._att_ctx)
        att_gl.addWidget(self._att_list)
        self._att_info=QLabel("添付なし"); self._att_info.setStyleSheet("color:#666666; font-size:11px;"); att_gl.addWidget(self._att_info)

        # 画像警告バナー（画像が含まれているときのみ表示）
        self._img_warn_row=QWidget(); iwh=QHBoxLayout(self._img_warn_row); iwh.setContentsMargins(0,2,0,0); iwh.setSpacing(4)
        self._img_warn_lbl=QLabel("🖼  画像はクリップボード経由では送れません。各AIサイトのアップロードボタンから手動でアップロードしてください。")
        self._img_warn_lbl.setStyleSheet("color:#f59e0b; font-size:10px;"); self._img_warn_lbl.setWordWrap(True)
        iwh.addWidget(self._img_warn_lbl)
        self._img_warn_row.setVisible(False)
        att_gl.addWidget(self._img_warn_row)
        lv.addWidget(att_g)

        # ── FRAMEWORK / VIEWPOINT / OUTPUT FORMAT（コンボボックス横並び1行）─
        combo_g=QGroupBox("FRAMEWORK  /  VIEWPOINT  /  OUTPUT FORMAT"); combo_gl=QVBoxLayout(combo_g)
        combo_gl.setContentsMargins(6,6,6,6); combo_gl.setSpacing(4)
        combo_row=QWidget(); combo_h=QHBoxLayout(combo_row); combo_h.setContentsMargins(0,0,0,0); combo_h.setSpacing(6)

        def _make_combo(options):
            cb=QComboBox()
            cb.setStyleSheet("background:#252525; border:1px solid #383838; border-radius:3px; color:#cccccc; font-size:11px; padding:2px 6px;")
            for key,label in options:
                cb.addItem(label, key)
            return cb

        fw_lbl=QLabel("FW:"); fw_lbl.setStyleSheet("color:#777; font-size:10px;")
        self._fw_combo=_make_combo(FRAMEWORKS)
        vp_lbl=QLabel("VP:"); vp_lbl.setStyleSheet("color:#777; font-size:10px;")
        self._vp_combo=_make_combo(VIEWPOINTS)
        fmt_lbl=QLabel("FMT:"); fmt_lbl.setStyleSheet("color:#777; font-size:10px;")
        self._fmt_combo=_make_combo(OUTPUT_FORMATS)

        combo_h.addWidget(fw_lbl); combo_h.addWidget(self._fw_combo,1)
        combo_h.addWidget(vp_lbl); combo_h.addWidget(self._vp_combo,1)
        combo_h.addWidget(fmt_lbl); combo_h.addWidget(self._fmt_combo,1)
        combo_gl.addWidget(combo_row)

        # カスタム観点入力（VP=custom時のみ表示）
        self.custom_vp_edit=QLineEdit(); self.custom_vp_edit.setPlaceholderText("カスタム観点を入力…")
        self.custom_vp_edit.setVisible(False)
        self.custom_vp_edit.textChanged.connect(lambda t: setattr(self,'_custom_vp',t))
        combo_gl.addWidget(self.custom_vp_edit)
        self._vp_combo.currentIndexChanged.connect(
            lambda: self.custom_vp_edit.setVisible(self._vp_combo.currentData()=="custom")
        )
        lv.addWidget(combo_g)

        # ── ONE-SHOT EXAMPLE（コンボ選択内容を自動反映 + 手動編集可）──
        os_g=QGroupBox("ONE-SHOT EXAMPLE  （選択内容が自動挿入・手動編集可）")
        osl=QVBoxLayout(os_g); osl.setContentsMargins(6,6,6,4); osl.setSpacing(3)
        self.oneshot_edit=QPlainTextEdit()
        self.oneshot_edit.setPlaceholderText(
            "FW/VP/FMTで選択した内容がここに自動反映されます。\n"
            "さらに理想的な回答例を追記することもできます。（省略可）"
        )
        self.oneshot_edit.setMaximumHeight(60)
        oscl=QWidget(); osch=QHBoxLayout(oscl); osch.setContentsMargins(0,0,0,0)
        osc_btn=QPushButton("✕ クリア"); osc_btn.setMaximumWidth(80)
        osc_btn.clicked.connect(self.oneshot_edit.clear)
        osch.addStretch(); osch.addWidget(osc_btn)
        osl.addWidget(self.oneshot_edit); osl.addWidget(oscl); lv.addWidget(os_g)

        # FW/VP/FMT変化時にone-shot欄へ自動反映
        def _update_oneshot_hint():
            parts=[]
            fw=self._fw_combo.currentData()
            vp=self._vp_combo.currentData()
            fmt=self._fmt_combo.currentData()
            if fw and fw!="none" and fw in FRAMEWORK_PROMPTS: parts.append(FRAMEWORK_PROMPTS[fw])
            if vp and vp not in ("none","custom") and vp in VIEWPOINT_PROMPTS: parts.append(VIEWPOINT_PROMPTS[vp])
            if fmt and fmt!="none" and fmt in FORMAT_PROMPTS: parts.append(FORMAT_PROMPTS[fmt])
            self.oneshot_edit.setPlainText("\n".join(parts))
        self._fw_combo.currentIndexChanged.connect(_update_oneshot_hint)
        self._vp_combo.currentIndexChanged.connect(_update_oneshot_hint)
        self._fmt_combo.currentIndexChanged.connect(_update_oneshot_hint)

        # _fw_btns/_vp_btns/_fmt_btns は互換性のため空dictで維持
        self._fw_btns={}; self._vp_btns={}; self._fmt_btns={}

        lv.addStretch()  # 下部の余白をstretchで吸収

        prev_btn=QPushButton("▶  プロンプトプレビュー（AI別タブ表示）"); prev_btn.setObjectName("btn_copy"); prev_btn.clicked.connect(self._show_preview); lv.addWidget(prev_btn)
        # 右パネル：AIカードスクロールエリア
        right=QWidget()
        rv=QVBoxLayout(right); rv.setContentsMargins(0,0,0,0); rv.setSpacing(4)

        ai_lbl=QLabel("TARGET AI SERVICES"); ai_lbl.setStyleSheet("color:#888888; font-size:10px; font-weight:600; letter-spacing:0.1em; padding:4px 2px 2px;"); rv.addWidget(ai_lbl)

        ai_scroll=QScrollArea(); ai_scroll.setWidgetResizable(True); ai_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ai_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ai_scroll_w=QWidget()
        self._ai_card_container=QVBoxLayout(ai_scroll_w)
        self._ai_card_container.setContentsMargins(0,0,0,0); self._ai_card_container.setSpacing(4)
        self._ai_card_container.addStretch()
        ai_scroll.setWidget(ai_scroll_w); rv.addWidget(ai_scroll)

        self.local_all_btn=QPushButton("⚡  SEND ALL LOCAL LLM"); self.local_all_btn.setObjectName("btn_local")
        self.local_all_btn.setMinimumHeight(26); self.local_all_btn.clicked.connect(self._send_to_local); rv.addWidget(self.local_all_btn)

        hint=QLabel("各AIカードの 📋 → ブラウザに貼り付け\nプロンプト変更でTSが自動リセット")
        hint.setStyleSheet("color:#555555; font-size:10px; padding:0 2px 3px;"); hint.setWordWrap(True); rv.addWidget(hint)


        # Splitter で左右幅を調整可能に
        sender_splitter=QSplitter(Qt.Orientation.Horizontal)
        self._sender_splitter=sender_splitter   # 状態保存用に参照を保持
        sender_splitter.setChildrenCollapsible(False)
        sender_splitter.addWidget(scroll)
        sender_splitter.addWidget(right)
        sender_splitter.setSizes([620, 220])
        sender_splitter.setHandleWidth(5)
        mh.addWidget(sender_splitter)
        return w

    # ── GRID BAR（SENDER上部の1行コンパクトバー）─────────────────────
    def _build_grid_bar(self) -> QWidget:
        """
        起動前：[🖥️ LAUNCH GRID]  [左上:Claude 右上:Gemini 左下:Grok 右下:ChatGPT]
        起動後：[● Claude][● Gemini][● Grok][● ChatGPT]  [⚡ 強制終了]
        """
        bar=QWidget()
        bar.setStyleSheet("background:#181818; border:1px solid #2a2a2a; border-radius:4px;")
        bar.setFixedHeight(36)
        bh=QHBoxLayout(bar); bh.setContentsMargins(8,4,8,4); bh.setSpacing(6)

        # 起動ボタン（未起動時）
        self._launch_btn=QPushButton("🖥️  LAUNCH GRID")
        self._launch_btn.setObjectName("btn_primary")
        self._launch_btn.setFixedHeight(26)
        self._launch_btn.setMinimumWidth(130)
        self._launch_btn.clicked.connect(self._launch_grid)
        bh.addWidget(self._launch_btn)

        # 配置プレビューラベル（未起動時）
        self._grid_preview=QLabel()
        self._grid_preview.setStyleSheet("color:#3a3a3a; font-size:10px;")
        self._update_grid_preview()
        bh.addWidget(self._grid_preview, 1)

        # ウィンドウ別フォーカスボタン群（起動後に表示）
        self._grid_focus_row=QWidget()
        fh=QHBoxLayout(self._grid_focus_row); fh.setContentsMargins(0,0,0,0); fh.setSpacing(4)
        self._grid_focus_btns={}   # {ai_name: QPushButton}
        self._grid_focus_row.setVisible(False)
        bh.addWidget(self._grid_focus_row, 1)

        bh.addStretch()

        # ステータス
        self._grid_status=QLabel("● 未起動")
        self._grid_status.setStyleSheet("color:#444444; font-size:10px;")
        bh.addWidget(self._grid_status)

        # 強制終了ボタン
        self._kill_grid_btn=QPushButton("⏹ 強制終了")
        self._kill_grid_btn.setObjectName("btn_danger")
        self._kill_grid_btn.setFixedHeight(26)
        self._kill_grid_btn.setEnabled(False)
        self._kill_grid_btn.clicked.connect(self._kill_grid)
        bh.addWidget(self._kill_grid_btn)

        return bar

    def _build_viewer_tab(self) -> QWidget:
        w=QWidget(); v=QVBoxLayout(w); v.setContentsMargins(0,0,0,0); v.setSpacing(0)

        # ── Search bar（1行・固定高さ）──────────────────────────────
        sb=QWidget(); sb.setFixedHeight(32)
        sb.setStyleSheet("background:#181818; border-bottom:1px solid #282828;")
        sh=QHBoxLayout(sb); sh.setContentsMargins(4,2,4,2); sh.setSpacing(4)
        self.search_edit=QLineEdit()
        self.search_edit.setPlaceholderText("例: date=2026-02  service=claude  content=python")
        self.search_edit.setToolTip(
            "【検索構文】スペース区切りですべて AND 結合\n"
            "  カンマ区切りで OR 指定も可能\n\n"
            "  date=2026-02-22      → 2026年2月22日のメッセージ\n"
            "  date=2026-02         → 2026年2月のメッセージ\n"
            "  date=!2026-02-21     → 2月21日以外\n"
            "  service=claude       → Claude の発言のみ\n"
            "  service=claude,gemini→ Claude または Gemini\n"
            "  service=!grok        → Grok を除外\n"
            "  label=question       → question ラベルのみ\n"
            "  label=!unknown       → unknown を除外\n"
            "  content=python       → 本文に python を含む\n"
            "  content=!error       → 本文から error を除外\n"
            "  src=cli              → クリップボード由来のみ\n"
            "  python               → 項目名なし → 本文の部分一致\n\n"
            "例: date=2026-02 service=claude content=python"
        )
        self.search_edit.setStyleSheet("border:1px solid #333; border-radius:3px; padding:2px 6px; background:#202020; color:#e0e0e0; font-size:12px;")
        self.search_edit.textChanged.connect(self._on_search); sh.addWidget(self.search_edit)
        # ヘルプボタン
        hb=QPushButton("? Help"); hb.setFixedSize(100,24)
        hb.setToolTip("検索ヘルプ")
        hb.setStyleSheet("border:1px solid #444; border-radius:3px; background:#2a2a2a; color:#aaa; font-size:11px;")
        def _show_search_help():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "検索ヘルプ",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  項目名参照検索   スペース区切りで AND 結合\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "【日付】\n"
                "  date=2026-02-22      → 2026年2月22日のみ\n"
                "  date=2026-02         → 2026年2月のみ\n"
                "  date=2026            → 2026年のみ\n"
                "  date=!2026-02-21     → 2月21日を除外\n\n"
                "【サービス】\n"
                "  service=claude       → Claude の発言のみ\n"
                "  service=claude,gemini→ Claude または Gemini\n"
                "  service=!grok        → Grok を除外\n\n"
                "【ラベル】\n"
                "  label=question       → question ラベルのみ\n"
                "  label=!unknown       → unknown を除外\n\n"
                "【本文】\n"
                "  content=python       → 本文に python を含む\n"
                "  content=!error       → 本文から error を除外\n"
                "  python               → 項目名なし＝本文の部分一致\n\n"
                "【その他】\n"
                "  src=cli              → クリップボード由来のみ\n\n"
                "【組み合わせ例】\n"
                "  date=2026-02 service=claude\n"
                "    → 2月の Claude 発言のみ\n\n"
                "  date=2026-02-22 service=claude,gemini content=python\n"
                "    → 2月22日の Claude か Gemini で python を含む\n\n"
                "  service=!grok label=!unknown\n"
                "    → Grok除外・unknown除外\n\n"
                "【ページ表示】\n"
                "  100件ずつ表示  ← 前ページ  → 次ページ"
            )
        hb.clicked.connect(_show_search_help); sh.addWidget(hb)
        rb=QPushButton("DB更新"); rb.setFixedSize(76,24); rb.clicked.connect(self._refresh_viewer); sh.addWidget(rb)
        v.addWidget(sb)

        # ── ページナビゲーションバー ──────────────────────────────────
        pb=QWidget(); pb.setFixedHeight(28)
        pb.setStyleSheet("background:#161616; border-bottom:1px solid #252525;")
        ph=QHBoxLayout(pb); ph.setContentsMargins(6,2,6,2); ph.setSpacing(6)
        self._page_prev=QPushButton("◀ 前"); self._page_prev.setFixedSize(60,22)
        self._page_prev.setStyleSheet("border:1px solid #383838; border-radius:3px; background:#222; color:#888; font-size:11px;")
        self._page_prev.clicked.connect(self._page_go_prev)
        ph.addWidget(self._page_prev)
        self._page_label=QLabel("1 / 1  (0件)"); self._page_label.setStyleSheet("color:#666; font-size:11px;")
        ph.addWidget(self._page_label,1)
        self._page_next=QPushButton("次 ▶"); self._page_next.setFixedSize(60,22)
        self._page_next.setStyleSheet("border:1px solid #383838; border-radius:3px; background:#222; color:#888; font-size:11px;")
        self._page_next.clicked.connect(self._page_go_next)
        ph.addWidget(self._page_next)
        v.addWidget(pb)
        v.addWidget(sb)

        # ── DB list + 右パネル（splitter、余白ゼロ）──────────────────
        splitter=QSplitter(Qt.Orientation.Horizontal)
        splitter.setContentsMargins(0,0,0,0)
        self.tree=QTreeWidget(); self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["TIME","SERVICE","LABEL","QUESTION","ANSWER","SRC"])
        for i,w2 in enumerate([105,72,68,155,185,45]): self.tree.setColumnWidth(i,w2)
        self.tree.setRootIsDecorated(False); self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)                          # ← ソート有効
        self.tree.header().setSortIndicatorShown(True)             # ← ソート矢印表示
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)     # ← デフォルトTIME昇順
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_ctx)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.tree)

        cw=QWidget(); cv=QVBoxLayout(cw); cv.setContentsMargins(0,0,0,0); cv.setSpacing(0)
        self._q_badge=QLabel(""); self._q_badge.setStyleSheet("color:#666666; font-size:10px; padding:3px 10px 1px; letter-spacing:0.08em;"); cv.addWidget(self._q_badge)
        self._q_view=QTextEdit(); self._q_view.setReadOnly(True); self._q_view.setMaximumHeight(56)
        self._q_view.setPlaceholderText("質問行を選択すると表示")
        self._q_view.setStyleSheet("background:#181818; border:none; border-bottom:1px solid #2a2a2a; color:#9a9a9a; font-size:11px; padding:3px 8px;"); cv.addWidget(self._q_view)
        self._content_badge=QLabel(""); self._content_badge.setStyleSheet("color:#777777; font-size:10px; padding:3px 10px 1px; letter-spacing:0.08em;"); cv.addWidget(self._content_badge)
        self._content_view=QTextEdit(); self._content_view.setReadOnly(True); self._content_view.setPlaceholderText("← select a message"); cv.addWidget(self._content_view)
        copy_btn=QPushButton("Copy Answer to Clipboard"); copy_btn.setObjectName("btn_copy"); copy_btn.setFixedHeight(28)
        copy_btn.clicked.connect(lambda: _set_cb(self._content_view.toPlainText())); cv.addWidget(copy_btn)
        splitter.addWidget(cw); splitter.setSizes([660,420]); v.addWidget(splitter)

        # ── アクションバー（Summary / Difference / 取り込み / Unknown削除）──────
        action_bar=QWidget(); action_bar.setFixedHeight(36)
        action_bar.setStyleSheet("background:#161616; border-top:1px solid #2a2a2a;")
        ab=QHBoxLayout(action_bar); ab.setContentsMargins(6,4,6,4); ab.setSpacing(5)
        hint_lbl=QLabel("Ctrl/Shift+クリックで複数選択 →"); hint_lbl.setStyleSheet("color:#505050; font-size:10px;"); ab.addWidget(hint_lbl)
        sum_btn=QPushButton("📄  Summary"); sum_btn.setObjectName("btn_primary"); sum_btn.setFixedHeight(26)
        sum_btn.setToolTip("選択した複数AI回答を統合要約"); sum_btn.clicked.connect(lambda: self._analyze_selected("summary")); ab.addWidget(sum_btn)
        diff_btn=QPushButton("🔀  Difference"); diff_btn.setObjectName("btn_primary"); diff_btn.setFixedHeight(26)
        diff_btn.setToolTip("共通点・相違点を分析"); diff_btn.clicked.connect(lambda: self._analyze_selected("difference")); ab.addWidget(diff_btn)
        fu_btn=QPushButton("🔗  Follow-up"); fu_btn.setObjectName("btn_copy"); fu_btn.setFixedHeight(26)
        fu_btn.setToolTip("選択行を元に引き継ぎ仕様書を自動生成してSENDERにセット"); fu_btn.clicked.connect(self._set_handover_prompt); ab.addWidget(fu_btn)
        ab.addStretch()
        # 手動取り込みボタン（手動モード時に使う）
        self._capture_btn=QPushButton("📥 取り込み"); self._capture_btn.setFixedHeight(26)
        self._capture_btn.setStyleSheet(
            "background:#1a3a2a; border:1px solid #2d6a4f; border-radius:3px;"
            "color:#4ade80; font-size:11px; font-weight:600; padding:0 10px;"
        )
        self._capture_btn.setToolTip("今のクリップボードを1回取り込む（手動モード専用）")
        self._capture_btn.clicked.connect(self._manual_capture)
        ab.addWidget(self._capture_btn)
        # コンテンツ表示クリア
        clr_btn=QPushButton("✕ 表示クリア"); clr_btn.setFixedHeight(26)
        clr_btn.setStyleSheet("background:#1e1e1e; border:1px solid #383838; border-radius:3px; color:#666666; font-size:10px; padding:0 8px;")
        clr_btn.setToolTip("右側の内容表示をクリア（DBは変更しない）")
        clr_btn.clicked.connect(self._clear_content_view)
        ab.addWidget(clr_btn)
        # 一括削除ボタン
        bulk_del_btn=QPushButton("🗑 選択削除"); bulk_del_btn.setObjectName("btn_danger"); bulk_del_btn.setFixedHeight(26)
        bulk_del_btn.setToolTip("選択中の行をすべて削除"); bulk_del_btn.clicked.connect(self._bulk_delete)
        ab.addWidget(bulk_del_btn)
        cu_btn=QPushButton("🗑 Unknown"); cu_btn.setObjectName("btn_danger"); cu_btn.setFixedSize(90,26)
        cu_btn.setToolTip("ラベルなし Unknown を一括削除"); cu_btn.clicked.connect(self._clean_unknown); ab.addWidget(cu_btn)
        v.addWidget(action_bar)
        return w

    # ── HISTORY タブ ───────────────────────────────────────────────────
    def _build_history_tab(self) -> QWidget:
        w=QWidget(); v=QVBoxLayout(w); v.setContentsMargins(8,8,8,8); v.setSpacing(6)
        self.stats_label=QLabel(); self.stats_label.setStyleSheet("color:#888888; font-size:12px; line-height:1.8;"); self.stats_label.setWordWrap(True); v.addWidget(self.stats_label)
        rb=QPushButton("統計を更新"); rb.clicked.connect(self._refresh_stats); v.addWidget(rb); v.addStretch()
        self._refresh_stats(); return w

    # ── AI SERVICES タブ ───────────────────────────────────────────────
    def _build_ai_tab(self) -> QWidget:
        w=QWidget(); v=QVBoxLayout(w); v.setContentsMargins(8,8,8,8); v.setSpacing(6)
        desc=QLabel("AI サービスの追加・編集・削除。\nBrowser: URLを開く（手動貼り付け）\nLocal: Ollama/LM Studioに直接送信")
        desc.setStyleSheet("color:#666666; font-size:11px; line-height:1.6;"); v.addWidget(desc)
        br=QWidget(); bh=QHBoxLayout(br); bh.setContentsMargins(0,0,0,0); bh.setSpacing(6)
        ab_web=QPushButton("🌐  Add Web Service"); ab_web.setObjectName("btn_primary")
        ab_web.clicked.connect(self._add_web_service); bh.addWidget(ab_web)
        ab_loc=QPushButton("⚡  Add Local LLM"); ab_loc.setObjectName("btn_local")
        ab_loc.clicked.connect(self._add_local_service); bh.addWidget(ab_loc)
        bh.addStretch(); v.addWidget(br)
        self.ai_svc_tree=QTreeWidget(); self.ai_svc_tree.setColumnCount(5)
        self.ai_svc_tree.setHeaderLabels(["NAME","TYPE","URL","ROLE","MODEL"])
        for i,w2 in enumerate([100,70,220,120,100]): self.ai_svc_tree.setColumnWidth(i,w2)
        self.ai_svc_tree.setRootIsDecorated(False)
        self.ai_svc_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ai_svc_tree.customContextMenuRequested.connect(self._ai_svc_ctx); v.addWidget(self.ai_svc_tree)
        self._reload_ai_svc_tree(); return w

    # ── SETTINGS タブ ──────────────────────────────────────────────────
    def _build_settings_tab(self) -> QWidget:
        from PyQt6.QtWidgets import QSizePolicy, QSpinBox
        # タブ外枠：スクロールエリアで全体を包む
        outer=QWidget(); ov=QVBoxLayout(outer); ov.setContentsMargins(0,0,0,0); ov.setSpacing(0)
        sa=QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        w=QWidget(); sv=QVBoxLayout(w); sv.setContentsMargins(14,14,14,14); sv.setSpacing(10)

        # ── DB情報 ────────────────────────────────────────────────────
        db_g=QGroupBox("DATABASE  ─  データベース管理"); dbl=QVBoxLayout(db_g); dbl.setSpacing(8)

        db_path_lbl=QLabel(f"📂  DB パス:  {self.db.db_path}")
        db_path_lbl.setStyleSheet("color:#777777; font-size:11px; font-family:monospace;"); dbl.addWidget(db_path_lbl)

        # DB初期化ボタン群
        def _danger_row(label, tooltip, fn):
            row=QWidget(); rh=QHBoxLayout(row); rh.setContentsMargins(0,0,0,0); rh.setSpacing(8)
            btn=QPushButton(label); btn.setObjectName("btn_danger"); btn.setFixedWidth(220)
            btn.setToolTip(tooltip); btn.clicked.connect(fn)
            desc=QLabel(tooltip); desc.setStyleSheet("color:#555555; font-size:11px;"); desc.setWordWrap(True)
            rh.addWidget(btn); rh.addWidget(desc,1); return row

        dbl.addWidget(_danger_row(
            "🗑  セッションメッセージを削除",
            "全メッセージ・質問データを削除。AI設定は保持。",
            self._reset_messages_only
        ))
        dbl.addWidget(_danger_row(
            "💣  DBを完全初期化",
            "messages / sessions / settings を全削除。AI設定も初期化。",
            self._reset_db_full
        ))
        sv.addWidget(db_g)

        # ── クリップボード設定 ────────────────────────────────────────
        cb_g=QGroupBox("CLIPBOARD  ─  取り込み設定"); cbl=QVBoxLayout(cb_g); cbl.setSpacing(8)

        # モード説明（常時監視UIは非表示・手動固定）
        mode_desc=QLabel(
            "🛡  AIのCopyボタンを押した後、VIEWERの「📥 取り込み」ボタンで保存します。\n"
            "    クリップボードの自動監視は行いません（ローカル保存のみ・外部送信なし）。"
        )
        mode_desc.setStyleSheet("color:#4ade80; font-size:11px;"); mode_desc.setWordWrap(True)
        cbl.addWidget(mode_desc)

        # ポーリング間隔（内部処理用・UIラベルは「応答チェック間隔」）
        poll_row=QWidget(); poll_h=QHBoxLayout(poll_row); poll_h.setContentsMargins(0,0,0,0); poll_h.setSpacing(8)
        poll_lbl=QLabel("応答チェック間隔 (秒):"); poll_lbl.setStyleSheet("color:#aaaaaa; font-size:12px;")
        self._poll_spin=QSpinBox(); self._poll_spin.setRange(1,10); self._poll_spin.setValue(int(self.monitor.poll))
        self._poll_spin.setFixedWidth(70)
        self._poll_spin.setStyleSheet("background:#252525; border:1px solid #383838; border-radius:4px; color:#cccccc; padding:3px 6px;")
        poll_apply=QPushButton("適用"); poll_apply.setFixedWidth(60)
        poll_apply.clicked.connect(lambda: (
            setattr(self.monitor,'poll',self._poll_spin.value()),
            self._log(f"⚙️  チェック間隔を {self._poll_spin.value()}秒 に変更")
        ))
        poll_h.addWidget(poll_lbl); poll_h.addWidget(self._poll_spin); poll_h.addWidget(poll_apply); poll_h.addStretch()
        cbl.addWidget(poll_row)
        sv.addWidget(cb_g)

        # ── シグネチャ設定 ────────────────────────────────────────────
        sig_g=QGroupBox("SIGNATURE  ─  突合設定"); sigl=QVBoxLayout(sig_g); sigl.setSpacing(8)
        sig_row=QWidget(); sig_h=QHBoxLayout(sig_row); sig_h.setContentsMargins(0,0,0,0); sig_h.setSpacing(8)
        sig_lbl=QLabel("Q プレフィックス文字数:"); sig_lbl.setStyleSheet("color:#aaaaaa; font-size:12px;")
        self._sig_spin=QSpinBox(); self._sig_spin.setRange(10,200); self._sig_spin.setValue(SIG_Q_LEN)
        self._sig_spin.setFixedWidth(70)
        self._sig_spin.setStyleSheet("background:#252525; border:1px solid #383838; border-radius:4px; color:#cccccc; padding:3px 6px;")
        sig_apply=QPushButton("適用"); sig_apply.setFixedWidth(60)
        def _apply_sig_len():
            global SIG_Q_LEN; SIG_Q_LEN=self._sig_spin.value()
            self._log(f"⚙️  シグネチャQ長を {SIG_Q_LEN}文字 に変更")
        sig_apply.clicked.connect(_apply_sig_len)
        sig_h.addWidget(sig_lbl); sig_h.addWidget(self._sig_spin); sig_h.addWidget(sig_apply); sig_h.addStretch()
        sigl.addWidget(sig_row)
        desc=QLabel("プロンプト末尾に付加するシグネチャ [Q:xxxx] の文字数。短いほど突合しやすいが衝突リスクあり。")
        desc.setStyleSheet("color:#555555; font-size:11px;"); desc.setWordWrap(True); sigl.addWidget(desc)
        sv.addWidget(sig_g)

        # ── Unknown自動削除 ───────────────────────────────────────────
        unk_g=QGroupBox("UNKNOWN  ─  終了時処理"); unkl=QVBoxLayout(unk_g); unkl.setSpacing(6)
        self._auto_del_unknown=QCheckBox("終了時に Unknown（ラベルなし）を自動削除する")
        self._auto_del_unknown.setChecked(False)
        self._auto_del_unknown.setStyleSheet("color:#aaaaaa; font-size:12px;")
        unkl.addWidget(self._auto_del_unknown)
        sv.addWidget(unk_g)

        # ── GRID LAUNCH 設定（Chrome パスのみ）───────────────────────
        grid_cfg_g=QGroupBox("GRID LAUNCH  ─  Chrome設定"); grid_cfgl=QVBoxLayout(grid_cfg_g); grid_cfgl.setSpacing(8)

        # Chrome パス
        chrome_row=QWidget(); chrome_h=QHBoxLayout(chrome_row); chrome_h.setContentsMargins(0,0,0,0); chrome_h.setSpacing(6)
        chrome_lbl=QLabel("Chrome パス:"); chrome_lbl.setStyleSheet("color:#aaaaaa; font-size:12px;"); chrome_lbl.setFixedWidth(90)
        self._chrome_path_edit=QLineEdit()
        self._chrome_path_edit.setPlaceholderText("空欄 = 自動検出")
        auto_detected=GridLauncher()._find_chrome()
        if auto_detected:
            self._chrome_path_edit.setPlaceholderText(f"自動検出: {auto_detected}")
        self._chrome_path_edit.setStyleSheet("font-size:11px; font-family:monospace;")
        chrome_browse=QPushButton("参照…"); chrome_browse.setFixedWidth(60)
        def _browse_chrome():
            from PyQt6.QtWidgets import QFileDialog
            p,_=QFileDialog.getOpenFileName(self,"chrome.exeを選択","","実行ファイル (*.exe);;全ファイル (*.*)")
            if p: self._chrome_path_edit.setText(p)
        chrome_browse.clicked.connect(_browse_chrome)
        chrome_h.addWidget(chrome_lbl); chrome_h.addWidget(self._chrome_path_edit,1); chrome_h.addWidget(chrome_browse)
        grid_cfgl.addWidget(chrome_row)

        # プロファイル保存先（表示のみ）
        prof_lbl=QLabel(f"プロファイル保存先:  {GridLauncher.get_profile_dir()}")
        prof_lbl.setStyleSheet("color:#555555; font-size:11px; font-family:monospace;"); prof_lbl.setWordWrap(True)
        grid_cfgl.addWidget(prof_lbl)

        # 注記
        note=QLabel(
            "ℹ️  Chat Rotatorはブラウザを自動操作しません。\n"
            "　  プロンプトのコピー・貼り付けはユーザーが行います。\n"
            "　  各AIサービスの利用規約に準拠した設計です。"
        )
        note.setStyleSheet("color:#3a3a3a; font-size:10px; padding:4px 0;"); note.setWordWrap(True)
        grid_cfgl.addWidget(note)
        sv.addWidget(grid_cfg_g)

        # グリッド状態の定期更新
        self._grid_timer=QTimer(); self._grid_timer.timeout.connect(self._refresh_grid_status); self._grid_timer.start(3000)

        sv.addStretch()

        # バージョン情報
        ver_lbl=QLabel("RogoAI Chat Rotator  v3.7  —  © 2026 RogoAI")
        ver_lbl.setStyleSheet("color:#333333; font-size:10px;"); ver_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        sv.addWidget(ver_lbl)

        sa.setWidget(w); ov.addWidget(sa)
        return outer

    def _build_log_bar(self) -> QWidget:
        w=QWidget(); w.setFixedHeight(44); w.setStyleSheet("background:#111111; border-top:1px solid #1e1e1e;")
        h=QHBoxLayout(w); h.setContentsMargins(8,4,8,4)
        lbl=QLabel("LOG"); lbl.setStyleSheet("color:#333333; font-size:10px; letter-spacing:0.1em; margin-right:6px;"); h.addWidget(lbl)
        self.log_view=QPlainTextEdit(); self.log_view.setObjectName("log_area"); self.log_view.setReadOnly(True); self.log_view.setMaximumBlockCount(200); h.addWidget(self.log_view)
        return w

    # ══════════════════════════════════════════════════════════════════
    # AIカード構築（C案）
    # ══════════════════════════════════════════════════════════════════
    def _load_ai_cards(self):
        """
        各AIカード:
        ┌──────────────────────────────────────────────────┐
        │ ☑  🌐 Claude  [ロール入力          ]  [📋]      │
        │    ▶ 詳細設定（このAIだけオーバーライド）        │
        │  ┌────────────────────────────────────────────┐  │  ← 展開時
        │  │ FW:[─共通─] VP:[技術] Fmt:[─共通─]        │  │
        │  │ ONE-SHOT: [このAI専用例示テキスト…]        │  │
        │  └────────────────────────────────────────────┘  │
        └──────────────────────────────────────────────────┘
        FW/VP/Fmt が「─共通─」のときはグローバル設定を使用。
        """
        while self._ai_card_container.count()>1:
            item=self._ai_card_container.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._ai_cards.clear()

        services=self.db.get_ai_services(); ins=0

        for name,cfg in services.items():
            color=cfg.get("color","#c0c0c0"); is_local=cfg.get("type")=="local"
            icon="⚡" if is_local else "🌐"

            outer=QWidget(); outer.setObjectName("ai_card")
            ov=QVBoxLayout(outer); ov.setContentsMargins(7,6,7,6); ov.setSpacing(3)

            # 行1: チェック・名前・ロール・ボタン
            row1=QWidget(); r1=QHBoxLayout(row1); r1.setContentsMargins(0,0,0,0); r1.setSpacing(5)
            chk=QCheckBox(); chk.setChecked(cfg.get("enabled",True)); r1.addWidget(chk)
            # チェック変化時：Browser AIが4超えたら警告
            if not is_local:
                chk.toggled.connect(self._check_grid_limit)
            nlbl=QLabel(f"{icon} {name}"); nlbl.setStyleSheet(f"color:{color}; font-size:12px; font-weight:700; min-width:76px;"); r1.addWidget(nlbl)
            role_e=QLineEdit(cfg.get("role","")); role_e.setPlaceholderText("role…")
            role_e.setStyleSheet("border:none; border-bottom:1px solid #383838; background:transparent; color:#bbbbbb; font-size:12px; padding:1px 3px;"); r1.addWidget(role_e,1)

            if is_local:
                act_btn=QPushButton("⚡"); act_btn.setObjectName("card_send")
                act_btn.setToolTip(f"{name}（LocalLLM）に送信"); act_btn.setFixedWidth(36)
                act_btn.clicked.connect(lambda c,n=name: self._send_single_local(n))
            else:
                act_btn=QPushButton("📋"); act_btn.setObjectName("card_copy")
                act_btn.setToolTip(f"{name} 用プロンプトをコピー → ブラウザに Ctrl+V"); act_btn.setFixedWidth(36)
                act_btn.clicked.connect(lambda c,n=name: self._copy_for_ai(n))
            r1.addWidget(act_btn); ov.addWidget(row1)

            # 詳細設定トグル
            toggle=QPushButton("▶  詳細設定（このAIだけオーバーライド）"); toggle.setCheckable(True)
            toggle.setStyleSheet("border:none; background:transparent; color:#555555; font-size:10px; text-align:left; padding:1px 2px; min-height:16px;"); ov.addWidget(toggle)

            # 詳細パネル（折りたたみ）
            detail=QWidget(); detail.setObjectName("detail_panel"); detail.setVisible(False)
            dv=QVBoxLayout(detail); dv.setContentsMargins(5,5,5,5); dv.setSpacing(4)

            def _cb(opts):
                cb=QComboBox(); cb.addItem(self.NONE_LABEL)
                for k,lbl in opts:
                    if k!="none": cb.addItem(lbl,k)
                return cb

            sr2=QWidget(); sr2l=QHBoxLayout(sr2); sr2l.setContentsMargins(0,0,0,0); sr2l.setSpacing(4)
            for lbl2,style in [("FW:",""),("VP:",""),("Fmt:","")]:
                l=QLabel(lbl2); l.setStyleSheet("color:#777777; font-size:11px;"); sr2l.addWidget(l)
            fw_cb =_cb(FRAMEWORKS);     sr2l.addWidget(fw_cb,1)
            vp_cb =_cb(VIEWPOINTS);     sr2l.addWidget(vp_cb,1)
            fmt_cb=_cb(OUTPUT_FORMATS); sr2l.addWidget(fmt_cb,1)

            # ラベルとコンボを正しく並べる
            sel_w=QWidget(); sl=QHBoxLayout(sel_w); sl.setContentsMargins(0,0,0,0); sl.setSpacing(4)
            fw_l=QLabel("FW:");fw_l.setStyleSheet("color:#777777; font-size:11px;"); sl.addWidget(fw_l); sl.addWidget(fw_cb,1)
            vp_l=QLabel("VP:");vp_l.setStyleSheet("color:#777777; font-size:11px;"); sl.addWidget(vp_l); sl.addWidget(vp_cb,1)
            ft_l=QLabel("Fmt:");ft_l.setStyleSheet("color:#777777; font-size:11px;"); sl.addWidget(ft_l); sl.addWidget(fmt_cb,1)
            dv.addWidget(sel_w)

            os_l=QLabel("ONE-SHOT（FW/VP/FMT選択で自動反映 · 手動編集可 · 空=グローバル）:")
            os_l.setStyleSheet("color:#666666; font-size:10px;"); dv.addWidget(os_l)
            os_e=QPlainTextEdit(); os_e.setPlaceholderText("このAI専用の指示（FW/VP/FMT選択で自動入力。さらに追記可）")
            os_e.setMaximumHeight(52); os_e.setStyleSheet("font-size:11px;"); dv.addWidget(os_e)

            # FW/VP/FMT変化時にこのAIのone-shot欄へ自動反映
            def _make_updater(fw_c, vp_c, fmt_c, os_edit):
                def _update(_=None):
                    parts=[]
                    fw  = fw_c.currentData()
                    vp  = vp_c.currentData()
                    fmt = fmt_c.currentData()
                    if fw  and fw  not in ("none", None) and fw  in FRAMEWORK_PROMPTS: parts.append(FRAMEWORK_PROMPTS[fw])
                    if vp  and vp  not in ("none","custom",None) and vp in VIEWPOINT_PROMPTS: parts.append(VIEWPOINT_PROMPTS[vp])
                    if fmt and fmt not in ("none", None) and fmt in FORMAT_PROMPTS: parts.append(FORMAT_PROMPTS[fmt])
                    os_edit.setPlainText("\n".join(parts))
                return _update
            _upd = _make_updater(fw_cb, vp_cb, fmt_cb, os_e)
            fw_cb.currentIndexChanged.connect(_upd)
            vp_cb.currentIndexChanged.connect(_upd)
            fmt_cb.currentIndexChanged.connect(_upd)

            ov.addWidget(detail)

            toggle.toggled.connect(
                lambda chked,p=detail,t=toggle: (
                    p.setVisible(chked),
                    t.setText(("▼  詳細設定（このAIだけオーバーライド）" if chked else "▶  詳細設定（このAIだけオーバーライド）"))
                )
            )
            self._ai_cards[name]={"check":chk,"role":role_e,"fw_cb":fw_cb,"vp_cb":vp_cb,"fmt_cb":fmt_cb,"oneshot":os_e,"cfg":cfg,"act_btn":act_btn}
            self._ai_card_container.insertWidget(ins,outer); ins+=1

    # ══════════════════════════════════════════════════════════════════
    # グローバル選択
    # ══════════════════════════════════════════════════════════════════
    def _sel_fw(self,k):
        for i in range(self._fw_combo.count()):
            if self._fw_combo.itemData(i)==k:
                self._fw_combo.setCurrentIndex(i); break
    def _sel_vp(self,k):
        for i in range(self._vp_combo.count()):
            if self._vp_combo.itemData(i)==k:
                self._vp_combo.setCurrentIndex(i); break
        self.custom_vp_edit.setVisible(k=="custom")
    def _sel_fmt(self,k):
        for i in range(self._fmt_combo.count()):
            if self._fmt_combo.itemData(i)==k:
                self._fmt_combo.setCurrentIndex(i); break
    def _gfw(self):  return self._fw_combo.currentData()  or "none"
    def _gvp(self):  return self._vp_combo.currentData()  or "none"
    def _gfmt(self): return self._fmt_combo.currentData() or "none"

    def _get_ai_override(self, name:str) -> dict:
        """AI別オーバーライド値。─共通─ならNone"""
        card=self._ai_cards.get(name,{})
        def _val(cb):
            if not cb: return None
            txt=cb.currentText(); return None if txt==self.NONE_LABEL else cb.currentData()
        return {
            "role":    card.get("role",None) and card["role"].text().strip(),
            "fw":      _val(card.get("fw_cb")),
            "vp":      _val(card.get("vp_cb")),
            "fmt":     _val(card.get("fmt_cb")),
            "oneshot": card["oneshot"].toPlainText().strip() if card.get("oneshot") else "",
        }

    # ══════════════════════════════════════════════════════════════════
    # プロンプト構築（AI別オーバーライド + シグネチャ付加）
    # ══════════════════════════════════════════════════════════════════
    def _build_prompt(self, ai_name:str, ts:str, include_attachments:bool=True, add_signature:bool=True) -> str:
        ov  =self._get_ai_override(ai_name)
        base=self.prompt_edit.toPlainText().strip()
        fw  =ov["fw"]   if ov["fw"]  is not None else self._gfw()
        vp  =ov["vp"]   if ov["vp"]  is not None else self._gvp()
        fmt =ov["fmt"]  if ov["fmt"] is not None else self._gfmt()
        role=ov["role"] or self._ai_cards.get(ai_name,{}).get("cfg",{}).get("role","")
        oneshot=ov["oneshot"] or self.oneshot_edit.toPlainText().strip()

        prompt=PromptBuilder.build(base_prompt=base,role=role,
            framework=fw,viewpoint=vp,output_fmt=fmt,
            oneshot=oneshot,custom_viewpoint=self._custom_vp)

        if include_attachments and self._attached_files:
            prompt+=FileAttachment.build_prompt_block(self._attached_files)

        if add_signature:
            return PromptBuilder.add_signature(prompt,ai_name,base,ts)
        return prompt

    # ══════════════════════════════════════════════════════════════════
    # 個別 COPY（ブラウザAI）
    # ══════════════════════════════════════════════════════════════════
    def _copy_for_ai(self, ai_name:str):
        base=self.prompt_edit.toPlainText().strip()
        if not base: self._log("⚠️  プロンプトを入力してください"); return
        card=self._ai_cards.get(ai_name)
        if not card or not card["check"].isChecked(): self._log(f"⚠️  {ai_name} は無効です"); return

        # 現在チェック済みAIセットを確認 → 変化があればTSリセット
        current_checked = frozenset(
            n for n,c in self._ai_cards.items() if c["check"].isChecked()
        )
        if not hasattr(self,'_last_checked_set'):
            self._last_checked_set = current_checked
        if current_checked != self._last_checked_set:
            self._current_ts = None
            self._last_checked_set = current_checked
            self._log("🔄  AI選択変更 → 質問TSをリセット")

        # 同一プロンプト・同一AI構成の最初の送信時だけ question を保存
        if self._current_ts is None:
            self._current_ts=datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            self.db.save_question(
                self.monitor.session_id,self._current_ts,base,
                self._gfw(),self._gvp(),self._gfmt()
            )
            self._log(f"💾  質問保存  ts={self._current_ts}")

        prompt=self._build_prompt(ai_name,self._current_ts)
        _set_cb(prompt)
        ov=self._get_ai_override(ai_name)
        self._log(f"📋  {ai_name} 用プロンプトをコピー  [AI:{ai_name}][TS:{self._current_ts}]")
        self._log(f"     FW:{ov['fw'] or self._gfw()}  VP:{ov['vp'] or self._gvp()}  Fmt:{ov['fmt'] or self._gfmt()}")
        # 画像が添付されていたらリマインド
        imgs=[af for af in self._attached_files if af.ftype=="image" and not af.error]
        if imgs:
            names=", ".join(af.name for af in imgs[:3])
            self._log(f"🖼  画像 [{names}] → {ai_name}のサイトでアップロードボタンから手動追加してください")
        self._last_copied_ai = ai_name   # 手動取り込み時のフォールバック用
        # グリッド起動中ならそのウィンドウにフォーカス
        if self._grid_launcher and self._grid_launcher.is_alive():
            focused = self._grid_launcher.focus_window(ai_name)
            if focused: self._log(f"🪟  {ai_name} ウィンドウにフォーカス移動")

    def _reset_question_state(self):
        self._current_ts=None
        if hasattr(self,'_last_checked_set'):
            del self._last_checked_set


    # ══════════════════════════════════════════════════════════════════
    # LocalLLM 送信
    # ══════════════════════════════════════════════════════════════════
    def _send_single_local(self, ai_name:str):
        base=self.prompt_edit.toPlainText().strip()
        if not base: self._log("⚠️  プロンプトを入力してください"); return
        svcs=self.db.get_ai_services(); cfg=svcs.get(ai_name)
        if not cfg or cfg.get("type")!="local": self._log(f"⚠️  {ai_name} はLocalLLMではありません"); return
        self._do_local_send([(ai_name,cfg)])

    def _send_to_local(self):
        base=self.prompt_edit.toPlainText().strip()
        if not base: self._log("⚠️  プロンプトを入力してください"); return
        svcs=self.db.get_ai_services()
        targets=[(n,c) for n,c in svcs.items()
                 if c.get("type")=="local" and c.get("enabled")
                 and self._ai_cards.get(n,{}).get("check") and self._ai_cards[n]["check"].isChecked()]
        if not targets: self._log("⚠️  有効なLocalLLMがありません"); return
        self._do_local_send(targets)


    def _do_local_send(self, targets:list):
        base=self.prompt_edit.toPlainText().strip()
        if self._current_ts is None:
            self._current_ts=datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            self.db.save_question(
                self.monitor.session_id,self._current_ts,base,
                self._gfw(),self._gvp(),self._gfmt()
            )
        ts=self._current_ts; files=list(self._attached_files)

        # プロンプトをメインスレッドで事前構築（UIアクセスはメインスレッドのみ安全）
        prompts={ai_name: self._build_prompt(ai_name,ts,include_attachments=False,add_signature=False)
                 for ai_name,cfg in targets}

        # 対象カードの⚡ボタンをSending...に
        for ai_name,_ in targets:
            btn=self._ai_cards.get(ai_name,{}).get("act_btn")
            if btn: btn.setEnabled(False); btn.setText("…")
        self.local_all_btn.setEnabled(False); self.local_all_btn.setText("Sending…")

        def _worker():
            print("[DEBUG] _worker start", flush=True)
            for ai_name,cfg in targets:
                print(f"[DEBUG] target: {ai_name}, url={cfg.get('url')}, endpoint={cfg.get('endpoint')}, model={cfg.get('model')}", flush=True)
                prompt=prompts[ai_name]
                print(f"[DEBUG] prompt length={len(prompt)}", flush=True)
                msgs=FileAttachment.build_local_messages(prompt,files)
                img_count=len([f for f in files if f.ftype=="image"])
                print(f"[DEBUG] msgs count={len(msgs)}, images={img_count}", flush=True)
                print(f"[DEBUG] msg content={msgs[0].get('content','')[:200]!r}", flush=True)
                print(f"[DEBUG] msg has images key={'images' in msgs[0]}, images_b64_len={len(msgs[0].get('images',[''])[0]) if msgs[0].get('images') else 0}", flush=True)
                print(f"[DEBUG] calling LocalLLMClient.chat...", flush=True)
                self.sig.log_message.emit(f"⚡  {ai_name} 送信中… model={cfg.get('model','')} （応答待ち、数分かかる場合があります）")
                resp=LocalLLMClient.chat(cfg.get("url",""),cfg.get("endpoint","/v1/chat/completions"),cfg.get("model",""),msgs,timeout=300)
                print(f"[DEBUG] resp received, length={len(resp)}, preview={resp[:80]}", flush=True)
                meta={"source":"local_api","model":cfg.get("model","")}
                self.db.save_message(self.monitor.session_id,"assistant",ai_name,resp,meta,ts)
                self.sig.local_result.emit(ai_name,resp[:80])
                self.sig.log_message.emit(f"✓  {ai_name} 応答: {len(resp)}文字")
            print("[DEBUG] _worker done", flush=True)
            self.sig.reset_local_btns.emit([ai_name for ai_name,_ in targets])
        threading.Thread(target=_worker,daemon=True).start()

    # ══════════════════════════════════════════════════════════════════
    # プロンプトプレビュー（AI別タブ）
    # ══════════════════════════════════════════════════════════════════
    def _show_preview(self):
        svcs=self.db.get_ai_services()
        enabled=[(n,c) for n,c in svcs.items()
                 if self._ai_cards.get(n,{}).get("check") and self._ai_cards[n]["check"].isChecked()]
        ts=datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        dlg=QDialog(self); dlg.setWindowTitle("Prompt Preview  ─  AI別"); dlg.setMinimumSize(700,520)
        dlg.setStyleSheet(STYLE); dv=QVBoxLayout(dlg)
        tabs=QTabWidget()
        for ai_name,_ in enabled[:8]:
            p=self._build_prompt(ai_name,ts)
            te=QTextEdit(); te.setPlainText(p); te.setReadOnly(True); tabs.addTab(te,ai_name)
        dv.addWidget(tabs)
        if enabled:
            first=enabled[0][0]
            cp=QPushButton(f"📋 {first} をコピー"); cp.setObjectName("btn_copy")
            cp.clicked.connect(lambda: _set_cb(self._build_prompt(first,ts))); dv.addWidget(cp)
        dlg.exec()

    # ══════════════════════════════════════════════════════════════════
    # Viewer
    # ══════════════════════════════════════════════════════════════════
    def _refresh_viewer(self):
        if not self.monitor.session_id: return
        q=self.search_edit.text().strip() if hasattr(self,"search_edit") else ""
        if q:
            msgs=self.db.search_messages(q)
        else:
            msgs=self.db.get_all_messages()
        # データ変化なし＆複数選択中 → タイマー由来の更新をスキップ
        if msgs==self._page_all_msgs:
            if len(self.tree.selectedItems())>1:
                print("[DEBUG] _refresh_viewer: skipped (multi-select active)", flush=True)
                return
            return  # データ変化なければ常にスキップ
        self._page_all_msgs=msgs
        self._page_current=0
        self._render_page()

    def _force_refresh_viewer(self):
        """選択状態を無視して強制再描画（削除・ラベル変更後など）"""
        if not self.monitor.session_id: return
        q=self.search_edit.text().strip() if hasattr(self,"search_edit") else ""
        msgs=self.db.search_messages(q) if q else self.db.get_all_messages()
        self._page_all_msgs=msgs
        self._render_page()

    def _render_page(self):
        msgs=self._page_all_msgs
        total=len(msgs)
        page_size=self._page_size
        total_pages=max(1,(total+page_size-1)//page_size)
        self._page_current=max(0,min(self._page_current,total_pages-1))
        start=self._page_current*page_size
        end=min(start+page_size,total)
        page_msgs=msgs[start:end]

        # ページラベル更新
        self._page_label.setText(f"{self._page_current+1} / {total_pages}  （全{total}件）")
        self._page_prev.setEnabled(self._page_current>0)
        self._page_next.setEnabled(self._page_current<total_pages-1)

        self.tree.clear(); svcs=self.db.get_ai_services()
        for m in page_msgs:
            meta=json.loads(m.get("metadata","{}") or "{}"); label=meta.get("label","")
            source=meta.get("source","cb")[:3]; svc=m["service"]
            t=datetime.fromisoformat(m["detected_at"]).strftime("%m/%d %H:%M:%S")
            is_question=(label=="question")
            content_prev=m["content"].replace("\n"," ")[:48]
            # question行はQUESTION列に内容を表示、ANSWER列は空
            q_col = content_prev if is_question else ""
            a_col = "" if is_question else content_prev
            disp_label = "question" if is_question else label
            item=QTreeWidgetItem([t,svc,disp_label,q_col,a_col,source])
            item.setData(0,Qt.ItemDataRole.UserRole,  m["id"])
            item.setData(0,Qt.ItemDataRole.UserRole+1,m["content"])
            item.setData(0,Qt.ItemDataRole.UserRole+2,svc)
            item.setData(0,Qt.ItemDataRole.UserRole+3,m["content"] if is_question else "")
            item.setData(0,Qt.ItemDataRole.UserRole+4,m.get("ts",""))
            color="#909090"
            if svc in svcs: color=svcs[svc].get("color","#c0c0c0")
            elif svc=="User": color="#94a3b8"
            if is_question:
                # question行：やや暗めのブルー系で識別
                for c in range(6): item.setForeground(c,QColor("#7ab0c8"))
                item.setForeground(1,QColor("#94a3b8"))
            elif svc=="Unknown" and not label:
                for c in range(6): item.setForeground(c,QColor("#666666"))
            else:
                item.setForeground(0,QColor("#909090"))
                item.setForeground(5,QColor("#707070"))
                item.setForeground(1,QColor(color))
                item.setForeground(3,QColor("#c8c8c8"))
                item.setForeground(4,QColor("#b8b8b8"))
                if label and not is_question: item.setForeground(2,QColor("#fb923c"))
                else: item.setForeground(2,QColor("#707070"))
            self.tree.addTopLevelItem(item)
        self._update_status()

    def _on_search(self,_):
        self._page_current=0; self._refresh_viewer()

    def _on_selection_changed(self):
        items=self.tree.selectedItems()
        print(f"[DEBUG] selection changed: {len(items)} items selected", flush=True)
        if len(items)==1:
            self._show_content(items[0])
        elif len(items)>1:
            print(f"[DEBUG] multi-select: {[it.text(1) for it in items]}", flush=True)
            # 複数選択時は右パネルに件数を表示するのみ
            self._content_badge.setText(f"{len(items)}件 選択中  ·  Summary / Difference ボタンで分析")
            self._content_view.setPlainText("")
        # 0件選択時は何もしない

    def _show_content(self, item:QTreeWidgetItem):
        content=item.data(0,Qt.ItemDataRole.UserRole+1)
        svc    =item.data(0,Qt.ItemDataRole.UserRole+2)
        q_full =item.data(0,Qt.ItemDataRole.UserRole+3)
        ts_val =item.data(0,Qt.ItemDataRole.UserRole+4)
        label  =item.text(2)
        badge  =svc.upper()
        if label: badge+=f"  ·  {label}"
        if ts_val: badge+=f"  ·  ts:{ts_val}"
        self._content_badge.setText(badge); self._content_view.setPlainText(content or "")
        if q_full:
            self._q_badge.setText("QUESTION"); self._q_view.setPlainText(q_full)
        else:
            self._q_badge.setText("QUESTION  ·  (質問行を選択)"); self._q_view.setPlainText("")

    def _tree_ctx(self, pos):
        item=self.tree.itemAt(pos)
        if not item: return
        msg_id=item.data(0,Qt.ItemDataRole.UserRole); svc=item.data(0,Qt.ItemDataRole.UserRole+2)
        menu=QMenu(self)
        def _add(lbl,fn): a=QAction(lbl,self); a.triggered.connect(fn); menu.addAction(a)
        _add("📋  Copy Answer",lambda: _set_cb(item.data(0,Qt.ItemDataRole.UserRole+1)))
        _add("📋  Copy Question",lambda: _set_cb(item.data(0,Qt.ItemDataRole.UserRole+3) or ""))
        # サービス名変更（全メッセージ対象）
        menu.addSeparator()
        _add("✏️  サービス名を変更…", lambda: self._rename_service(msg_id, item))
        svcs=self.db.get_ai_services()
        locals_=[(n,c) for n,c in svcs.items() if c.get("type")=="local" and c.get("enabled")]
        if locals_:
            menu.addSeparator()
            _add("📄  Generate Summary (Local)",
                 lambda: self._local_task(item.data(0,Qt.ItemDataRole.UserRole+1),"summary",msg_id))
        if svc=="Unknown":
            menu.addSeparator()
            lm=menu.addMenu("🏷  Assign Label")
            for key,disp,_ in ChatDatabase.LABELS:
                a=QAction(disp,self); a.triggered.connect(lambda c,k=key,i=msg_id,it=item: self._assign_label(i,k,it)); lm.addAction(a)
            if item.text(2):
                _add("✕  Remove Label",lambda: self._assign_label(msg_id,None,item))
        menu.addSeparator()
        _add("🗑  Delete",lambda: self._delete_item(msg_id))
        menu.exec(QCursor.pos())

    def _assign_label(self,msg_id,label,item):
        self.db.set_label(msg_id,label); item.setText(2,label or "")
        if label: item.setForeground(2,QColor("#fb923c"))
        else:
            for c in range(6): item.setForeground(c,QColor("#3a3a3a"))
        self._update_status()

    def _rename_service(self, msg_id:int, item):
        """サービス名を手動変更するダイアログ"""
        svcs = self.db.get_ai_services()
        ai_names = list(svcs.keys()) + ["Unknown"]
        current_svc = item.data(0, Qt.ItemDataRole.UserRole+2) or "Unknown"

        dlg = QDialog(self); dlg.setWindowTitle("サービス名を変更"); dlg.setFixedWidth(320)
        dlg.setStyleSheet(STYLE); v = QVBoxLayout(dlg)

        lbl = QLabel(f"現在: {current_svc}"); lbl.setStyleSheet("color:#888888; font-size:11px;")
        v.addWidget(lbl)

        cb = QComboBox(); cb.setStyleSheet(
            "background:#252525; border:1px solid #383838; border-radius:4px;"
            "color:#e0e0e0; font-size:13px; padding:4px 8px;"
        )
        for name in ai_names:
            cb.addItem(name)
        # 現在のサービス名を選択状態に
        idx = cb.findText(current_svc)
        if idx >= 0: cb.setCurrentIndex(idx)
        v.addWidget(cb)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.setStyleSheet(STYLE)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_svc = cb.currentText()
            self.db.update_service(msg_id, new_svc)
            item.setText(1, new_svc)  # VIEWERのSERVICE列を即時更新
            # サービス色を更新
            colors = {"Claude":"#da7756","Gemini":"#4a90d9","Grok":"#c084fc",
                      "ChatGPT":"#74c99a","Unknown":"#3a3a3a"}
            color = colors.get(new_svc, "#aaaaaa")
            item.setForeground(1, QColor(color))
            self._log(f"✏️  {current_svc} → {new_svc}（ID:{msg_id}）")

    def _clear_content_view(self):
        """コンテンツ表示エリアをクリア（削除・初期化時に呼ぶ）"""
        self._content_badge.setText("")
        self._content_view.setPlainText("")
        self._q_badge.setText("QUESTION  ·  (質問行を選択)")
        self._q_view.setPlainText("")

    def _delete_item(self,msg_id):
        # msg_idでツリーを検索（itemの参照は使わない）
        for i in range(self.tree.topLevelItemCount()):
            it=self.tree.topLevelItem(i)
            if it and it.data(0,Qt.ItemDataRole.UserRole)==msg_id:
                self.tree.takeTopLevelItem(i)
                break
        self.db.delete_message(msg_id)
        self._page_all_msgs=[m for m in self._page_all_msgs if m.get("id")!=msg_id]
        total=len(self._page_all_msgs)
        total_pages=max(1,(total+self._page_size-1)//self._page_size)
        self._page_current=min(self._page_current,total_pages-1)
        self._page_label.setText(f"{self._page_current+1} / {total_pages}  （全{total}件）")
        self._page_prev.setEnabled(self._page_current>0)
        self._page_next.setEnabled(self._page_current<total_pages-1)
        self._clear_content_view()
        self._update_status()

    def _bulk_delete(self):
        """選択中の全行を一括削除"""
        items=self.tree.selectedItems()
        if not items: self._log("⚠️  削除する行を選択してください"); return
        ans=QMessageBox.question(self,"一括削除",
            f"{len(items)}件を削除しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if ans!=QMessageBox.StandardButton.Yes: return
        # msg_idを先に収集（itemは削除で無効になるので参照しない）
        ids=[it.data(0,Qt.ItemDataRole.UserRole) for it in items]
        for msg_id in ids:
            self.db.delete_message(msg_id)
            for i in range(self.tree.topLevelItemCount()-1,-1,-1):
                it=self.tree.topLevelItem(i)
                if it and it.data(0,Qt.ItemDataRole.UserRole)==msg_id:
                    self.tree.takeTopLevelItem(i); break
        self._page_all_msgs=[m for m in self._page_all_msgs if m.get("id") not in ids]
        total=len(self._page_all_msgs)
        total_pages=max(1,(total+self._page_size-1)//self._page_size)
        self._page_current=min(self._page_current,total_pages-1)
        self._page_label.setText(f"{self._page_current+1} / {total_pages}  （全{total}件）")
        self._page_prev.setEnabled(self._page_current>0)
        self._page_next.setEnabled(self._page_current<total_pages-1)
        self._log(f"🗑  {len(ids)}件 一括削除")
        self._clear_content_view(); self._update_status()

    def _local_task(self,content:str,task:str,source_id:int):
        svcs=self.db.get_ai_services()
        locals_=[(n,c) for n,c in svcs.items() if c.get("type")=="local" and c.get("enabled")]
        if not locals_: return
        ai_name,cfg=locals_[0]
        prompts={"summary":f"以下を簡潔に要約してください。\n\n{content}"}
        prompt=prompts.get(task,content); self._log(f"⚡  {ai_name} で {task} 生成中…")
        def _w():
            resp=LocalLLMClient.chat(cfg.get("url",""),cfg.get("endpoint","/v1/chat/completions"),cfg.get("model",""),[{"role":"user","content":prompt}])
            meta={"source":"local_api","label":task,"model":cfg.get("model",""),"from":source_id}
            self.db.save_message(self.monitor.session_id,"assistant",ai_name,resp,meta)
            self.sig.local_result.emit(ai_name,resp[:80]); self.sig.log_message.emit(f"✓  {task}: {len(resp)}文字")
        threading.Thread(target=_w,daemon=True).start()

    # ══════════════════════════════════════════════════════════════════
    # 複数選択 Analysis（Summary / Difference）
    # ══════════════════════════════════════════════════════════════════
    def _analyze_selected(self, mode: str):
        items=self.tree.selectedItems()
        if len(items)<2:
            QMessageBox.warning(self,"選択不足",
                "2件以上の行を選択してください。\n（Ctrl+クリック または Shift+クリック）"); return

        parts=[]
        for it in items:
            svc=it.data(0,Qt.ItemDataRole.UserRole+2) or it.text(1)
            content=it.data(0,Qt.ItemDataRole.UserRole+1) or ""
            parts.append(f"[{svc}]\n{content}")
        combined="\n\n---\n\n".join(parts)

        if mode=="summary":
            label="SUMMARY"
            prompt=(f"以下{len(items)}件のAI回答を統合して簡潔に要約してください。\n"
                    f"各AIの特徴的な視点も保持しながら整理してください。\n\n{combined}")
        else:
            label="DIFFERENCE"
            prompt=(f"以下{len(items)}件のAI回答を比較分析してください。\n\n"
                    f"## 出力フォーマット\n"
                    f"### ✅ 共通点\n### 🔀 相違点・独自見解\n### 💡 総合評価\n\n{combined}")

        self._show_analysis_popup(label, prompt)

    def _show_analysis_popup(self, title: str, prompt: str):
        dlg=QDialog(self); dlg.setWindowTitle(f"Analysis: {title}"); dlg.setMinimumSize(620,480)
        dlg.setStyleSheet(STYLE); dv=QVBoxLayout(dlg)

        info=QLabel(f"📋 プロンプトを確認・編集して送信先AIを選択し [RUN ANALYSIS] を押してください。")
        info.setStyleSheet("color:#888888; font-size:11px; padding:4px 0;"); dv.addWidget(info)

        box=QTextEdit(); box.setPlainText(prompt); dv.addWidget(box)

        # ── AI選択行 ──────────────────────────────────────────────────
        sel_row=QWidget(); sel_h=QHBoxLayout(sel_row)
        sel_h.setContentsMargins(0,4,0,4); sel_h.setSpacing(8)
        sel_lbl=QLabel("送信先 AI:"); sel_lbl.setStyleSheet("color:#aaaaaa; font-size:12px;")
        sel_h.addWidget(sel_lbl)

        ai_cb=QComboBox(); ai_cb.setMinimumWidth(160)
        ai_cb.setStyleSheet(
            "background:#252525; border:1px solid #383838; border-radius:4px;"
            "color:#e0e0e0; font-size:12px; padding:3px 8px;"
        )

        # 有効なAIをリストアップ（_ai_cardsのチェック状態を優先）、Geminiをデフォルトに
        svcs=self.db.get_ai_services()
        # SENDERのチェックボックスがあればそちらを優先、なければDBのenabled
        ai_names=[]
        for n,c in svcs.items():
            card=self._ai_cards.get(n)
            if card:
                if card["check"].isChecked(): ai_names.append(n)
            elif c.get("enabled"):
                ai_names.append(n)
        if not ai_names:  # 全部OFF時はDB値で全件表示
            ai_names=[n for n,c in svcs.items()]
        default_idx=0
        for i,n in enumerate(ai_names):
            ai_cb.addItem(n)
            if n=="Gemini": default_idx=i
        ai_cb.setCurrentIndex(default_idx)

        sel_h.addWidget(ai_cb); sel_h.addStretch()
        dv.addWidget(sel_row)

        # ── ボタン行 ──────────────────────────────────────────────────
        bb=QDialogButtonBox(); bb.setStyleSheet(STYLE)
        run_btn=bb.addButton("▶  RUN ANALYSIS",QDialogButtonBox.ButtonRole.AcceptRole)
        run_btn.setObjectName("btn_primary")
        bb.addButton("キャンセル",QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(lambda: (
            self._broadcast_analysis(box.toPlainText(), ai_cb.currentText()),
            dlg.accept()
        ))
        bb.rejected.connect(dlg.reject)
        dv.addWidget(bb); dlg.exec()

    def _broadcast_analysis(self, prompt: str, target_ai: str = ""):
        """選択された1つのAIにAnalysisプロンプトを送信"""
        svcs=self.db.get_ai_services()

        # 選択AIを優先、なければフォールバック
        if target_ai and target_ai in svcs:
            ai_name=target_ai
            cfg=svcs[target_ai]
        else:
            # Gemini → 最初のenabled → 諦め
            candidates=[(n,c) for n,c in svcs.items() if c.get("enabled")]
            if not candidates: self._log("⚠️  有効なAIがありません"); return
            gemini=[x for x in candidates if x[0]=="Gemini"]
            ai_name,cfg=gemini[0] if gemini else candidates[0]

        if cfg.get("type")=="local":
            source_id=(self.db.get_messages(self.monitor.session_id) or [{}])[0].get("id",0)
            self._local_task_direct(ai_name,cfg,prompt,source_id)
            self._log(f"⚡  {ai_name} にAnalysisを送信")
        else:
            ts=datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            signed=PromptBuilder.add_signature(prompt,ai_name,"ANALYSIS",ts)
            _set_cb(signed)
            self._log(f"📋  {ai_name} 用Analysisプロンプトをコピーしました → ブラウザに貼り付けてください")

    def _local_task_direct(self, ai_name:str, cfg:dict, prompt:str, source_id:int):
        self._log(f"⚡  {ai_name} で分析中…")
        def _w():
            resp=LocalLLMClient.chat(cfg.get("url",""),cfg.get("endpoint","/v1/chat/completions"),
                                     cfg.get("model",""),[{"role":"user","content":prompt}])
            meta={"source":"local_api","label":"analysis","model":cfg.get("model",""),"from":source_id}
            self.db.save_message(self.monitor.session_id,"assistant",ai_name,resp,meta)
            self.sig.local_result.emit(ai_name,resp[:80])
            self.sig.log_message.emit(f"✓  {ai_name} 分析完了: {len(resp)}文字")
        threading.Thread(target=_w,daemon=True).start()

    def _set_handover_prompt(self):
        """Follow-up 引き継ぎ仕様書：選択行から自動生成 + 引き継ぎ先AIセレクト"""
        sel=self.tree.selectedItems()
        if not sel:
            QMessageBox.warning(self,"選択なし",
                "VIEWERで引き継ぎ元の行を選択してから\nFollow-upボタンを押してください。\n（Ctrl/Shift+クリックで複数選択可）")
            return

        # 選択行から会話内容を収集
        parts=[]
        for it in sel:
            svc=it.data(0,Qt.ItemDataRole.UserRole+2) or it.text(1)
            content=it.data(0,Qt.ItemDataRole.UserRole+1) or ""
            label=it.text(2)
            parts.append(f"[{svc}]{f'({label})' if label else ''}\n{content}")
        combined="\n\n---\n\n".join(parts)

        dlg=QDialog(self); dlg.setWindowTitle("🔗  Follow-up 引き継ぎ仕様書"); dlg.setMinimumSize(620,420)
        dlg.setStyleSheet(STYLE); lay=QVBoxLayout(dlg); lay.setSpacing(8)

        # ── 引き継ぎ先AIセレクト ──────────────────────────────────────
        sel_row=QWidget(); sh=QHBoxLayout(sel_row); sh.setContentsMargins(0,0,0,0); sh.setSpacing(8)
        sh.addWidget(QLabel("引き継ぎ先AI："))
        ai_cb=QComboBox()
        svcs=self.db.get_ai_services()
        browser_ais=[n for n,c in svcs.items() if c.get("type")=="browser" and c.get("enabled",True)]
        ai_cb.addItems(browser_ais if browser_ais else list(svcs.keys()))
        ai_cb.setMinimumWidth(160)
        sh.addWidget(ai_cb); sh.addStretch(); lay.addWidget(sel_row)

        # ── 選択内容サマリ ────────────────────────────────────────────
        info=QLabel(f"選択行: {len(sel)}件  →  引き継ぎ先AIのSENDER欄にプロンプトをセットします。\n"
                    f"該当AIのウィンドウを開いて手動で貼り付けてください（規約準拠）。")
        info.setStyleSheet("color:#888; font-size:11px; padding:4px 0;")
        info.setWordWrap(True); lay.addWidget(info)

        # ── 生成されたプロンプトプレビュー ───────────────────────────
        lay.addWidget(QLabel("生成されるプロンプト（編集可）："))
        preview=QTextEdit(); preview.setStyleSheet(
            "background:#141414; border:1px solid #333; border-radius:3px; color:#ccc; font-size:12px;")

        def _build_prompt(ai_name):
            return (
                f"以下は {ai_name} への引き継ぎ仕様書です。\n"
                f"これまでの会話・回答の要点を踏まえ、続きのタスクをお願いします。\n\n"
                f"## 引き継ぎ内容（選択された会話 {len(sel)}件）\n\n"
                f"{combined}\n\n"
                f"## 引き継ぎ先AIへの指示\n"
                f"上記の内容を理解した上で、続きのタスクを実装・回答してください。\n"
                f"不明点があれば質問してください。"
            )

        preview.setPlainText(_build_prompt(ai_cb.currentText()))
        ai_cb.currentTextChanged.connect(lambda t: preview.setPlainText(_build_prompt(t)))
        lay.addWidget(preview)

        # ── ボタン行 ─────────────────────────────────────────────────
        btn_row=QWidget(); bh=QHBoxLayout(btn_row); bh.setContentsMargins(0,4,0,0); bh.setSpacing(8)
        set_btn=QPushButton("✅  SENDERにセット"); set_btn.setObjectName("btn_primary"); set_btn.setFixedHeight(28)
        cancel_btn=QPushButton("キャンセル"); cancel_btn.setFixedHeight(28)
        cancel_btn.setStyleSheet("background:#222; border:1px solid #444; border-radius:3px; color:#888; padding:0 10px;")
        bh.addStretch(); bh.addWidget(set_btn); bh.addWidget(cancel_btn)
        lay.addWidget(btn_row)

        def _set():
            text=preview.toPlainText()
            target=ai_cb.currentText()
            self.prompt_edit.setPlainText(text)
            self.tabs.setCurrentIndex(0)
            self._log(f"🔗  Follow-up仕様書をセット → {target} に手動貼り付けてください")
            dlg.accept()

        set_btn.clicked.connect(_set)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    # ══════════════════════════════════════════════════════════════════
    # ファイル添付
    # ══════════════════════════════════════════════════════════════════
    def _att_add_files(self):
        from PyQt6.QtWidgets import QFileDialog
        exts=("対応ファイル (*.txt *.md *.csv *.py *.js *.ts *.html *.css *.json *.yaml *.yml *.xml *.sql *.sh *.java *.c *.cpp *.rs *.go *.jpg *.jpeg *.png *.gif *.webp *.bmp *.pdf *.log *.toml *.ini);;全ファイル (*.*)")
        paths,_=QFileDialog.getOpenFileNames(self,"ファイルを選択","",exts)
        for p in paths: self._att_load(p)
        self._att_info_update()

    def _att_add_folder(self):
        from PyQt6.QtWidgets import QFileDialog
        folder=QFileDialog.getExistingDirectory(self,"フォルダを選択","",QFileDialog.Option.ShowDirsOnly)
        if not folder: return
        supp=FileAttachment.TEXT_EXT|FileAttachment.IMAGE_EXT|FileAttachment.PDF_EXT
        SKIP={'.git','node_modules','__pycache__','venv','.venv','.idea','.vscode'}
        cnt=0
        for root,dirs,files in os.walk(folder):
            dirs[:]=[d for d in dirs if d not in SKIP and not d.startswith('.')]
            for fname in sorted(files):
                if Path(fname).suffix.lower() in supp:
                    self._att_load(os.path.join(root,fname)); cnt+=1
                    if cnt>=30: self._log("⚠️  最大30件まで"); self._att_info_update(); return
        self._att_info_update()

    def _att_load(self,path:str):
        if any(af.path==path for af in self._attached_files): return
        af=FileAttachment.load(path); self._attached_files.append(af)
        icon={"text":"📄","image":"🖼","pdf":"📑"}.get(af.ftype,"❓")
        err=f" ⚠{af.error}" if af.error else ""
        self._att_list.addItem(f"{icon} {af.name}  ({FileAttachment.fmt_size(af.size)}){err}")
        self._log(f"{'⚠️' if af.error else '📎'}  {af.name}: {af.error or af.ftype}")
        self._reset_question_state()

    def _att_clear(self):
        self._attached_files.clear(); self._att_list.clear(); self._att_info_update(); self._reset_question_state()

    def _att_ctx(self,pos):
        item=self._att_list.itemAt(pos)
        if not item: return
        row=self._att_list.row(item); m=QMenu(self)
        a=QAction("✕  Remove",self)
        a.triggered.connect(lambda: (
            self._attached_files.pop(row) if row<len(self._attached_files) else None,
            self._att_list.takeItem(row),
            self._att_info_update()
        ))
        m.addAction(a); m.exec(QCursor.pos())

    def _att_info_update(self):
        files=self._attached_files
        if not files:
            self._att_info.setText("添付なし")
            if hasattr(self,'_img_warn_row'): self._img_warn_row.setVisible(False)
            return
        texts=sum(1 for af in files if af.ftype in ("text","pdf") and not af.error)
        imgs =sum(1 for af in files if af.ftype=="image" and not af.error)
        errs =sum(1 for af in files if af.error)
        total=sum(af.size for af in files)
        p=[]
        if texts: p.append(f"テキスト{texts}件")
        if imgs:  p.append(f"🖼 画像{imgs}件")
        if errs:  p.append(f"⚠ エラー{errs}件")
        self._att_info.setText(f"{len(files)}件 {FileAttachment.fmt_size(total)}  "+"  ".join(p))
        # 画像が含まれていれば警告バナーを表示
        if hasattr(self,'_img_warn_row'):
            self._img_warn_row.setVisible(imgs > 0)

    # ══════════════════════════════════════════════════════════════════
    # AI Services タブ
    # ══════════════════════════════════════════════════════════════════
    def _reload_ai_svc_tree(self):
        self.ai_svc_tree.clear()
        for name,cfg in self.db.get_ai_services().items():
            if not isinstance(cfg,dict): continue  # 不正データをスキップ
            item=QTreeWidgetItem([str(name),cfg.get("type",""),cfg.get("url",""),cfg.get("role",""),cfg.get("model","")])
            color=cfg.get("color","#c0c0c0")
            if cfg.get("enabled",True): item.setForeground(0,QColor(color))
            else:
                for c in range(5): item.setForeground(c,QColor("#3a3a3a"))
            self.ai_svc_tree.addTopLevelItem(item)

    def _ai_svc_ctx(self,pos):
        item=self.ai_svc_tree.itemAt(pos); menu=QMenu(self)
        if item:
            print(f"[DEBUG] _ai_svc_ctx item.text(0)={item.text(0)!r} item.text(1)={item.text(1)!r}", flush=True)
            name=str(item.text(0)); cfg=self.db.get_ai_services().get(name,{})
            for lbl,fn in [("✏️  Edit",lambda _,n=name: self._edit_ai(n)),
                           ("⚫ Disable" if cfg.get("enabled") else "🟢 Enable",lambda _,n=name: self._toggle_ai(n))]:
                a=QAction(lbl,self); a.triggered.connect(fn); menu.addAction(a)
            menu.addSeparator(); da=QAction("🗑  Delete",self); da.triggered.connect(lambda _,n=name: self._del_ai(n)); menu.addAction(da)
        else:
            aa_web=QAction("🌐  Add Web Service",self); aa_web.triggered.connect(self._add_web_service); menu.addAction(aa_web)
            aa_loc=QAction("⚡  Add Local LLM",self); aa_loc.triggered.connect(self._add_local_service); menu.addAction(aa_loc)
        menu.exec(QCursor.pos())

    def _add_web_service(self):
        dlg=WebAIConfigDialog(self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            name,cfg=dlg.get_config()
            if name: self.db.save_ai_service(name,cfg); self._reload_ai_svc_tree(); self._load_ai_cards(); self._log(f"🌐 ＋ {name}")

    def _add_local_service(self):
        dlg=LocalAIConfigDialog(self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            name,cfg=dlg.get_config()
            if name: self.db.save_ai_service(name,cfg); self._reload_ai_svc_tree(); self._load_ai_cards(); self._log(f"⚡ ＋ {name}")

    def _add_ai_service(self):
        dlg=AIConfigDialog(self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            name,cfg=dlg.get_config()
            if name: self.db.save_ai_service(name,cfg); self._reload_ai_svc_tree(); self._load_ai_cards(); self._log(f"＋ {name}")

    def _edit_ai(self,name):
        name=str(name)  # DBキーがboolになっている場合の保護
        cfg=self.db.get_ai_services().get(name,{})
        if not isinstance(cfg,dict): cfg={}  # configがdict以外の場合の保護
        dlg=AIConfigDialog(self,name,cfg)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            nn,nc=dlg.get_config()
            if nn:
                if nn!=name: self.db.delete_ai_service(name)
                self.db.save_ai_service(nn,nc); self._reload_ai_svc_tree(); self._load_ai_cards(); self._log(f"✏️ {nn}")

    def _toggle_ai(self,name):
        cfg=dict(self.db.get_ai_services().get(name,{})); cfg["enabled"]=not cfg.get("enabled",True)
        self.db.save_ai_service(name,cfg); self._reload_ai_svc_tree(); self._load_ai_cards()
        self._log(f"{'🟢' if cfg['enabled'] else '⚫'} {name}")

    def _del_ai(self,name):
        print(f"[DEBUG] _del_ai called name={name!r} type={type(name)}", flush=True)
        dlg=QMessageBox(self); dlg.setWindowTitle("Delete"); dlg.setText(f"「{name}」を削除しますか？")
        dlg.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel); dlg.setStyleSheet(STYLE)
        result=dlg.exec()
        print(f"[DEBUG] _del_ai dialog result={result} Yes={QMessageBox.StandardButton.Yes}", flush=True)
        if result==QMessageBox.StandardButton.Yes:
            print(f"[DEBUG] calling db.delete_ai_service({name!r})", flush=True)
            self.db.delete_ai_service(name)
            print(f"[DEBUG] delete done, reloading...", flush=True)
            self._reload_ai_svc_tree(); self._load_ai_cards(); self._log(f"🗑 {name}")

    # ══════════════════════════════════════════════════════════════════
    # Unknown・統計・コールバック
    # ══════════════════════════════════════════════════════════════════
    def _clean_unknown(self):
        cnt=self.db.count_unknown(self.monitor.session_id)
        if cnt==0: self._log("Unknown（ラベルなし）はありません"); return
        dlg=QMessageBox(self); dlg.setWindowTitle("Clean Unknown"); dlg.setText(f"ラベルなし Unknown を {cnt}件 削除しますか？")
        dlg.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel); dlg.setStyleSheet(STYLE)
        if dlg.exec()==QMessageBox.StandardButton.Yes:
            deleted=self.db.delete_unknown(self.monitor.session_id)
            self._force_refresh_viewer(); self._clear_content_view(); self._log(f"🗑  Unknown {deleted}件 削除")

    def _refresh_stats(self):
        s=self.db.get_stats()
        lines=[f"総メッセージ     : {s['total']}",f"処理対象         : {s['active']}",
               f"Unknown(ラベルなし): {s['unknown_unlabeled']}",f"保存質問数       : {s['questions']}","","── サービス別 ──"]
        lines+=[f"  {k} : {v}" for k,v in s["by_service"].items()]
        self.stats_label.setText("\n".join(lines))

    def _monitor_cb(self,event,svc,text):
        if event=="sensitive":
            self.sig.log_message.emit(f"🔒  機密っぽい文字列を検出 → 保存スキップ（APIキー/トークン系）")
        else:
            self.sig.new_message.emit(svc,text[:50])
    def _on_new_message(self,svc,prev): self._force_refresh_viewer(); self._log(f"{'✓' if svc!='Unknown' else '?'}  {svc}  ·  {prev}")
    def _on_local_result(self,svc,prev): self._force_refresh_viewer(); self._log(f"⚡  {svc}  ·  {prev}")

    def _page_go_prev(self):
        self._page_current=max(0,self._page_current-1); self._render_page()

    def _page_go_next(self):
        total=len(self._page_all_msgs)
        total_pages=max(1,(total+self._page_size-1)//self._page_size)
        self._page_current=min(total_pages-1,self._page_current+1); self._render_page()

    def _on_reset_local_btns(self, names:list):
        for ai_name in names:
            btn=self._ai_cards.get(ai_name,{}).get("act_btn")
            if btn: btn.setEnabled(True); btn.setText("⚡")
        self.local_all_btn.setEnabled(True)
        self.local_all_btn.setText("⚡  SEND ALL LOCAL LLM")
    def _append_log(self,text): self.log_view.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}]  {text}")
    def _log(self,text): self.sig.log_message.emit(text)

    def _update_status(self):
        s=self.db.get_stats()
        parts=[f"{k} {v}" for k,v in s["by_service"].items() if k not in ("Unknown","User")]
        ul=s["unknown_unlabeled"]; qs=s["questions"]
        if ul: parts.append(f"Unknown {ul}")
        msg=f"Session {self.monitor.session_id}  ·  Q:{qs}  ·  "+"  ·  ".join(parts) if parts else "No messages"
        self._statusbar.showMessage(msg)

    # ══════════════════════════════════════════════════════════════════
    # Grid Launch
    # ══════════════════════════════════════════════════════════════════
    def _check_grid_limit(self):
        """Browser AIのチェック数が4を超えたら警告表示"""
        svcs = self.db.get_ai_services()
        browser_checked = [
            name for name, cfg in svcs.items()
            if cfg.get("type") == "browser"
            and self._ai_cards.get(name, {}).get("check")
            and self._ai_cards[name]["check"].isChecked()
        ]
        count = len(browser_checked)
        if count > 4:
            QMessageBox.warning(
                self, "GRID LAUNCH は4画面固定",
                f"現在 {count} 個のAIが選択されています。\n\n"
                f"GRID LAUNCH は画面を2×2の4分割で起動するため、\n"
                f"上から4つ目までのAIのみ起動されます。\n\n"
                f"起動対象: {', '.join(browser_checked[:4])}\n"
                f"スキップ: {', '.join(browser_checked[4:])}"
            )

    def _update_grid_preview(self):
        """未起動時プレビュー：左上:Claude 右上:Gemini ..."""
        if not hasattr(self, '_grid_preview'): return
        svcs = self.db.get_ai_services()
        targets = [
            name for name, cfg in svcs.items()
            if cfg.get("type") == "browser" and cfg.get("enabled") and cfg.get("url")
        ][:4]
        labels = ["左上", "右上", "左下", "右下"]
        if not targets:
            self._grid_preview.setText("有効なBrowser AIなし")
            return
        parts = [f"{labels[i]}:{name}" for i, name in enumerate(targets)]
        self._grid_preview.setText("  ".join(parts))

    def _launch_grid(self):
        chrome_path = self._chrome_path_edit.text().strip() if hasattr(self,'_chrome_path_edit') else ""
        launcher = GridLauncher(chrome_path)
        if not launcher.chrome_path:
            QMessageBox.warning(self, "Chrome未検出",
                "Chromeが見つかりませんでした。\n"
                "SETTINGSタブの「Chrome パス」に chrome.exe のパスを指定してください。")
            return

        screen = QApplication.primaryScreen().availableGeometry()
        sw, sh = screen.width(), screen.height()

        # AIカードのチェック状態を優先 ─ DBのenabledではなく画面の☑を使う
        all_svcs = self.db.get_ai_services()
        svcs_for_launch = {}
        for name, cfg in all_svcs.items():
            card = self._ai_cards.get(name)
            merged = dict(cfg)
            if card:
                merged["enabled"] = card["check"].isChecked()
            svcs_for_launch[name] = merged

        browser_targets = [n for n,c in svcs_for_launch.items()
                           if c.get("type")=="browser" and c.get("enabled")]
        self._log(f"🖥️  起動対象: {', '.join(browser_targets)}")

        # 起動パラメータを一時保存（_on_grid_done_global で参照）
        self._pending_launcher   = launcher
        self._pending_svcs       = svcs_for_launch
        self._pending_sw, self._pending_sh = sw, sh

        self._launch_btn.setEnabled(False)
        self._launch_btn.setText("起動中…")
        self._kill_grid_btn.setEnabled(False)

        def _do():
            try:
                launched = launcher.launch(
                    svcs_for_launch, sw, sh,
                    on_log=lambda m: self.sig.log_message.emit(m)
                )
            except Exception as e:
                import traceback; traceback.print_exc()
                launched = []
                self.sig.log_message.emit(f"❌  Grid起動エラー: {e}")
            self.sig.grid_done.emit(launched, launcher)

        threading.Thread(target=_do, daemon=True).start()

    def _on_grid_done_global(self, launched, launcher):
        """grid_done シグナル受信 → 必ずメインスレッドで実行される"""
        sw = self._pending_sw
        sh = self._pending_sh
        svcs_for_launch = self._pending_svcs

        self._launch_btn.setEnabled(True)
        self._launch_btn.setText("🖥️  LAUNCH GRID")

        if launched:
            self._grid_launcher = launcher
            self._kill_grid_btn.setEnabled(True)
            self._grid_status.setText(f"● {len(launched)}窓 起動中")
            self._grid_status.setStyleSheet("color:#4ade80; font-size:10px;")
            self._grid_preview.setVisible(False)
            self._build_focus_buttons(launched, svcs_for_launch)
            self._grid_focus_row.setVisible(True)
            self._log(f"🖥️  Grid起動完了: {len(launched)}ウィンドウ  ({sw}×{sh})")
        else:
            self._grid_status.setText("● 起動失敗")
            self._grid_status.setStyleSheet("color:#f87171; font-size:10px;")
            self._log("❌  Grid起動失敗：Chromeパスを確認してください")

    def _build_focus_buttons(self, launched: list, svcs: dict):
        """起動後：各AIのフォーカスボタンをグリッドバーに配置"""
        # 既存ボタンを削除
        while self._grid_focus_row.layout().count():
            item = self._grid_focus_row.layout().takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._grid_focus_btns.clear()

        pos_labels = ["↖", "↗", "↙", "↘"]  # 配置位置アイコン
        colors = {name: cfg.get("color","#c0c0c0") for name,cfg in svcs.items()}

        for i, name in enumerate(launched):
            color = colors.get(name, "#c0c0c0")
            pos = pos_labels[i] if i < len(pos_labels) else ""
            btn = QPushButton(f"{pos} {name}")
            btn.setFixedHeight(24)
            # f文字列内の波括弧は {{ }} でエスケープ
            btn.setStyleSheet(
                f"background:#1e1e1e; border:1px solid #333; border-radius:3px;"
                f"color:{color}; font-size:11px; font-weight:600; padding:0 8px;"
                f"QPushButton:hover{{{{ background:#2a2a2a; border-color:{color}; }}}}"
            )
            btn.setToolTip(f"{name} ウィンドウにフォーカス（Ctrl+V で貼り付け）")
            btn.clicked.connect(lambda c, n=name: self._focus_and_copy(n))
            self._grid_focus_row.layout().addWidget(btn)
            self._grid_focus_btns[name] = btn

    def _focus_and_copy(self, ai_name: str):
        """フォーカスボタン押下：コピー → ウィンドウフォーカス"""
        # まずプロンプトをコピー
        self._copy_for_ai(ai_name)
        # フォーカスボタンを一瞬ハイライト
        btn = self._grid_focus_btns.get(ai_name)
        if btn:
            orig = btn.styleSheet()
            btn.setStyleSheet(orig.replace("#1e1e1e","#1e3d2a"))
            QTimer.singleShot(600, lambda: btn.setStyleSheet(orig))

    def _kill_grid(self):
        """強制終了：Chromeプロセスをkill → UIを完全リセット"""
        if self._grid_launcher:
            self._grid_launcher.terminate_all()
            self._grid_launcher = None
        # Windows追加保険：残存chrome.exeを名前で全kill
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "chrome.exe"],
                    capture_output=True
                )
            except Exception:
                pass
        self._launch_btn.setEnabled(True)
        self._launch_btn.setText("🖥️  LAUNCH GRID")
        self._kill_grid_btn.setEnabled(False)
        self._grid_status.setText("● 未起動")
        self._grid_status.setStyleSheet("color:#444444; font-size:10px;")
        self._grid_focus_row.setVisible(False)
        self._grid_preview.setVisible(True)
        self._log("⏹  グリッドを強制終了しました")

    def _close_grid(self):
        """通常終了（closeEvent用）"""
        if self._grid_launcher:
            self._grid_launcher.terminate_all()
            self._grid_launcher = None

    def _refresh_grid_status(self):
        """3秒おきにグリッド状態を更新"""
        if not self._grid_launcher: return
        alive = self._grid_launcher.is_alive()
        if alive:
            self._grid_status.setText(f"● {len(alive)}窓 起動中")
            self._grid_status.setStyleSheet("color:#4ade80; font-size:10px;")
        else:
            # 全ウィンドウが外部から閉じられた場合もUIをリセット
            self._grid_launcher = None
            self._launch_btn.setEnabled(True)
            self._launch_btn.setText("🖥️  LAUNCH GRID")
            self._kill_grid_btn.setEnabled(False)
            self._grid_status.setText("● 全ウィンドウ終了")
            self._grid_status.setStyleSheet("color:#888888; font-size:10px;")
            self._grid_focus_row.setVisible(False)
            self._grid_preview.setVisible(True)

    # ══════════════════════════════════════════════════════════════════
    # Settings: DB初期化
    # ══════════════════════════════════════════════════════════════════
    def _reset_messages_only(self):
        dlg=QMessageBox(self); dlg.setWindowTitle("セッションメッセージを削除")
        dlg.setText("全メッセージ・質問データを削除します。\nAI設定は保持されます。\n\n本当に実行しますか？")
        dlg.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel)
        dlg.setStyleSheet(STYLE)
        if dlg.exec()!=QMessageBox.StandardButton.Yes: return
        with self.db._lock:
            self.db._conn.executescript("""
                DELETE FROM messages;
                DELETE FROM sessions;
            """)
            self.db._conn.commit()
        self.monitor.start_session()
        self._force_refresh_viewer(); self._clear_content_view(); self._refresh_stats(); self._update_status()
        self._log("🗑  セッションメッセージを全削除しました")

    def _reset_db_full(self):
        dlg=QMessageBox(self); dlg.setWindowTitle("⚠️  DB完全初期化")
        dlg.setText("messages / sessions / settings を全削除し、\nAI設定もデフォルトに戻します。\n\n本当に実行しますか？この操作は取り消せません。")
        dlg.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel)
        dlg.setStyleSheet(STYLE)
        if dlg.exec()!=QMessageBox.StandardButton.Yes: return
        with self.db._lock:
            self.db._conn.executescript("""
                DELETE FROM messages;
                DELETE FROM sessions;
                DELETE FROM settings;
                DELETE FROM ai_services;
            """)
            self.db._conn.commit()
        self.db._init_ai_services()
        self.monitor.start_session()
        self._reload_ai_svc_tree(); self._load_ai_cards()
        self._force_refresh_viewer(); self._clear_content_view(); self._refresh_stats(); self._update_status()
        self._log("💣  DBを完全初期化しました（AI設定もリセット）")

    def _show_consent_if_first_run(self):
        """初回起動時のみ動作説明と同意確認を表示"""
        if self.db.get_setting("consent_agreed"):
            return  # 同意済みならスキップ

        msg = QMessageBox(self)
        msg.setWindowTitle("RogoAI Chat Rotator  ─  はじめに")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setStyleSheet(STYLE)
        msg.setText(
            "<b>ご利用前に以下をご確認ください</b><br><br>"
            "本ツールは以下の設計方針で動作します：<br><br>"
            "✅ &nbsp;ブラウザの自動操作・自動入力は一切行いません<br>"
            "✅ &nbsp;プロンプトのコピーと回答の送信は、すべて <b>あなた自身の手動操作</b> です<br>"
            "✅ &nbsp;クリップボードの変化を検出して回答を保存します<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;（AIのCopyボタンを押した結果をローカルDBに記録）<br>"
            "✅ &nbsp;収集したデータは <b>このPCのローカルにのみ保存</b> されます<br>"
            "✅ &nbsp;外部への自動送信・クラウド同期は行いません<br><br>"
            "⚠️ &nbsp;各AIサービスの利用規約に従ってご利用ください<br>"
            "⚠️ &nbsp;正規サブスクリプションアカウントでのみご使用ください<br><br>"
            "<small>この説明は初回起動時のみ表示されます</small>"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        ok_btn = msg.button(QMessageBox.StandardButton.Ok)
        ok_btn.setText("同意して使用する")
        cancel_btn = msg.button(QMessageBox.StandardButton.Cancel)
        cancel_btn.setText("キャンセル")

        result = msg.exec()
        if result == QMessageBox.StandardButton.Ok:
            self.db.set_setting("consent_agreed", "1")
            self._log("✅  利用同意を記録しました")
        else:
            # 同意しない場合はアプリ終了
            QApplication.quit()

    def _update_mode_indicator(self):
        """ヘッダーのモードインジケーターを更新"""
        if not hasattr(self,'_mode_indicator'): return
        if self.monitor.manual_mode:
            self._mode_indicator.setText("🛡 手動取り込み")
            self._mode_indicator.setStyleSheet("color:#4ade80; font-size:10px; margin-right:12px;")
            if hasattr(self,'_capture_btn'): self._capture_btn.setVisible(True)
        else:
            self._mode_indicator.setText("⚠️ 常時監視中")
            self._mode_indicator.setStyleSheet("color:#f87171; font-size:10px; margin-right:12px;")
            if hasattr(self,'_capture_btn'): self._capture_btn.setVisible(False)

    def _manual_capture(self):
        """手動モード用：クリップボードを今すぐ1回取り込む"""
        hint = getattr(self, '_last_copied_ai', "")
        captured=self.monitor.capture_once(hint_ai=hint)
        if captured:
            msg = f"📥  クリップボードを取り込みました"
            if hint: msg += f"  （AI候補: {hint}）"
            self._log(msg)
            self._force_refresh_viewer()
        else:
            self._log("⚠️  変化なし（前回と同じ内容）")

    # ══════════════════════════════════════════════════════════════════
    # 状態保存・復元
    # ══════════════════════════════════════════════════════════════════
    def _save_state(self):
        """終了時にUI状態をDBのsettingsテーブルに保存"""
        try:
            # AIカードのチェック状態を保存
            ai_checks = {
                name: card["check"].isChecked()
                for name, card in self._ai_cards.items()
            }
            state = {
                "win_x":    self.x(),
                "win_y":    self.y(),
                "win_w":    self.width(),
                "win_h":    self.height(),
                "tab_idx":  self.tabs.currentIndex(),
                "fw":       self._gfw(),
                "vp":       self._gvp(),
                "fmt":      self._gfmt(),
                "custom_vp": self._custom_vp,
                "draft_prompt":   self.prompt_edit.toPlainText(),
                "draft_oneshot":  self.oneshot_edit.toPlainText(),
                "poll_interval":  int(self.monitor.poll),
                "splitter_sizes": json.dumps(
                    self._sender_splitter.sizes() if hasattr(self,'_sender_splitter') else [620,220]
                ),
                "auto_del_unknown": int(
                    self._auto_del_unknown.isChecked() if hasattr(self,'_auto_del_unknown') else 0
                ),
                "chrome_path": (
                    self._chrome_path_edit.text() if hasattr(self,'_chrome_path_edit') else ""
                ),
                "ai_checks": ai_checks,
                "manual_mode": int(self.monitor.manual_mode),   # ← 追加
            }
            self.db.set_setting("ui_state", json.dumps(state, ensure_ascii=False))
        except Exception:
            pass

    def _restore_state(self):
        """起動時にDBから前回状態を復元"""
        try:
            raw = self.db.get_setting("ui_state")
            if not raw: return
            state = json.loads(raw)

            # ウィンドウ位置・サイズ（画面内に収まる範囲で）
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(0, min(state.get("win_x", 100), screen.width()  - 200))
            y = max(0, min(state.get("win_y", 100), screen.height() - 200))
            w = max(760, min(state.get("win_w", 900), screen.width()))
            h = max(400, min(state.get("win_h", 580), screen.height()))
            self.setGeometry(x, y, w, h)

            # アクティブタブ
            idx = state.get("tab_idx", 0)
            if 0 <= idx < self.tabs.count():
                self.tabs.setCurrentIndex(idx)

            # FW / VP / Fmt（コンボボックス）
            fw = state.get("fw","none");   self._sel_fw(fw)
            vp = state.get("vp","none");   self._sel_vp(vp)
            fmt = state.get("fmt","none"); self._sel_fmt(fmt)

            # カスタム観点
            cv = state.get("custom_vp","")
            if cv: self.custom_vp_edit.setText(cv); self._custom_vp=cv

            # プロンプト下書き
            draft = state.get("draft_prompt","")
            if draft: self.prompt_edit.setPlainText(draft)
            oneshot = state.get("draft_oneshot","")
            if oneshot: self.oneshot_edit.setPlainText(oneshot)

            # ポーリング間隔
            poll = state.get("poll_interval", 1)
            self.monitor.poll = poll
            if hasattr(self,'_poll_spin'): self._poll_spin.setValue(poll)

            # Splitter 比率
            sizes_raw = state.get("splitter_sizes")
            if sizes_raw and hasattr(self,'_sender_splitter'):
                sizes = json.loads(sizes_raw)
                if sum(sizes) > 0:
                    self._sender_splitter.setSizes(sizes)

            # Unknown自動削除
            if hasattr(self,'_auto_del_unknown'):
                self._auto_del_unknown.setChecked(bool(state.get("auto_del_unknown",0)))

            # Chromeパス
            cp = state.get("chrome_path","")
            if cp and hasattr(self,'_chrome_path_edit'):
                self._chrome_path_edit.setText(cp)

            # AIカードのチェック状態を復元
            ai_checks = state.get("ai_checks", {})
            for name, checked in ai_checks.items():
                card = self._ai_cards.get(name)
                if card:
                    card["check"].setChecked(checked)

            # 取り込みモードを復元
            manual = bool(state.get("manual_mode", 1))   # デフォルト手動
            self.monitor.manual_mode = manual
            if hasattr(self,'_mode_manual'): self._mode_manual.setChecked(manual)
            if hasattr(self,'_mode_auto'):   self._mode_auto.setChecked(not manual)
            self._update_mode_indicator()

        except Exception:
            pass  # 復元失敗は無視（初回起動扱い）

    def closeEvent(self,event):
        self._save_state()   # 状態保存
        self.monitor.stop()
        if self._grid_launcher:
            self._grid_launcher.terminate_all()
        cnt=self.db.count_unknown(self.monitor.session_id)
        # 自動削除が有効な場合は確認なしで削除
        if cnt>0 and hasattr(self,'_auto_del_unknown') and self._auto_del_unknown.isChecked():
            self.db.delete_unknown(self.monitor.session_id)
            event.accept(); return
        if cnt>0:
            dlg=QMessageBox(self); dlg.setWindowTitle("Exit"); dlg.setText(f"Unknown {cnt}件 を削除して終了しますか？")
            dlg.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel); dlg.setStyleSheet(STYLE)
            r=dlg.exec()
            if r==QMessageBox.StandardButton.Cancel: self.monitor.start(); event.ignore(); return
            if r==QMessageBox.StandardButton.Yes: self.db.delete_unknown(self.monitor.session_id)
        event.accept()


# ─────────────────────────────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────────────────────────────
def main():
    if not HAS_QT:   print("[❌] pip install PyQt6"); sys.exit(1)
    if not _init_cb():print("[❌] pip install pyperclip"); sys.exit(1)
    db=ChatDatabase("chat_rotator.db"); monitor=ClipboardMonitor(db,poll=0.8)
    monitor.start_session(); monitor.start()
    app=QApplication(sys.argv); app.setApplicationName("RogoAI Chat Rotator")

    # アイコン設定（exe化後もリソースを正しく参照）
    def _resource(rel):
        if hasattr(sys, "_MEIPASS"):
            return os.path.join(sys._MEIPASS, rel)
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)

    icon_path = _resource("icon.ico")
    if os.path.exists(icon_path):
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon_path))

    win=MainWindow(db,monitor)
    if os.path.exists(icon_path):
        from PyQt6.QtGui import QIcon
        win.setWindowIcon(QIcon(icon_path))

    win.prompt_edit.textChanged.connect(win._reset_question_state)
    win.show(); sys.exit(app.exec())

if __name__=="__main__":
    main()
