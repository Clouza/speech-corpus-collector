from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv

from corpus_api.auth import (
    ApiKeyError,
    ApiKeyStoreError,
    generate_api_key,
    validate_api_key,
    validate_secret,
)
from corpus_api.catalog import (
    CatalogError,
    CorpusQuery,
    QueryError,
    export_records,
    list_records,
    parse_query,
)


DEFAULT_ENV_PATH = Path(__file__).resolve().parent / ".env"
DEFAULT_CATALOG_PATH = Path("data/catalog/corpus.jsonl")
DEFAULT_KEY_STORE_PATH = Path("data/api_keys.json")


class CorpusApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        catalog_path: Path,
        key_store_path: Path,
        api_secret: str,
    ) -> None:
        super().__init__(server_address, CorpusRequestHandler)
        self.catalog_path = catalog_path
        self.key_store_path = key_store_path
        self.api_secret = api_secret


class CorpusRequestHandler(BaseHTTPRequestHandler):
    server: CorpusApiServer

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if request.path not in {"/corpus", "/corpus/export"}:
            self._send_error(HTTPStatus.NOT_FOUND, "Endpoint Not Found")
            return
        if not self._authenticate():
            return

        try:
            if request.path == "/corpus":
                query = parse_query(request.query)
                page = list_records(self.server.catalog_path, query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "data": page.records,
                        "pagination": {
                            "total": page.total,
                            "limit": page.limit,
                            "offset": page.offset,
                            "has_more": page.offset + len(page.records) < page.total,
                        },
                    },
                )
                return

            query = parse_query(request.query, export=True)
            self._send_export(query)
        except QueryError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except CatalogError:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Corpus Catalog Is Unavailable",
            )

    def _authenticate(self) -> bool:
        authorization = self.headers.get("Authorization")
        header_key = self.headers.get("X-API-Key")
        if authorization:
            scheme, separator, value = authorization.partition(" ")
            api_key = value.strip() if separator and scheme.casefold() == "bearer" else ""
        else:
            api_key = header_key.strip() if header_key else ""

        try:
            validate_api_key(
                api_key,
                self.server.api_secret,
                self.server.key_store_path,
            )
        except ApiKeyStoreError:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "API Key Registry Is Unavailable",
            )
            return False
        except ApiKeyError:
            self._send_error(
                HTTPStatus.UNAUTHORIZED,
                "Invalid or Expired API Key",
                headers={"WWW-Authenticate": "Bearer"},
            )
            return False
        return True

    def _send_export(self, query: CorpusQuery) -> None:
        if not self.server.catalog_path.is_file():
            raise CatalogError("Corpus Catalog Does Not Exist")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="corpus-export.jsonl"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            for line in export_records(self.server.catalog_path, query):
                self.wfile.write(line)
        except (BrokenPipeError, ConnectionResetError):
            return
        except CatalogError as exc:
            # headers are already sent for a streamed response, so close cleanly
            self.log_error("Corpus Export Failed: %s", exc)
            self.close_connection = True

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(
            {"error": {"status": status.value, "message": message}},
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Corpus API Proof of Concept")
    parser.add_argument(
        "--env",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help="Environment File Path",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate-key", help="Generate a Short API Key")
    generate.add_argument("--name", required=True, help="API Key Name")
    generate.add_argument(
        "--expires-in-days",
        type=int,
        default=30,
        help="API Key Lifetime in Days",
    )
    generate.add_argument(
        "--key-store",
        type=Path,
        default=DEFAULT_KEY_STORE_PATH,
        help="API Key Registry Path",
    )

    serve = commands.add_parser("serve", help="Run the Read-Only Corpus API")
    serve.add_argument("--host", default="127.0.0.1", help="Server Host")
    serve.add_argument("--port", type=int, default=8765, help="Server Port")
    serve.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG_PATH,
        help="Corpus JSONL Path",
    )
    serve.add_argument(
        "--key-store",
        type=Path,
        default=DEFAULT_KEY_STORE_PATH,
        help="API Key Registry Path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(args.env, override=False)
    try:
        api_secret = validate_secret(os.getenv("CORPUS_API_SECRET"))
        if args.command == "generate-key":
            api_key = generate_api_key(
                api_secret,
                args.name,
                args.key_store.resolve(),
                expires_in_days=args.expires_in_days,
            )
            claims = validate_api_key(
                api_key,
                api_secret,
                args.key_store.resolve(),
            )
            expiration = datetime.fromtimestamp(claims.expires_at, UTC).isoformat()
            print(api_key)
            print(f"Valid Until: {expiration}", file=sys.stderr)
            return 0
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ValueError("Port Must Be Between 1 and 65535")
            catalog_path = args.catalog.resolve()
            key_store_path = args.key_store.resolve()
            server = CorpusApiServer(
                (args.host, args.port),
                catalog_path,
                key_store_path,
                api_secret,
            )
            print(f"Corpus API: http://{args.host}:{args.port}")
            print(f"Catalog: {catalog_path}")
            print(f"API Key Registry: {key_store_path}")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("Stopping Corpus API")
            finally:
                server.server_close()
            return 0
    except (ApiKeyError, ApiKeyStoreError, OSError, ValueError) as exc:
        print(f"API Error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
