export type Invoice = { amount: string };
export function label(invoice: Invoice) { return `$${invoice.amount}`; }
