"""Use bundled CA roots without overriding an explicitly configured trust store."""
import os

import certifi

os.environ.setdefault('SSL_CERT_FILE', certifi.where())
