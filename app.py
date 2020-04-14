import asyncio
from collections import defaultdict
import copy
import datetime
import itertools
import json
import logging

from asgiref.wsgi import WsgiToAsgi
import motor.motor_asyncio
import poker
import pymongo
from pyramid.config import Configurator
from pyramid.response import Response


class ComplexEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return obj.decode()
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
def hello_world(request):
    return Response(HTML_BODY)

# Configure a normal WSGI app then wrap it with WSGI -> ASGI class
with Configurator() as config:
    config.add_route("hello", "/")
    config.add_view(hello_world, route_name="hello")
    config.add_static_view(name='static_assets', path='static')
    wsgi_app = config.make_wsgi_app()

client = motor.motor_asyncio.AsyncIOMotorClient('mongo')
db = client.test_database
app = ExtendedWsgiToAsgi(wsgi_app)

# app code

NO_TABLE = 0
AT_TABLE = 1
DISCONNECTING = 2
watch_list = defaultdict(set)  # (k:table_id, v:[(id, send)])

def set_envelope(msg):
    envelope = {"type": "websocket.send",
                "text": json.dumps(msg, cls=ComplexEncoder)}
    return envelope

def send_command(cmd, send, data=None):
    msg = {"status": "ok", "command": cmd}
    if data is not None:
        msg['data'] = data
    return send(set_envelope(msg))

def send_error(error, send):
    msg = {"status": "error", "error": error}
    return send(set_envelope(msg))

async def watch_collection():
    resume_token = None
    pipeline = [{'$match': {'operationType': {'$in': ['insert', 'update', 'replace']}}}]
    # TODO: debug why this fails if mongo is not up when starting
    try:
        logging.debug('starting watch collection')
        async with db.tables.watch(pipeline) as stream:
            async for change in stream:
                logging.debug('caught change: {}'.format(change))
                logging.debug('watch list: {}'.format(watch_list[change['documentKey']['_id']]))
                try:
                    send_awaitables = []
                    changed_table_id = change['documentKey']['_id']
                    if change['operationType'] == 'update':
                        # get full doc because update syntax is hard to parse
                        original_table = await db.tables.find_one({'_id': changed_table_id})
                    else:
                        original_table = change['fullDocument']
                    for player_id, send in watch_list[changed_table_id]:
                        table = copy.deepcopy(original_table)
                        table.pop('deck', None)
                        # identify user and hide other players' cards
                        if 'players' in table:
                            for player in table['players']:
                                if player['id'] != player_id:
                                    player.pop('cards', None)
                                else:
                                    player['you'] = True
                        logging.debug('change handler running for user: {}, operationType: {}, table: {}'.format(player_id, change['operationType'], table))
                        send_awaitables.append(send_command('table_update',
                                                            send,
                                                            table))
                    await asyncio.wait(send_awaitables)
                    table = poker.Table(original_table)
                    table.check_table()
                    if original_table != table.to_mongo():
                        await db.tables.replace_one(original_table, table.to_mongo())
                except ValueError as e:
                    logging.info('no watch list: {}'.format(e))
                resume_token = stream.resume_token
    except pymongo.errors.PyMongoError:
        # The ChangeStream encountered an unrecoverable error or the
        # resume attempt failed to recreate the cursor.
        if resume_token is None:
            # There is no usable resume token because there was a
            # failure during ChangeStream initialization.
            logging.exception('failure during ChangeStream initialization')
        else:
            # Use the interrupted ChangeStream's resume token to
            # create a new ChangeStream. The new stream will
            # continue from the last seen insert change without
            # missing any events.
            async with db.tables.watch(
                    pipeline, resume_after=resume_token) as stream:
                async for insert_change in stream:
                    logging.debug(insert_change)

asyncio.create_task(watch_collection())

async def command_handler(command, state, send):
    try:
        cmd = json.loads(command)
    except:
        return await send_error('invalid command', send)
    if cmd.get('command') == 'get_random_table':
        # TODO: find tables with fewer seats available first? or more?
        # TODO: exclude private tables
        ret = await db.tables.find_one({'seats_available': {'$gte': 1}})
        # TODO: if no tables returned, create a new one
        return await send_command('found_table', send, ret['_id'])
    elif cmd.get('command') == 'change_name':
        if state['current_state'] == NO_TABLE:
            return await send_error('cannot change name if not at table', send)
        else:
            await db.tables.update_one({'_id': state['table_id'],
                                        'players.id': state['id']},  # for safety
                                       {'$set': {'players.$.name': cmd.get('data')}})
            return await send_command('name_changed', send)
    elif cmd.get('command') == 'start_game':
        if state['current_state'] != AT_TABLE:
            return await send_error('not at table', send)
        table = await db.tables.find_one({'_id': state['table_id'],
                                          'round_state': 'not_started',
                                          'players.id': state['id']})
        # apply poker
        try:
            new_table = poker.Table(table)
            new_table.start_table()
        except poker.PokerException as e:
            return await send_error(str(e), send)
        await db.tables.replace_one(table, new_table.to_mongo())
        return await send_command('game_started', send)
    elif cmd.get('command') == 'join_table':
        # can user join a table?
        if state['current_state'] != NO_TABLE:
            return await send_error('already at a table', send)
        table_id = cmd.get('data', {}).get('table_id')
        if not table_id:
            return await send_error('invalid table id', send)
        player_name = cmd.get('data', {}).get('player_name')
        # lookup table
        table = await db.tables.find_one({'_id': table_id})
        logging.debug(table)
        me = {'id': state['id'], 'stack': 10000, 'seat': 0}
        if player_name:
            me['name'] = player_name
        # if there is no table, create it
        if not table:
            new_table = {'_id': table_id,
                         'seats_available': 9,
                         'players': [me],
                         'dealer': 0,
                         'round_state': 'not_started',
                         'bets': [],
                         'pot': 0,
                         'action_to': 0,
                         'seats_starting_in_round': [],
                         'seats_currently_in_round': []}
            new_table = poker.Table()
            new_table._id = table_id
            # TODO: since we start the watch first, we have to remove it if the
            # table create fails
            watch_list[table_id].add((state['id'], send))
            await db.tables.insert_one(new_table.to_mongo())
            state['current_state'] = AT_TABLE
            state['table_id'] = table_id
            return await send_command('joined_table', send)
        else:
            # join table if seat available and not already at the table
            poker_table = poker.Table(table)
            if not poker_table.seats_available:
                return await send_error('table full', send)
            if state['id'] in [p['id'] for p in poker_table.players]:
                return await send_error('already seated at table', send)
            poker_table.seats_available -= 1
            # TODO: allow choosing seat
            me['seat'] = next(seat for seat in range(0, 9) if seat not in [p['seat'] for p in poker_table.players])
            poker_table.players.append(me)
            # TODO: since we start the watch first, we have to remove it if the
            # table join fails
            watch_list[table_id].add((state['id'], send))
            # atomically join by ensuring the current doc hasn't changed
            ret = await db.tables.replace_one(table, poker_table.to_mongo())
            # TODO: handle fail
            logging.debug('table update returned: {}'.format(ret))
            state['current_state'] = AT_TABLE
            state['table_id'] = table_id
            return await send_command('joined_table', send)
    else:
        return await send_error('unknown command')

async def disconnect_handler(state):
    # remove disconnecting user from any tables they are at (should be only 1)
    await db.tables.update_many({'players.id': state['id']},
                                {'$pull': {'players': {'id': state['id']}},
                                 '$inc': {'seats_available': 1}})

# Define ASGI consumers
@app.route("/ws", protocol="websocket")
async def hello_websocket(scope, receive, send):
    logging.debug("scope: {}".format(scope))
    # no user login for now, use sec-websocket-key as id
    state = {'current_state': NO_TABLE,
             'data': {},
             'id': next(t[1] for t in scope['headers'] if t[0] == b'sec-websocket-key')}
    while True:
        message = await receive()
        if message["type"] == "websocket.connect":
            await send({"type": "websocket.accept"})
        elif message["type"] == "websocket.receive":
            text = message.get("text")
            if text:
                await command_handler(text, state, send)
        elif message["type"] == "websocket.disconnect":
            state['current_state'] = DISCONNECTING
            for watch_list_set in watch_list.values():
                if send in watch_list_set:
                    watch_list_set.remove((state['id'], send))
            await disconnect_handler(state)
            await send({"type": "websocket.close"})  # ?
            break  # ?
        else:
            logging.debug('unhandled websocket message type: {}'.format(message['type']))

@app.route("/pantry", protocol="websocket")
async def pantry_websocket(scope, receive, send):
    def transform_doc(x):
        return (x['name'],
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
                    await send({"type": "websocket.send",
                                "text": json.dumps([transform_doc(x) async
                                                for x in db.pantry.find()])})
                else:
                    item = json.loads(text)
                    item['location'] = item['location'].split(',')
                    item['categories'] = item['categories'].split(',')
                    dt = datetime.datetime.strptime(item['expiration'], '%m/%d/%Y')
                    item['expiration'] = dt
                    await db.pantry.insert_one(item)
                    await send({"type": "websocket.send",
                                "text": json.dumps([transform_doc(x) async
                                                for x in db.pantry.find()])})
        elif message["type"] == "websocket.disconnect":
            await send({"type": "websocket.close"})  # ?
            break  # ?
        else:
            logging.debug('unhandled websocket message type: {}'.format(message['type']))
