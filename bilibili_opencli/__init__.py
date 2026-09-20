"""Optional OpenCLI bridge for bili2rag.

OpenCLI (npm: @jackwener/opencli) provides a browser-session channel to
Bilibili with capabilities the pipeline lacks (hot/ranking discovery,
threaded comments via the official API, official AI summaries). This
package wraps it as an OPTIONAL subprocess dependency, exactly like
yt-dlp: every caller must degrade gracefully when it is missing.
"""
