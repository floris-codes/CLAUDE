// Vercel Serverless Function — Fetch all customers from Shopify (paginated)
// Client sends shop domain + token in headers

export default async function handler(req, res) {
  const shopDomain = req.headers['x-shopify-domain'];
  const shopToken = req.headers['x-shopify-access-token'];

  if (!shopDomain || !shopToken) {
    return res.status(400).json({ error: 'Missing Shopify domain or token headers' });
  }

  const { created_at_min, page_info } = req.query;
  const domain = shopDomain.replace(/^https?:\/\//, '').replace(/\/$/, '');

  let url;
  if (page_info) {
    url = `https://${domain}/admin/api/2024-01/customers.json?page_info=${page_info}&limit=250`;
  } else {
    url = `https://${domain}/admin/api/2024-01/customers.json?limit=250&fields=id,email,first_name,last_name,created_at,orders_count,total_spent`;
    if (created_at_min) url += `&created_at_min=${encodeURIComponent(created_at_min)}`;
  }

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

    // Extract pagination from Link header
    let nextPageInfo = null;
    const linkHeader = response.headers.get('link');
    if (linkHeader) {
      const nextMatch = linkHeader.match(/page_info=([^>&]+)>;\s*rel="next"/);
      if (nextMatch) nextPageInfo = nextMatch[1];
    }

    return res.status(200).json({ customers: data.customers || [], nextPageInfo });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
