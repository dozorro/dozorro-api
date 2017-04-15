import struct
import rethinkdb as r
import rethinkdb.errors
import logging

logger = logging.getLogger(__name__)


class RethinkEngine(object):
    @staticmethod
    async def cleanup(app):
        await app['db'].conn.close()

    async def init_engine(self, app):
        r.set_loop_type('asyncio')
        self.conn = await r.connect(db='sandbox')
        app.on_cleanup.append(self.cleanup)
        app['db'] = self

    async def check_open(self):
        try:
            self.conn.check_open()
        except rethinkdb.errors.ReqlDriverError:
            logger.warning('Reconnect RethinkEngine')
            self.conn = await self.conn.reconnect(False)

    def pack_offset(self, offset):
        if not offset:
            return offset
        return struct.pack('d', offset.timestamp()).hex()

    def unpack_offset(self, offset):
        offset = struct.unpack('d', bytes.fromhex(offset))[0]
        return r.epoch_time(offset)

    async def get_list(self, offset=None, limit=100, reverse=False, table='data'):
        minval, maxval, oindex = r.minval, r.maxval, 'ts'
        if offset:
            offset = self.unpack_offset(offset)
            if reverse:
                maxval = offset
            else:
                minval = offset
        if reverse:
            oindex = r.desc(oindex)
        cursor = await (r.table(table)
            .between(minval, maxval, index='ts', left_bound='open')
            .order_by(index=oindex)
            .limit(limit)
            .pluck('id', 'ts')
            .run(self.conn))
        items_list = list()
        first_ts = None
        last_ts = None
        while await cursor.fetch_next():
            doc = await cursor.next()
            if not doc:
                break
            last_ts = doc.pop('ts')
            if not first_ts:
                first_ts = last_ts
            items_list.append(doc)
        first_ts = self.pack_offset(first_ts)
        last_ts = self.pack_offset(last_ts)
        return (items_list, first_ts, last_ts)


    async def get_item(self, item_id, table='data'):
        doc = await r.table(table).get(item_id).run(self.conn)
        if doc:
            doc.pop('ts')
        return doc


    async def get_many(self, items_list, table='data'):
        if len(items_list) == 1:
            doc = await self.get_item(items_list[0], table)
            return [doc, ] if doc else []
        cursor = await r.table(table).get_all(*items_list).run(self.conn)
        docs = list()
        while await cursor.fetch_next():
            doc = await cursor.next()
            if doc:
                doc.pop('ts')
                docs.append(doc)
        return docs


    async def check_exists(self, item_id, table='data', model=None):
        doc = await r.table(table).get(item_id).run(self.conn)
        assert doc, '{} not found'.format(item_id)
        assert not model or model == doc['envelope']['model'], 'bad model ref'


    async def put_item(self, data, table='data'):
        data['ts'] = r.now()
        status = await r.table(table).insert(data).run(self.conn)
        if status['errors']:
            logger.error(status.get('first_error', 'insert error'))
            raise AssertionError('insert error or already exists')
