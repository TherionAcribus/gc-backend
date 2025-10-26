from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True)  # autorise les appels depuis Theia (port 3000)

@app.get("/ping")
def ping():
    return jsonify(ok=True, message="Hello from Flask 👋")

@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")
    # TODO: ici tu appelleras ton vrai modèle IA
    return jsonify(reply=f"Echo Flask: {prompt}")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
