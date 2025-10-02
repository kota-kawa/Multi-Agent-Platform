# Multi-Agent Platform UI on Flask

このリポジトリはシングルページのフロントエンドを Flask アプリケーションとして提供し、Docker Compose を利用してポート 5050 で起動できます。

## 必要条件

- Docker および Docker Compose

## ローカル開発

```bash
pip install -r requirements.txt
python app.py
```

アプリケーションが `http://localhost:5050` で利用可能になります。

## Docker Compose での起動

```bash
docker compose up --build
```

ビルドと起動が完了すると、ブラウザで `http://localhost:5050` にアクセスできます。

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
