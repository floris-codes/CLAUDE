// Vercel Serverless Function — Proxy JUO subscription requests
// Forwards pagination params and API key to JUO's real API

export default async function handler(req, res) {
  const juoKey = req.headers['x-juo-admin-api-key'];
  if (!juoKey) {
    return res.status(400).json({ error: 'Missing X-Juo-Admin-Api-Key header' });
  }

  const { limit = 100, cursor } = req.query;
  let url = `https://api.juo.io/admin/v1/subscriptions?limit=${limit}`;
  if (cursor) url += `&cursor=${encodeURIComponent(cursor)}`;

  try {
    const response = await fetch(url, {
      headers: {
        'X-Juo-Admin-Api-Key': juoKey,
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const text = await response.text();
      return res.status(response.status).json({ error: text });
    }

    const data = await response.json();

    // Extract next cursor from Link header
    let nextCursor = null;
    const linkHeader = response.headers.get('link');
    if (linkHeader) {
      const nextMatch = linkHeader.match(/cursor=([^>&]+)>;\s*rel="next"/);
      if (nextMatch) nextCursor = nextMatch[1];
    }

    return res.status(200).json({ ...data, nextCursor });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
