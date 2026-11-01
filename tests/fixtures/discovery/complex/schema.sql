CREATE TABLE invoices (id INTEGER PRIMARY KEY, amount DECIMAL(12,2) NOT NULL);
-- Existing rows are denominated in USD; migration must preserve their meaning.
