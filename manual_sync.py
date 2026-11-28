"""Server-side, admin-only bridge to the existing Dragon Shield Lambda."""
import json
import os
import re


def invoke():
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError, ReadTimeoutError

    arn = os.getenv('DUEL_SYNC_FUNCTION_ARN', '')
    key = os.getenv('DUEL_SYNC_ACCESS_KEY_ID')
    secret = os.getenv('DUEL_SYNC_SECRET_ACCESS_KEY')
    match = re.fullmatch(r'arn:aws:lambda:([a-z0-9-]+):[0-9]{12}:function:[A-Za-z0-9_-]+', arn)
    if not match or not key or not secret:
        return 503, {'error': 'Dragon Shield sync is not configured on this server.'}
    try:
        client = boto3.client('lambda', region_name=match[1],
            aws_access_key_id=key, aws_secret_access_key=secret,
            aws_session_token=os.getenv('DUEL_SYNC_SESSION_TOKEN') or None,
            config=Config(connect_timeout=5, read_timeout=25, retries={'total_max_attempts': 1}))
        response = client.invoke(FunctionName=arn, InvocationType='RequestResponse',
                                 Payload=b'{"mode":"sync"}')
        stream = response['Payload']
        try:
            raw = stream.read(65537)
        finally:
            stream.close()
        if len(raw) > 65536:
            raise ValueError('Unexpected response size')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Unexpected response')
        if response.get('FunctionError'):
            return 409, {'error': 'Dragon Shield sync failed or is paused. Check the sync alert or Lambda logs before retrying.'}
        status = result.get('status')
        if status == 'skipped-overlap':
            return 409, {'error': 'A Dragon Shield sync is already running. Wait for it to finish, then refresh the collection.'}
        if status not in ('imported', 'unchanged-collection', 'unchanged-export'):
            raise ValueError('Unexpected sync status')
        counts = {}
        for name in ('entries', 'copies', 'folders'):
            value = result.get(name)
            if type(value) is not int or value < 0:
                raise ValueError('Unexpected counts')
            counts[name] = value
        return 200, {'status': status, **counts}
    except ReadTimeoutError:
        # Lambda may have committed even though the response was not received.
        return 202, {'status': 'pending', 'message': 'Sync is taking longer than expected and may still finish. Refresh the collection shortly; do not start another sync yet.'}
    except ClientError as error:
        if error.response.get('Error', {}).get('Code') in ('AccessDeniedException', 'ResourceNotFoundException'):
            return 503, {'error': 'Dragon Shield sync permissions or function configuration need attention.'}
        return 502, {'error': 'Could not confirm the sync result. It may have run; refresh the collection and check Lambda logs before retrying.'}
    except (BotoCoreError, ValueError, KeyError, TypeError):
        return 502, {'error': 'Could not confirm the sync result. It may have run; refresh the collection and check Lambda logs before retrying.'}
