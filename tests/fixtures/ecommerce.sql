-- Ecommerce fixture schema for Insyte integration tests (spec §23).
-- Idempotent: drops and recreates everything in a clean public schema.

DROP TABLE IF EXISTS refunds CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS cities CASCADE;

CREATE TABLE cities (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    state       text
);
COMMENT ON TABLE cities IS 'Cities customers belong to';

CREATE TABLE customers (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    email       text UNIQUE,
    city_id     integer REFERENCES cities (id),
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN customers.email IS 'Contact email';

CREATE TABLE products (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    category    text,
    price       numeric(12, 2) NOT NULL
);

CREATE TABLE orders (
    id            serial PRIMARY KEY,
    customer_id   integer NOT NULL REFERENCES customers (id),
    status        text NOT NULL DEFAULT 'pending',
    total_amount  numeric(12, 2) NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz
);
CREATE INDEX orders_customer_idx ON orders (customer_id);

-- Junction / bridge table between orders and products.
CREATE TABLE order_items (
    order_id    integer NOT NULL REFERENCES orders (id),
    product_id  integer NOT NULL REFERENCES products (id),
    quantity    integer NOT NULL DEFAULT 1,
    unit_price  numeric(12, 2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE payments (
    id          serial PRIMARY KEY,
    order_id    integer NOT NULL REFERENCES orders (id),
    method      text NOT NULL,
    amount      numeric(12, 2) NOT NULL,
    status      text NOT NULL DEFAULT 'captured',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE refunds (
    id          serial PRIMARY KEY,
    payment_id  integer NOT NULL REFERENCES payments (id),
    amount      numeric(12, 2) NOT NULL,
    reason      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO cities (name, state) VALUES ('Bengaluru', 'KA'), ('Mumbai', 'MH');
INSERT INTO customers (name, email, city_id) VALUES
    ('Asha', 'asha@example.com', 1),
    ('Ravi', 'ravi@example.com', 2);
INSERT INTO products (name, category, price) VALUES
    ('Widget', 'Hardware', 199.00),
    ('Gadget', 'Electronics', 999.00);
INSERT INTO orders (customer_id, status, total_amount, completed_at) VALUES
    (1, 'completed', 199.00, now()),
    (2, 'completed', 999.00, now());
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
    (1, 1, 1, 199.00),
    (2, 2, 1, 999.00);
INSERT INTO payments (order_id, method, amount, status) VALUES
    (1, 'upi', 199.00, 'captured'),
    (2, 'card', 999.00, 'captured'),
    (2, 'card', 999.00, 'failed');
INSERT INTO refunds (payment_id, amount, reason) VALUES (2, 100.00, 'partial');

ANALYZE;
