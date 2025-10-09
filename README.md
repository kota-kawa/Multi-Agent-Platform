# Multi-Agent Platform UI on Flask

このリポジトリはシングルページのフロントエンドを Flask アプリケーションとして提供し、Docker Compose を利用してポート 5050 で起動できます。

## 必要条件

- Docker および Docker Compose

## ローカル開発

```bash
pip install -r requirements.txt
python app.py
```

アプリケーションが `http://localhost:5050` で利用可能になります。バックエンドの
[FAQ_Gemini](https://github.com/kota-kawa/FAQ_Gemini/) サービスも別途起動してくだ
さい (デフォルトでは `http://localhost:5000`)。

必要に応じて Flask 側に FAQ_Gemini の接続先を環境変数で指定できます。複数の
候補をカンマ区切りで指定すると、上から順に接続を試みます。

```bash
export FAQ_GEMINI_API_BASE="http://localhost:5000,http://faq_gemini:5000"
export BROWSER_AGENT_API_BASE="http://localhost:5005,http://browser_agent:5005"
python app.py
```

## Docker Compose での起動

```bash
docker compose up --build
```

ビルドと起動が完了すると、ブラウザで `http://localhost:5050` にアクセスできま
す。FAQ_Gemini を別コンテナで動かす場合は同じ Docker ネットワーク上で
`faq_gemini` や `browser_agent` というホスト名になるよう起動してください (例:
FAQ_Gemini リポジトリの Dockerfile を利用して `docker run --name faq_gemini ...`
など)。

終了するには `Ctrl+C` を押すか、別のターミナルで次のコマンドを実行します。

```bash
docker compose down
```

## 構成

- `app.py` : Flask アプリケーション本体。
- `index.html` / `assets/` : 既存のフロントエンド資産。
- `Dockerfile` : Flask アプリケーションをコンテナ化する設定。
- `docker-compose.yml` : コンテナをポート 5050 で起動する Compose 設定。
- `requirements.txt` : Python 依存関係。

## ライセンス

このプロジェクトは提供されたファイルに基づいており、特別なライセンスは含まれていません。
