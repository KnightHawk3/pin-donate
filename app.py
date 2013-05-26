import json
import tornado.web
import tornado.ioloop
import logging
import tornado.gen
import decimal
import datetime
import uuid
import pymongo

from decimal import Decimal
from tornado.httpserver import HTTPServer
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.web import StaticFileHandler, asynchronous, HTTPError
from tornado.options import define, options
from urllib.parse import urlencode

define("port", default=8888, help="Port number")
define("config", default="./config.json", help="Config file")
define("mode", default="testing", help="Deployment mode")


class Nonce:
    def __init__(self, **kwargs):
        if kwargs.get('uuid'):
            self.uuid = kwargs['uuid']
            self.expires = kwargs['expires']
        else:
            self.uuid = uuid.uuid4()
            self.expires = datetime.datetime.utcnow() + datetime.timedelta(minutes=kwargs['expires'])

    def has_expired(self):
        return datetime.datetime.utcnow() > self.expires


class NonceManager:
    def __init__(self, collection):
        self.collection = collection

    def generate(self):
        nonce = Nonce(expires=5)
        self.collection.insert({"uuid": nonce.uuid, "expires": nonce.expires}, safe=True)
        return nonce

    def consume(self, id):
        '''Return true if consumed, false is not found or expired'''
        try:
            id = uuid.UUID(id)
        except:
            logging.debug("%s is invalid" % id)
            return False

        data = self.collection.find_one({"uuid": id})
        if data is None:
            logging.debug("%r not found" % id)
            return False

        nonce = Nonce(**data)
        self.collection.remove(data)
        logging.debug("Expired: %s" % nonce.has_expired())
        return not nonce.has_expired()


class ReceiptManager:
    def __init__(self, collection):
        self.collection = collection

    def save(self, response):
        return self.collection.insert(response, safe=True)

class DonateHandler(tornado.web.RequestHandler):
    def get(self):
        self.render('templates/donate.html', **{
            "api_endpoint": self.application.settings['api_endpoint'],
            "publishable_key": self.application.settings['publishable_key'],
            "referer": self.request.headers.get('referer', None),
            "nonce": self.application.settings['nonces'].generate(),
            "mode": options.mode
        })

    @asynchronous
    @tornado.gen.engine
    def post(self):
        nonce = self.get_argument('nonce')
        if self.application.settings['nonces'].consume(nonce) is False:
            self.render("templates/expired.html", **{"mode": options.mode})

        ip_address = self.get_argument('ip_address')
        card_token = self.get_argument('card_token')
        amount = self.get_argument('amount')
        email = self.get_argument('email')

        # Convert dollar amount into cents
        amount = str((Decimal(amount) * Decimal(100))
                   .quantize(Decimal('1.'), rounding=decimal.ROUND_DOWN))

        currency = "AUD"
        description = "Pirate Party Donation"
        key = self.application.settings['secret_key']

        body = {
            "email": email,
            "ip_address": ip_address,
            "description": description,
            "amount": amount,
            "currency": currency,
            "card_token": card_token
        }
        logging.debug(repr(body))

        http_client = AsyncHTTPClient()

        req = HTTPRequest("https://" + self.application.settings['api_endpoint'] + "/1/charges",
                method="POST",
                body=urlencode(body),
                auth_username=key
        )
        response = yield tornado.gen.Task(http_client.fetch, req)
        logging.debug("%r" % response)
        logging.debug("%r" % response.body)

        try:
            data = json.loads(response.body.decode())
            if data.get('response'):
                self.application.settings['receipts'].save(data['response'])
                self.render("templates/receipt.html", **{
                    "token": data['response']['token'],
                    "amount": str(Decimal(data['response']['amount']) / Decimal(100)),
                    "mode": options.mode
                })
            elif data.get('error'):
                if data['error'] == "invalid_resource":
                    self.render("templates/rejected.html", **{"mode": options.mode})
            else:
                raise Exception("Unexpected response body") # force exception
        except Exception as e:
            logging.error(e)
            logging.error("Response body: %r" % response.body)
            logging.error("Raw response: %r" % response)
            logging.error("Email: %s" % email)
            self.render("templates/error.html", **{"mode": options.mode})


if __name__ == "__main__":
    tornado.options.parse_command_line()
    settings = json.load(open(options.config))[options.mode]
    settings['nonces'] = NonceManager(pymongo.Connection().pindonate.nonces)
    settings['receipts'] = ReceiptManager(pymongo.Connection().pindonate.receipts)

    application = tornado.web.Application([
        (r"/static/(.*)", StaticFileHandler, {"path": "static"}),
        (r"/", DonateHandler)
    ], **settings)

    application.listen(options.port, xheaders=True)
    tornado.ioloop.IOLoop.instance().start()
