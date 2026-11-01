CREATE TABLE invoices (
  id UUID PRIMARY KEY,
  business_id UUID NOT NULL,
  number INTEGER NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  currency VARCHAR(3) NOT NULL,
  provider_id TEXT,
  UNIQUE(business_id, number)
);
