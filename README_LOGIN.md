# Terraform Login Troubleshooting

If you encounter the error:
`Error: Host does not support Terraform tokens API`

Check the following:

## 1. Ngrok Free Tier Warning
If you are using **ngrok free tier**, it presents a warning page ("Click to Visit Site") when accessed via a browser. 
Terraform CLI cannot click this button, so it receives HTML instead of JSON, causing the discovery to fail.

**Solutions:**
1.  **Use a paid ngrok account** (removes the warning).
2.  **Use `localtox` or `cloudflared`** which do not have this interstitial page.
3.  **Use `localhost`** if you are running Terraform on the same machine as the server.
    *   `terraform login localhost:8000` (Note: Terraform requires HTTPS for non-localhost, but localhost might work with HTTP if configured).

## 2. Service Discovery
Ensure your server is running and accessible.
Visit `https://your-host/.well-known/terraform.json` in your browser.
You should see:
```json
{
  "modules.v1": "/v1/modules/",
  "login.v1": "/v1/login"
}
```
If you see the ngrok warning page instead, see section 1.
