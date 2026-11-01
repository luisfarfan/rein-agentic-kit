export async function emitInvoice(http: any, payload: object) {
  try {
    return await http.post('/invoices/emit', payload);
  } catch (error: any) {
    if (error.status === 0) return await http.post('/invoices/emit', payload);
    throw error;
  }
}
