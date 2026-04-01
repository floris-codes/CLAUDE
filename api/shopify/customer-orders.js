// Vercel Serverless Function — Fetch all orders for a Shopify customer
// Client sends shop domain + token in headers, customer ID in query

export default async function handler(req, res) {
  const shopDomain = req.headers['x-shopify-domain'];
  const shopToken = req.headers['x-shopify-access-token'];
  const { customerId } = req.query;

  if (!shopDomain || !shopToken) {
    return res.status(400).json({ error: 'Missing Shopify domain or token headers' });
  }
  if (!customerId) {
    return res.status(400).json({ error: 'Missing customerId query parameter' });
  }

  const domain = shopDomain.replace(/^https?:\/\//, '').replace(/\/$/, '');
  const url = `https://${domain}/admin/api/2024-01/customers/${customerId}/orders.json?status=any&limit=250&fields=id,total_price,financial_status,created_at,tags`;

  try {
    const response = await fetch(url, {
      headers: {
        'X-Shopify-Access-Token': shopToken,
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const text = await response.text();
      return res.status(response.status).json({ error: text });
    }

    const data = await response.json();
    return res.status(200).json(data);
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
