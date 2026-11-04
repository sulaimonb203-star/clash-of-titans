import os, hmac, hashlib, sqlite3, datetime
from flask import Flask, request, jsonify, send_from_directory
import db_manager

app = Flask(__name__, static_url_path='/static')

# 1. Health check route for Railway
@app.route('/')
def health_check():
    return "Bot is running", 200

# 2. Calculator route
@app.route('/calculator')
def serve_calculator():
    try:
        return send_from_directory('static', 'calculator.html')
    except Exception as e:
        return f"Calculator not found. Error: {e}", 404

# 3. Paystack webhook
@app.route('/paystack_webhook', methods=['POST'])
def paystack_webhook():
    secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").encode('utf-8')
    signature = request.headers.get('x-paystack-signature')
    if not signature: return jsonify({"status": "error"}), 400
    hash_obj = hmac.new(secret_key, request.get_data(), hashlib.sha512).hexdigest()
    if hash_obj != signature: return jsonify({"status": "error"}), 401

    event = request.json
    if event and event.get('event') == 'charge.success':
        reference = event['data'].get('reference', '')
        try:
            user_id = int(reference.split('_')[1])
            expiry = (datetime.datetime.now() + datetime.timedelta(days=30)).isoformat()
            with sqlite3.connect(db_manager.DB_PATH) as conn:
                conn.execute("UPDATE players SET pass_expiry = ? WHERE telegram_id = ?", (expiry, user_id))
                conn.commit()
        except: pass
    return jsonify({"status": "success"}), 200

def run_server():
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
