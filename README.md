# Multi-Agent Platform UI on Flask

Flask をベースにしたマルチエージェント・プラットフォームのフロントエンドとオーケストレーション層をまとめたリポジトリです。単一ページアプリ (SPA) を配信しつつ、以下の 3 つのバックエンドサービスにアクセスし、ユーザーの入力内容に応じて適切なエージェントへルーティングします。

- **FAQ_Gemini** : RAG ベースでナレッジ検索と回答を行う FAQ サービス。
- **Browser Agent** : ウェブブラウジングやサイト操作を代行する自動化エージェント。
- **IoT Agent** : IoT デバイスの状態取得や制御を行う API。

## アーキテクチャ概要

```
┌─────────────┐      ┌──────────────┐
│  Single Page │      │ MultiAgent    │    ┌─────────────┐
│  Application │◀────▶│ Orchestrator  │───▶│ FAQ_Gemini   │
└─────────────┘  SSE  │  (LangGraph)  │    └─────────────┘
        ▲             │                │
        │             │                │    ┌─────────────┐
  Static assets       │                ├───▶│ BrowserAgent │
        │             │                │    └─────────────┘
        ▼             │                │    ┌─────────────┐
   Flask (app.py) ────┴────────────────┴───▶│  IoT Agent   │
```

- `app.py` が SPA の配信、API プロキシ、LangGraph ベースのマルチエージェント・オーケストレーターを兼任します。
- `/orchestrator/chat` へのリクエストは、`OPENAI_API_KEY` で認証された `ChatOpenAI` モデル (`ORCHESTRATOR_MODEL` 環境変数で指定、既定は `gpt-4.1-2025-04-14`) を用いてプランを作成し、FAQ・ブラウザ・IoT 各エージェントへ順次タスクを実行します。
- オーケストレーション結果は Server-Sent Events (SSE) で逐次クライアントにストリーミングされ、タスク進行状況や完了メッセージを UI に反映します。

## 主な機能

- SPA 配信 (`index.html` および `assets/` ディレクトリ) と静的ファイルサーブ。
- FAQ_Gemini への API プロキシ (`/rag_answer`, `/conversation_history`, `/conversation_summary`, `/reset_history`)。
- IoT Agent への透過プロキシ (`/iot_agent/**`) とチャット連携。
- Browser Agent とのチャットおよび進捗ストリーミング。
- LangGraph による自動タスクプランニング (`ORCHESTRATOR_MAX_TASKS` で最大タスク数を制御)。

## 必要条件

- Python 3.11 以降 (Docker イメージは `python:3.11-slim` を使用)
- `pip` または Docker / Docker Compose
- OpenAI API キー (`OPENAI_API_KEY`)
- FAQ_Gemini / Browser Agent / IoT Agent の各バックエンド (ローカルまたはネットワーク経由)

## 環境変数

| 変数 | 説明 | 既定値 |
| ---- | ---- | ------ |
| `OPENAI_API_KEY` | LangGraph オーケストレーターが利用する OpenAI API キー。`.env` に記述して読み込まれます。 | (必須) |
| `FAQ_GEMINI_API_BASE` | FAQ_Gemini のベース URL をカンマ区切りで列挙。先頭から順に接続を試行します。 | `http://localhost:5000,http://faq_gemini:5000` |
| `FAQ_GEMINI_TIMEOUT` | FAQ_Gemini へのタイムアウト (秒)。 | `30` |
| `BROWSER_AGENT_API_BASE` | Browser Agent のベース URL をカンマ区切りで列挙。 | `http://localhost:5005,http://browser_agent:5005` |
| `BROWSER_AGENT_TIMEOUT` | Browser Agent へのタイムアウト (秒)。 | `120` |
| `IOT_AGENT_API_BASE` | IoT Agent のベース URL をカンマ区切りで列挙。 | `https://iot-agent.project-kk.com` |
| `IOT_AGENT_TIMEOUT` | IoT Agent へのタイムアウト (秒)。 | `30` |
| `ORCHESTRATOR_MODEL` | ChatOpenAI で使用するモデル名。 | `gpt-4.1-2025-04-14` |
| `ORCHESTRATOR_MAX_TASKS` | プランで生成されるタスクの最大数。 | `5` |

`.env` ファイルを作成すると `app.py` の `_load_env_file()` により自動で読み込まれます。

## ローカル開発手順

1. 依存関係をインストールします。

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. `.env` を用意し、少なくとも `OPENAI_API_KEY` を設定します。必要に応じて各 API の接続先も上書きしてください。

   ```bash
   cat <<'EOF' > .env
   OPENAI_API_KEY=sk-...
   FAQ_GEMINI_API_BASE=http://localhost:5000
   BROWSER_AGENT_API_BASE=http://localhost:5005
   IOT_AGENT_API_BASE=http://localhost:6000
   EOF
   ```

3. Flask アプリを起動します。

   ```bash
   python app.py
   ```

   ブラウザで `http://localhost:5050` にアクセスすると UI と SSE ストリームが利用できます。

## Docker / Docker Compose

Docker を利用すると依存関係をホストにインストールせずに試せます。

```bash
docker compose up --build
```

- `docker-compose.yml` はホットリロード向けにリポジトリをボリュームマウントし、`FLASK_DEBUG=1` で開発モードを有効にします。
- 外部の FAQ_Gemini / Browser Agent / IoT Agent コンテナを同じネットワークに接続する場合は、`MULTI_AGENT_NETWORK` 環境変数で共有ネットワーク名を指定してください (既定: `multi_agent_platform_net`)。
- 停止するには `Ctrl+C` または別ターミナルから `docker compose down`。

## 公開 API エンドポイント

| メソッド | パス | 説明 |
| -------- | ---- | ---- |
| `POST` | `/orchestrator/chat` | LangGraph オーケストレーターに問い合わせ。SSE ストリームを返します。 |
| `POST` | `/rag_answer` | FAQ_Gemini の `/rag_answer` へプロキシ。 |
| `GET` | `/conversation_history` | FAQ_Gemini の会話履歴を取得。 |
| `GET` | `/conversation_summary` | FAQ_Gemini の会話要約を取得。 |
| `POST` | `/reset_history` | FAQ_Gemini の会話履歴をリセット。 |
| `*` | `/iot_agent/**` | IoT Agent API への透過プロキシ。 |
| `GET` | `/` および `/assets/**` | SPA と静的アセットの提供。 |

## プロジェクト構成

- `app.py` : Flask アプリケーションおよびマルチエージェント・オーケストレーターの実装。
- `index.html` と `assets/` : ユーザーインターフェースとなる静的フロントエンド資産。
- `requirements.txt` : Python 依存パッケージ一覧。
- `Dockerfile` : コンテナビルド設定 (`python:3.11-slim` ベース)。
- `docker-compose.yml` : 開発用 Compose 設定。外部ネットワーク `multi_agent_platform_net` を前提にしています。

## ライセンス

提供されたファイルに基づくプロジェクトであり、特定のライセンスは含まれていません。
