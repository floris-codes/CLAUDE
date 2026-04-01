// Vercel Serverless Function — Check if Shopify credentials are valid
// Client sends shop domain + token in headers

export default async function handler(req, res) {
  const shopDomain = req.headers['x-shopify-domain'];
  const shopToken = req.headers['x-shopify-access-token'];

  if (!shopDomain || !shopToken) {
    return res.status(200).json({ connected: false });
  }

  const domain = shopDomain.replace(/^https?:\/\//, '').replace(/\/$/, '');

  try {
    const response = await fetch(`https://${domain}/admin/api/2024-01/shop.json`, {
      headers: {
        'X-Shopify-Access-Token': shopToken,
        'Content-Type': 'application/json',
      },
    });

    return res.status(200).json({ connected: response.ok });
  } catch {
    return res.status(200).json({ connected: false });
  }
}
