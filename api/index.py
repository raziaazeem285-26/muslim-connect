"""Minimal stdlib-only handler to test the Vercel Python runtime itself."""

def handler(request):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": '{"runtime": "ok", "python": "stdlib-only"}',
    }
