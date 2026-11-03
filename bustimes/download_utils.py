import logging
import tempfile
from datetime import UTC, datetime
from functools import cache
from http import HTTPStatus
from pathlib import Path

import requests
import requests.certs
from django.utils.http import http_date, parse_http_date

CERTS_DIR = Path(__file__).parent / "certs"


@cache
def get_ca_bundle(extra_ca_certs: tuple) -> str:
    """Path to a CA bundle combining the default trust store with any
    extra intermediate certificates needed for servers that fail to send
    their full certificate chain. Written once (cached) to the system
    temp directory, since the app directory may not be writable.
    """
    with open(requests.certs.where(), "rb") as default_bundle:
        bundle = default_bundle.read()
    for extra_ca_cert in extra_ca_certs:
        with open(CERTS_DIR / extra_ca_cert, "rb") as extra_file:
            bundle += b"\n" + extra_file.read()
    bundle_path = Path(tempfile.gettempdir()) / f"{'_'.join(extra_ca_certs)}_bundle.pem"
    with open(bundle_path, "wb") as open_file:
        open_file.write(bundle)
    return str(bundle_path)


def write_file(path, response):
    with open(path, "wb") as open_file:
        open_file.writelines(response.iter_content(chunk_size=102400))


def download(path, url, session=None):
    response = (session or requests).get(url, stream=True, timeout=61)
    response.raise_for_status()
    write_file(path, response)


def download_if_modified(path, source, session=None, extra_ca_certs: tuple = ()):
    headers = {"User-Agent": "bustimes.org"}
    if source.last_modified:
        headers["if-modified-since"] = http_date(source.last_modified.timestamp())
    if source.etag:
        headers["if-none-match"] = source.etag

    verify = get_ca_bundle(extra_ca_certs) if extra_ca_certs else True

    response = (session or requests).get(
        source.url, headers=headers, stream=True, timeout=61, verify=verify
    )

    modified = response.status_code != HTTPStatus.NOT_MODIFIED

    if last_modified := response.headers.get("last-modified"):
        last_modified = datetime.fromtimestamp(parse_http_date(last_modified), UTC)

    if not response.ok:
        logger = logging.getLogger(__name__)
        logger.error(f"{response} {response.url}")
        modified = None
    elif modified:
        write_file(path, response)

        source.last_modified = last_modified
        source.etag = response.headers.get("etag", "")
        source.save(update_fields=["last_modified", "etag"])

    return modified, last_modified
