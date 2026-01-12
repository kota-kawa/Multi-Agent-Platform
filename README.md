# 🎵 Symphony Agent Conductor

<div align="center">
  <img src="assets/icons/Symphony Agent Conductor.png" width="200px">
  <p><strong>あなたのためのAIエージェント・オーケストラ</strong></p>
</div>

Symphony Agent Conductor へようこそ！  
ここは、様々な能力を持ったAIエージェントたち（ブラウザ操作、IoT、スケジュール管理など）を指揮し、あなたの生活やタスクをサポートする司令塔です。

チャットで話しかけるだけで、エージェントたちが連携して動いてくれます！ 🤖✨

---

## ✨ 何ができるの？

*   🗣️ **チャットでお願い**: 「明日の天気を調べて」「電気をつけて」など、自然な会話で指示を出せます。
*   🌐 **ブラウザ操作**: あなたの代わりにWebサイトを見て情報を集めたり、操作したりします。
*   🏠 **スマートホーム (IoT)**: 家電の操作や部屋の環境（温度など）の確認ができます。
*   📅 **スケジュール管理**: 予定の調整や確認もお任せあれ。
*   🧠 **記憶**: 会話の内容やあなたの好みを覚えて、どんどん賢くなります。

## 🚀 すぐに始める (Docker版)

Docker があれば、コマンドひとつでコンサート（システム）が開演します！ 🎼

### 1. 準備 🔑

まずは、AIの頭脳となる APIキーを設定ファイルに書き込みます。
プロジェクトのフォルダに `secrets.env` という名前のファイルを作り、以下の内容を書いて保存してください。

**secrets.env**
```env
OPENAI_API_KEY=sk-proj-xxxxxxxx... (あなたのOpenAI APIキー)
```

> 💡 **ポイント**: `secrets.env` は秘密の鍵なので、他人に見せたり Git にアップロードしたりしないでくださいね。

### 2. 起動 🐳

ターミナル（コマンドプロンプト）で以下のコマンドを実行します。

```bash
docker compose up --build web
```

いろいろな文字が流れますが、準備をしている音合わせのようなものです。しばらく待ちましょう。

### 3. 開演！ 🎭

準備ができたら、ブラウザで以下のURLにアクセスしてください。

👉 **[http://localhost:5050](http://localhost:5050)**

画面が表示されたら成功です！チャット欄に「こんにちは！」と入力して、エージェントたちとの対話を楽しみましょう。

---

## 🛠️ 開発者の方へ (ローカル実行)

Docker を使わずに、自分のパソコン上で直接動かしたい場合はこちら。

1.  **Python の準備**: Python 3.11 以上が必要です。
2.  **インストール**:
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # Windowsなら .venv\Scripts\activate
    pip install -r requirements.txt
    ```
3.  **起動**:
    ```bash
    uvicorn app:app --host 0.0.0.0 --port 5050 --reload
    ```

## 📚 もっと詳しく

詳しいエージェントの設定や、開発の裏側を知りたい方は [AGENTS.md](AGENTS.md) を覗いてみてください。技術的な詳細やカスタマイズ方法が書いてあります。

---

<div align="center">
  Enjoy your Symphony! 🎶
</div>