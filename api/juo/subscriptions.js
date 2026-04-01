// Vercel Serverless Function — Proxy JUO subscription requests
// Forwards pagination params and API key to JUO's real API

export default async function handler(req, res) {
  const juoKey = req.headers['x-juo-admin-api-key'];
  if (!juoKey) {
    return res.status(400).json({ error: 'Missing X-Juo-Admin-Api-Key header' });
  }

  const { limit = 100, after } = req.query;
  let url = `https://api.juo.io/admin/v1/subscriptions?limit=${limit}`;
  if (after) url += `&after=${encodeURIComponent(after)}`;

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
    return res.status(200).json(data);
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
