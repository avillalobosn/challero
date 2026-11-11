"""FastAPI entry point for Duel Archive's local backend."""
from contextlib import asynccontextmanager
import os
import logging
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

import server


@asynccontextmanager
async def lifespan(app):
    if not os.getenv('DATABASE_URL') and not os.getenv('VERCEL') and (server.DATA / 'collection.json').is_file():
        server.queue_refresh()
    yield


app = FastAPI(
    title='Duel Archive',
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    redirect_slashes=False,
)


class FastAPIRoutes(server.ArchiveRoutes):
    def __init__(self, request):
        self.headers = request.headers

    def send(self, status, value, cookie=None, content_type='application/json'):
        headers = server.response_headers(content_type, cookie)
        response_class = JSONResponse if content_type == 'application/json' else Response
        return response_class(value, status_code=status, headers=headers)


HOSTED_UPLOAD_LIMIT = 4_000_000


def hosted_request(database_url, routes, path, body=None, client_ip=None):
    from hosted_store import transaction, DatabaseURLConfigurationError
    from psycopg import Error

    try:
        with transaction(database_url, write=body is not None) as store:
            token = server.HOSTED_STORE.set(store)
            try:
                return routes.get(path) if body is None else routes.post(path, body, client_ip)
            finally:
                server.HOSTED_STORE.reset(token)
    except DatabaseURLConfigurationError:
        logging.getLogger(__name__).error('DATABASE_URL configuration is invalid.')
        return routes.send(503, {'error': 'Hosted database connection URL is invalid.'})
    except Error as error:
        logging.getLogger(__name__).error(
            'Hosted storage failure: type=%s sqlstate=%s',
            type(error).__name__, error.sqlstate or 'none',
        )
        return routes.send(503, {'error': 'Hosted storage is unavailable.'})


@app.api_route('/{path:path}', methods=['GET', 'POST'])
async def archive(request: Request, path: str):
    routes = FastAPIRoutes(request)
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        origin = os.getenv('DUEL_PUBLIC_ORIGIN', '').rstrip('/')
        expected_host = urlsplit(origin).netloc
        if not origin.startswith('https://') or not expected_host or origin != 'https://' + expected_host:
            return routes.send(503, {'error': 'Hosted origin is not configured.'})
        if request.headers.get('Host') != expected_host:
            return routes.send(403, {'error': 'Invalid host'})
        valid_origin = (request.headers.get('Origin') == origin
                        and request.headers.get('X-Requested-With') == 'DuelArchive')
    elif os.getenv('VERCEL'):
        return routes.send(503, {'error': 'Hosted storage is not configured.'})
    else:
        address = request.scope.get('server')
        if not address or not routes.valid_host(address[1]):
            return routes.send(403, {'error': 'Invalid host'})
        valid_origin = routes.valid_origin(request.url.scheme)

    path = '/' + path
    if request.method == 'GET':
        if database_url:
            return await run_in_threadpool(hosted_request, database_url, routes, path)
        return await run_in_threadpool(routes.get, path)

    if not valid_origin:
        return routes.send(403, {'error': 'Invalid request origin'})
    limit = HOSTED_UPLOAD_LIMIT if database_url else server.MAX_UPLOAD
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            message = 'Hosted uploads must be under 4 MB.' if database_url else 'Upload must be under 12 MB.'
            return routes.send(413, {'error': message})
        body.extend(chunk)
    client_ip = request.client.host if request.client else 'unknown'
    if database_url:
        return await run_in_threadpool(hosted_request, database_url, routes, path, body, client_ip)
    return await run_in_threadpool(routes.post, path, body, client_ip)
