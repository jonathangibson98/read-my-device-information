"""Container health check: the web page answers (401 counts, it means a password is set)."""
import os
import sys
import urllib.error
import urllib.request

url = f"http://127.0.0.1:{os.environ.get('PORT', '8099')}/api/status"
try:
    urllib.request.urlopen(url, timeout=5)
except urllib.error.HTTPError as err:
    sys.exit(0 if err.code == 401 else 1)
except Exception:
    sys.exit(1)
