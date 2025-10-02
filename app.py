from flask import Flask, send_from_directory

app = Flask(__name__, static_folder="assets", static_url_path="/assets")


@app.route("/")
def serve_index() -> object:
    """Serve the main single-page application."""
    return send_from_directory(app.root_path, "index.html")


@app.route("/<path:path>")
def serve_file(path: str) -> object:
    """Serve any additional static files that live alongside index.html."""
    return send_from_directory(app.root_path, path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
