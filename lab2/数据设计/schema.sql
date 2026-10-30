-- PostgreSQL 16+ 设计稿；本次实验不运行数据库服务。
CREATE TABLE app_user (
 id uuid PRIMARY KEY, username varchar(20) NOT NULL UNIQUE,
 password_hash text NOT NULL,
 role varchar(8) NOT NULL CHECK (role IN ('USER','ADMIN')),
 status varchar(8) NOT NULL CHECK (status IN ('ACTIVE','DISABLED')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE category (id uuid PRIMARY KEY, name varchar(40) NOT NULL UNIQUE);
CREATE TABLE product (
 id uuid PRIMARY KEY, seller_id uuid NOT NULL REFERENCES app_user(id),
 category_id uuid REFERENCES category(id), title varchar(50), description varchar(1000),
 condition varchar(40), price numeric(7,2),
 status varchar(20) NOT NULL CHECK(status IN
 ('DRAFT','PENDING_REVIEW','PUBLISHED','RESERVED','CONFIRMED','COMPLETED','OFFLINE')),
 version integer NOT NULL DEFAULT 1,
 first_published_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(price IS NULL OR price BETWEEN 0.01 AND 99999.99)
);
-- 草稿允许字段暂缺；提交审核前的必填、图片张数及字符范围由服务验证。
CREATE TABLE product_image (
 id uuid PRIMARY KEY, product_id uuid NOT NULL REFERENCES product(id),
 storage_key text NOT NULL UNIQUE, sort_order smallint NOT NULL CHECK(sort_order BETWEEN 1 AND 5),
 content_type varchar(20) NOT NULL CHECK(content_type IN ('image/jpeg','image/png')),
 byte_size integer NOT NULL CHECK(byte_size > 0 AND byte_size <= 5242880),
 UNIQUE(product_id,sort_order)
);
CREATE TABLE review_record (
 id uuid PRIMARY KEY, product_id uuid NOT NULL REFERENCES product(id),
 submission_version integer NOT NULL, reviewer_id uuid REFERENCES app_user(id),
 decision varchar(10) NOT NULL CHECK(decision IN ('PENDING','APPROVED','REJECTED')),
 reason text, created_at timestamptz NOT NULL DEFAULT now(), handled_at timestamptz,
 UNIQUE(product_id,submission_version),
 CHECK(decision <> 'REJECTED' OR (reason IS NOT NULL AND length(trim(reason)) > 0))
);
CREATE TABLE reservation (
 id uuid PRIMARY KEY, product_id uuid NOT NULL REFERENCES product(id),
 buyer_id uuid NOT NULL REFERENCES app_user(id), price_snapshot numeric(7,2) NOT NULL,
 status varchar(24) NOT NULL CHECK(status IN
 ('PENDING_SELLER','SELLER_CONFIRMED','CANCELLED','REJECTED','CLOSED','COMPLETED')),
 created_at timestamptz NOT NULL DEFAULT now(), confirmed_at timestamptz,
 completed_at timestamptz, reason text,
 CHECK(price_snapshot BETWEEN 0.01 AND 99999.99)
);
CREATE UNIQUE INDEX uq_active_reservation ON reservation(product_id)
 WHERE status IN ('PENDING_SELLER','SELLER_CONFIRMED');
CREATE TABLE request_result (
 user_id uuid NOT NULL REFERENCES app_user(id), action varchar(30) NOT NULL,
 request_key varchar(100) NOT NULL, parameter_hash text NOT NULL,
 reservation_id uuid REFERENCES reservation(id), response_json jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 PRIMARY KEY(user_id,action,request_key),
 CHECK(expires_at >= created_at + interval '24 hours')
);
CREATE TABLE product_report (
 id uuid PRIMARY KEY, product_id uuid NOT NULL REFERENCES product(id),
 reporter_id uuid NOT NULL REFERENCES app_user(id), reason text NOT NULL CHECK(length(trim(reason))>0),
 status varchar(10) NOT NULL CHECK(status IN ('PENDING','RESOLVED')),
 result text, handled_by uuid REFERENCES app_user(id),
 created_at timestamptz NOT NULL DEFAULT now(), handled_at timestamptz
);
CREATE UNIQUE INDEX uq_pending_report ON product_report(reporter_id,product_id)
 WHERE status='PENDING';
CREATE TABLE close_request (
 id uuid PRIMARY KEY, reservation_id uuid NOT NULL REFERENCES reservation(id),
 applicant_id uuid NOT NULL REFERENCES app_user(id), reason text NOT NULL CHECK(length(trim(reason))>0),
 status varchar(10) NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
 handled_by uuid REFERENCES app_user(id), result text,
 created_at timestamptz NOT NULL DEFAULT now(), handled_at timestamptz
);
CREATE TABLE audit_log (
 id uuid PRIMARY KEY, actor_id uuid REFERENCES app_user(id), object_id uuid,
 object_type varchar(30) NOT NULL, action varchar(40) NOT NULL,
 before_state varchar(30), after_state varchar(30), result varchar(10) NOT NULL,
 reason text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_product_filter ON product(status,category_id,price,id);
CREATE INDEX ix_product_published ON product(status,first_published_at DESC,id);
CREATE INDEX ix_reservation_buyer ON reservation(buyer_id,status,created_at DESC);
CREATE INDEX ix_reservation_completed ON reservation(completed_at) WHERE status='COMPLETED';
CREATE INDEX ix_audit_query ON audit_log(created_at,actor_id,object_id,result);
-- 关联更新：BEGIN -> 锁product -> 锁reservation -> 校验前态/归属 ->
-- 更新预约及商品 -> 成功日志与请求结果 -> COMMIT。错误回滚后另记失败日志。
-- 成交与关键日志不提供普通物理删除入口；日志保留策略不得短于180天。
