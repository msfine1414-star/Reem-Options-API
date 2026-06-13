"""
RS Liquidity Flow — License Server
نظام ترخيص البوت مع Supabase PostgreSQL
"""

from flask import Flask, request, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import hashlib
import secrets
import psycopg2
import psycopg2.extras

app = Flask(__name__)
SAU_TZ = ZoneInfo("Asia/Riyadh")

# ── اتصال Supabase ──────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        os.environ.get("DATABASE_URL"),
        cursor_factory=psycopg2.extras.RealDictCursor,
        connect_timeout=10,
        sslmode="require",
    )


def init_db():
    """إنشاء الجدول إذا لم يكن موجوداً"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS licenses (
                    license_key     TEXT PRIMARY KEY,
                    customer_name   TEXT NOT NULL,
                    notes           TEXT DEFAULT '',
                    status          TEXT DEFAULT 'pending',
                    machine_hash    TEXT,
                    ibkr_account    TEXT,
                    created_at      TEXT,
                    activated_at    TEXT,
                    last_check      TEXT,
                    check_count     INTEGER DEFAULT 0
                )
            """)
        conn.commit()


# ── helpers ─────────────────────────────────────────────────

def generate_key() -> str:
    raw = secrets.token_hex(8).upper()
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


def hash_machine(machine_id: str, ibkr_account: str) -> str:
    combined = f"{machine_id}:{ibkr_account}"
    return hashlib.sha256(combined.encode()).hexdigest()


def now_str() -> str:
    return datetime.now(SAU_TZ).isoformat()


def check_admin(data: dict) -> bool:
    return data.get("admin_key") == os.environ.get("ADMIN_SECRET", "RS_ADMIN_2026")


# ── API ─────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": now_str()})


@app.route("/admin/create", methods=["POST"])
def create_license():
    data = request.get_json()
    if not check_admin(data):
        return jsonify({"error": "Unauthorized"}), 401

    customer_name = data.get("customer_name", "Unknown")
    notes         = data.get("notes", "")
    license_key   = generate_key()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO licenses
                (license_key, customer_name, notes, status, created_at, check_count)
                VALUES (%s, %s, %s, 'pending', %s, 0)
            """, (license_key, customer_name, notes, now_str()))
        conn.commit()

    return jsonify({"success": True, "license_key": license_key, "customer_name": customer_name})


@app.route("/verify", methods=["POST"])
@app.route("/activate", methods=["POST"])
def verify():
    data         = request.get_json()
    license_key  = data.get("license_key", "").strip().upper()
    machine_id   = data.get("machine_id", "")
    ibkr_account = data.get("ibkr_account", "")

    if not license_key:
        return jsonify({"valid": False, "reason": "مفتاح الترخيص مفقود"}), 400

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM licenses WHERE license_key = %s", (license_key,))
            lic = cur.fetchone()

            if not lic:
                return jsonify({"valid": False, "reason": "مفتاح الترخيص غير صحيح"})

            if lic["status"] == "revoked":
                return jsonify({"valid": False, "reason": "تم إلغاء هذا الترخيص"})

            machine_hash = hash_machine(machine_id, ibkr_account) if machine_id else None

            if lic["status"] == "pending":
                cur.execute("""
                    UPDATE licenses SET
                        status='active', machine_hash=%s, ibkr_account=%s,
                        activated_at=%s, last_check=%s, check_count=1
                    WHERE license_key=%s
                """, (machine_hash, ibkr_account, now_str(), now_str(), license_key))
                conn.commit()
                return jsonify({"valid": True, "message": "تم التفعيل!", "customer_name": lic["customer_name"]})

            if machine_hash and lic["machine_hash"] and lic["machine_hash"] != machine_hash:
                return jsonify({"valid": False, "reason": "هذا الترخيص مسجل على جهاز آخر"})

            cur.execute("""
                UPDATE licenses SET last_check=%s, check_count=check_count+1
                WHERE license_key=%s
            """, (now_str(), license_key))
            conn.commit()

    return jsonify({"valid": True, "message": "ترخيص صالح", "customer_name": lic["customer_name"]})


@app.route("/admin/list", methods=["POST"])
def list_licenses():
    data = request.get_json()
    if not check_admin(data):
        return jsonify({"error": "Unauthorized"}), 401

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
            rows = cur.fetchall()

    result = [{
        "key":       r["license_key"],
        "customer":  r["customer_name"],
        "status":    r["status"],
        "ibkr":      r["ibkr_account"] or "—",
        "activated": r["activated_at"] or "—",
        "checks":    r["check_count"],
        "notes":     r["notes"] or "",
    } for r in rows]

    return jsonify({"licenses": result, "total": len(result)})


@app.route("/admin/revoke", methods=["POST"])
def revoke():
    data = request.get_json()
    if not check_admin(data):
        return jsonify({"error": "Unauthorized"}), 401

    license_key = data.get("license_key", "").strip().upper()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE licenses SET status='revoked' WHERE license_key=%s", (license_key,))
        conn.commit()

    return jsonify({"success": True, "message": f"تم إلغاء {license_key}"})


@app.route("/admin/transfer", methods=["POST"])
def transfer():
    data = request.get_json()
    if not check_admin(data):
        return jsonify({"error": "Unauthorized"}), 401

    license_key = data.get("license_key", "").strip().upper()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE licenses SET status='pending', machine_hash=NULL, activated_at=NULL
                WHERE license_key=%s
            """, (license_key,))
        conn.commit()

    return jsonify({"success": True, "message": "تم إعادة تعيين الترخيص"})


# ── Start ────────────────────────────────────────────────────

try:
    init_db()
except Exception as e:
    print(f"⚠️ DB init warning: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
