PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('USER','ADMIN')),
 status TEXT NOT NULL CHECK(status IN ('ACTIVE','DISABLED')), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 csrf_token TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
 id TEXT PRIMARY KEY, seller_id TEXT NOT NULL REFERENCES users(id),
 title TEXT, description TEXT, category TEXT, condition TEXT, price_cents INTEGER,
 status TEXT NOT NULL CHECK(status IN
 ('DRAFT','PENDING_REVIEW','PUBLISHED','RESERVED','CONFIRMED','COMPLETED','OFFLINE')),
 version INTEGER NOT NULL DEFAULT 1, first_published_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(price_cents IS NULL OR (typeof(price_cents)='integer'
 AND price_cents BETWEEN 1 AND 9999999))
);
CREATE TABLE IF NOT EXISTS images (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
 storage_key TEXT NOT NULL UNIQUE, content_type TEXT NOT NULL,
 byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 5242880),
 sort_order INTEGER NOT NULL CHECK(sort_order BETWEEN 1 AND 5),
 UNIQUE(product_id,sort_order)
);
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 submission_version INTEGER NOT NULL, snapshot_json TEXT NOT NULL,
 reviewer_id TEXT REFERENCES users(id), decision TEXT NOT NULL,
 reason TEXT, created_at TEXT NOT NULL, handled_at TEXT,
 UNIQUE(product_id,submission_version),
 CHECK(decision IN ('PENDING','APPROVED','REJECTED')),
 CHECK(decision!='REJECTED' OR (reason IS NOT NULL AND length(trim(reason))>0))
);
CREATE TABLE IF NOT EXISTS reservations (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 buyer_id TEXT NOT NULL REFERENCES users(id), price_snapshot INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN
 ('PENDING_SELLER','SELLER_CONFIRMED','CANCELLED','REJECTED','CLOSED','COMPLETED')),
 reason TEXT, created_at TEXT NOT NULL, confirmed_at TEXT, completed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_reservation ON reservations(product_id)
 WHERE status IN ('PENDING_SELLER','SELLER_CONFIRMED');
CREATE TABLE IF NOT EXISTS request_results (
 user_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL, request_key TEXT NOT NULL,
 parameter_hash TEXT NOT NULL, reservation_id TEXT NOT NULL REFERENCES reservations(id),
 response_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 PRIMARY KEY(user_id,action,request_key)
);
CREATE TABLE IF NOT EXISTS reports (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 reporter_id TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','RESOLVED')),
 result TEXT, handled_by TEXT REFERENCES users(id), created_at TEXT NOT NULL, handled_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_report ON reports(reporter_id,product_id)
 WHERE status='PENDING';
CREATE TABLE IF NOT EXISTS close_requests (
 id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL REFERENCES reservations(id),
 applicant_id TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
 result TEXT, handled_by TEXT REFERENCES users(id), created_at TEXT NOT NULL, handled_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_close ON close_requests(reservation_id)
 WHERE status='PENDING';
CREATE TABLE IF NOT EXISTS audit_logs (
 id TEXT PRIMARY KEY, actor_id TEXT REFERENCES users(id), object_id TEXT,
 action TEXT NOT NULL, before_state TEXT, after_state TEXT,
 result TEXT NOT NULL CHECK(result IN ('SUCCESS','FAILURE')), reason TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS product_filter ON products(status,category,price_cents,id);
CREATE INDEX IF NOT EXISTS product_published ON products(status,first_published_at DESC,id);
CREATE INDEX IF NOT EXISTS buyer_reservations ON reservations(buyer_id,created_at DESC);
CREATE INDEX IF NOT EXISTS completed_reservations ON reservations(completed_at);
CREATE INDEX IF NOT EXISTS audit_filter ON audit_logs(created_at,actor_id,object_id,result);
