"""Read-only deployment gate for Judy seller-card integration."""
import os
import sys
import psycopg2

def main():
    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        print("BLOCKED: SUPABASE_DB_URL missing")
        return 2
    with psycopg2.connect(dsn, connect_timeout=12) as conn:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.v_judy_seller_cards')::text")
            view = cur.fetchone()[0]
            if not view:
                print("BLOCKED: Judy view migration not applied to database")
                return 3
            cur.execute("SELECT count(*) FROM public.v_judy_seller_cards")
            count = cur.fetchone()[0]
            print(f"READ ONLY: Judy view exists; currently {count} eligible seller cards")
    print("READINESS PASS: private view reachable. Dual-gate ingestion and production writes remain DISABLED.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
