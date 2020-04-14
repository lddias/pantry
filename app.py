import asyncio
import datetime
import json
import logging

from asgiref.wsgi import WsgiToAsgi
import motor.motor_asyncio
import pymongo
from bson.objectid import ObjectId
from pyramid.config import Configurator
from pyramid.response import Response


class ComplexEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return obj.decode()
        elif isinstance(obj, ObjectId):
            return str(obj)
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)

class ExtendedWsgiToAsgi(WsgiToAsgi):

    """Extends the WsgiToAsgi wrapper to include an ASGI consumer protocol router"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.protocol_router = {"http": {}, "websocket": {}}

    async def __call__(self, scope, receive, send, **kwargs):
        protocol = scope["type"]
        path = scope["path"]
        try:
            consumer = self.protocol_router[protocol][path]
        except KeyError:
            consumer = None
        if consumer is not None:
            await consumer(scope, receive, send, **kwargs)
            return
        await super().__call__(scope, receive, send, **kwargs)

    def route(self, rule, *args, **kwargs):
        try:
            protocol = kwargs["protocol"]
        except KeyError:
            raise Exception("You must define a protocol type for an ASGI handler")

        def _route(func):
            self.protocol_router[protocol][rule] = func

        return _route

# Define normal WSGI views
# def hello_world(request):
#     return Response(HTML_BODY)

# Configure a normal WSGI app then wrap it with WSGI -> ASGI class
with Configurator() as config:
    config.add_route("hello", "/")
    # config.add_view(hello_world, route_name="hello")
    config.add_static_view(name='static_assets', path='static')
    wsgi_app = config.make_wsgi_app()

client = motor.motor_asyncio.AsyncIOMotorClient('mongo')
db = client.test_database
app = ExtendedWsgiToAsgi(wsgi_app)

# app code

def set_envelope(msg):
    envelope = {"type": "websocket.send",
                "text": json.dumps(msg, cls=ComplexEncoder)}
    return envelope

@app.route("/pantry", protocol="websocket")
async def pantry_websocket(scope, receive, send):
    def transform_doc(x):
        return (x['_id'],
                x['name'],
                x['location'],
                x['categories'],
                x['quantity'],
                x['expiration'].strftime('%m/%d/%Y'))
    # no user login for now, use sec-websocket-key as id
    while True:
        message = await receive()
        if message["type"] == "websocket.connect":
            await send({"type": "websocket.accept"})
        elif message["type"] == "websocket.receive":
            text = message.get("text")
            if text:
                if text == 'request':
                    await send(set_envelope([transform_doc(x) async
                                                for x in db.pantry.find()]))
                else:
                    item = json.loads(text)
                    item['location'] = item['location'].split(',')
                    item['categories'] = item['categories'].split(',')
                    dt = datetime.datetime.strptime(item['expiration'], '%m/%d/%Y')
                    item['expiration'] = dt
                    if not item['_id']:
                        del item['_id']
                        await db.pantry.insert_one(item)
                    else:
                        item['_id'] = ObjectId(item['_id'])
                        await db.pantry.replace_one({'_id': item['_id']}, item)
                    await send(set_envelope([transform_doc(x) async
                                                for x in db.pantry.find()]))
        elif message["type"] == "websocket.disconnect":
            await send({"type": "websocket.close"})  # ?
            break  # ?
        else:
            logging.debug('unhandled websocket message type: {}'.format(message['type']))
