import os
import logging
import requests
import pandas as pd
import math
import re
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from openai import OpenAI

# ─── Load configuration ──────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CSV_URL    = os.getenv("GOOGLE_SHEET_URL")
APPS_URL   = os.getenv("APPS_SCRIPT_URL", "").strip()
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# Ensure required environment variables are set
for name, var in [
    ("GOOGLE_SHEET_URL", CSV_URL),
    ("APPS_SCRIPT_URL", APPS_URL),
    ("OPENAI_API_KEY", OPENAI_KEY)
]:
    if not var:
        raise RuntimeError(f"Missing {name} in .env")

# Ensure the Apps Script URL ends with /exec
if not APPS_URL.endswith("/exec"):
    APPS_URL = APPS_URL.rstrip("/") + "/exec"

# Initialize OpenAI client (v1.x)
client = OpenAI(api_key=OPENAI_KEY)

logging.info("CSV URL: %s", CSV_URL)
logging.info("Apps Script URL: %s", APPS_URL)

# ─── Flask setup ────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static")

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/data")
def get_data():
    logging.info("Fetching CSV from: %s", CSV_URL)
    try:
        df = pd.read_csv(CSV_URL)
        records = df.to_dict(orient="records")
        # Replace NaN with None for valid JSON
        for rec in records:
            for k, v in rec.items():
                if isinstance(v, float) and math.isnan(v):
                    rec[k] = None
        return jsonify(records)
    except Exception as e:
        logging.exception("Failed to load CSV")
        return jsonify({"error": "Could not read sheet", "details": str(e)}), 500

@app.route("/add")
def add_row():
    params = request.args.to_dict(flat=True)
    if not params:
        return jsonify({"error": "No data to add"}), 400
    params["action"] = "add"
    try:
        resp = requests.get(APPS_URL, params=params)
        resp.raise_for_status()
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logging.exception("Add proxy failed")
        return jsonify({"error": "Add failed", "details": str(e)}), 500

@app.route("/delete")
def delete_row():
    key = request.args.get("key", "").strip()
    if not key:
        return jsonify({"error": "Missing key parameter"}), 400
    try:
        resp = requests.get(APPS_URL, params={"action": "delete", "key": key})
        resp.raise_for_status()
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logging.exception("Delete proxy failed")
        return jsonify({"error": "Delete failed", "details": str(e)}), 500

@app.route("/query")
def query_sheet():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    # Simple pattern: list X for records where Y greater/less than N
    sel = re.search(r"list\s+([\w_,\s]+)\s+for", q, re.IGNORECASE)
    where = re.search(
        r"where\s+(\w+)\s+(greater|less) than\s+([0-9]+(?:\.[0-9]+)?)",
        q, re.IGNORECASE
    )
    if sel and where:
        cols = [c.strip() for c in sel.group(1).split("and")]
        field, op, val = where.group(1), where.group(2).lower(), float(where.group(3))
        df = pd.read_csv(CSV_URL)
        df_filtered = df[df[field] > val] if op == "greater" else df[df[field] < val]
        df_out = df_filtered[cols]
        result = df_out.fillna("").to_dict(orient="records")
        return jsonify({"result": result})

    # Fallback: prompt LLM with first 10 rows
    try:
        df = pd.read_csv(CSV_URL)
        sample = df.head(10).to_dict(orient="records")
        prompt = f"Here are the first 10 rows:\n{sample}\n\nAnswer: {q}"
        resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        answer = resp.choices[0].message.content.strip()
        return jsonify({"answer": answer})
    except Exception as e:
        logging.exception("Query failed")
        return jsonify({"error": "Query failed", "details": str(e)}), 500

if __name__ == "__main__":
    logging.info("Starting Flask on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
